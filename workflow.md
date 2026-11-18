# Workflow Orchestration — "The Unreliable Assistant" Enterprise Architecture

> Gate 2 Pre-Build Artifact: This document describes the full end-to-end request lifecycle. A high-DPI diagram must also be generated at `docs/workflow_orchestration.png`.

---

## Overview

Every user request passes through six sequential layers before a response is returned. Each layer is independently instrumented with OpenTelemetry W3C TraceContext spans so the full critical path is visible in Grafana Tempo.

```
Ingress → Security Plugins → Cache → Triage → RAG/Execution → Control Plane → Egress
```

---

## Layer 1: Ingress & Security

### 1.1 API Gateway
- TLS termination via NGINX or Envoy
- Rate limiting per `tenant_id`
- Injects `X-Tenant-ID` and `X-Request-ID` headers
- Forwards to FastAPI async workers (Python 3.11 + Uvicorn)

### 1.2 Pre-Execution Plugin Chain (Pluggy hooks, fail-closed)

```
Request
   │
   ├─► [Priority 10] PII Redactor
   │     - Presidio NER + regex patterns
   │     - Catches: email, phone, SSN, obfuscated (name[at]domain)
   │     - On failure: reject with PII_001 (fail_closed=true)
   │
   └─► [Priority 20] Injection Shield
         - Classifies direct jailbreaks, indirect injections in query text
         - Unicode homoglyph detection
         - On detection: reject with 403 + log adversarial event
```

**OTel span:** `hook.pre` — budget ≤ 15 ms p95 (KPI-01)

### 1.3 Redis Semantic Cache Lookup

```
Normalize query (lowercase, strip punctuation, canonical entity IDs)
   │
   ▼
Key = SHA256(tenant_id + ":" + user_id + ":" + normalized_query)
   │
   ├─ HIT (cosine similarity ≥ 0.92) ──► Return cached response immediately
   │                                       OTel span: cache.lookup (HIT)
   │
   └─ MISS ──────────────────────────────► Continue to Triage
                                           OTel span: cache.lookup (MISS)
```

TTL: 300s ± 45s jitter (prevents cache stampede on mass eviction)

---

## Layer 2: Triage Agent

**Parameters:** `temperature=0.0`, distilled classifier, ≤ 25 ms p95 (KPI-05)

The Triage Agent tags every query with four dimensions and routes to exactly one downstream handler:

```
Query
   │
   ▼
Triage Agent classifies:
  intent    = {factoid_lookup | reasoning | transaction | out_of_scope}
  domain    = {policy | product | ops | general}
  risk_tier = {TIER_1_READ | TIER_2_COMPUTE | TIER_3_MUTATE}
  complexity = {simple | multi_hop | aggregate}
   │
   ├─ intent=factoid_lookup + complexity=simple ──────► DIRECT_ANSWER
   │                                                    (skip RAG, use cache or LLM direct)
   │
   ├─ intent=factoid_lookup|reasoning + domain=policy ► HYBRID_RAG
   │                                                    → Retrieval Subagent
   │
   ├─ intent=transaction ─────────────────────────────► TRANSACTION_EXEC
   │                                                    → Execution Subagent
   │
   └─ intent=out_of_scope ────────────────────────────► REJECT_OOS
                                                        (deterministic rejection, no LLM call)
```

**OTel span:** `triage.classify`

---

## Layer 3: Subagent Routing

Three specialized subagents execute in isolation with strict capability boundaries.

### 3.1 Retrieval Subagent

**Bound MCP servers:** `mcp-hybrid-rag`, `mcp-graphify-vault`

**Guardrail:** Read-only. Cannot invoke any state-mutating tools.

Responsibilities:
1. Query rewriting (entity normalization, synonym expansion)
2. 3-Stage Hybrid RAG execution (see Layer 4)
3. Citation grounding — every claim mapped to a source chunk

### 3.2 Execution Subagent

**Bound MCP servers:** `mcp-state-store` only

**Guardrail:** No RAG access. No external web access. TIER_3 operations trigger HITL freeze before any write.

Responsibilities:
1. Pydantic v2 strict schema validation of tool arguments
2. Deterministic regex auto-repair (SKU/LOT normalization)
3. RBAC tier check before tool dispatch
4. UUIDv4 idempotency enforcement on state writes

### 3.3 Verification Subagent

**Bound MCP servers:** `mcp-state-store` (audit log write only)

**Guardrail:** Independent model context — cannot be influenced by Retrieval or Execution subagent outputs.

Responsibilities:
1. NLI claim-to-chunk verification (veto ungrounded claims)
2. RAGAS triad scoring (Faithfulness, Answer Relevancy, Context Entity Recall)
3. OTel span emission for all verification events

---

## Layer 4: 3-Stage Hybrid RAG Pipeline

Executed by the Retrieval Subagent via MCP JSON-RPC call to `mcp-hybrid-rag`.

```
Rewritten Query
      │
      ▼
┌─────────────────────────────────────────────────────────────┐
│  Stage 1: BM25 Sparse Retrieval                             │
│  BM25Okapi + PorterStemmer, k1=1.25, b=0.75, top_k=50      │
│  Pre-tokenized inverted index                               │
│  Output: 50 lexical candidates + raw BM25 scores            │
│  OTel: rag.bm25 ── budget < 12 ms                          │
└──────────────────────────┬──────────────────────────────────┘
                           │
      ┌────────────────────┘ (parallel with Stage 2)
      │
┌─────────────────────────────────────────────────────────────┐
│  Stage 2: Dense Bi-Encoder + FAISS HNSW                     │
│  bge-large-en-v1.5 (ONNX quantized)                        │
│  chunk=512 tok, overlap=64 tok, efSearch=64, top_k=50       │
│  Output: 50 semantic candidates + cosine similarity         │
│  OTel: rag.dense ── budget < 25 ms                         │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Rank Fusion: Reciprocal Rank Fusion (RRF)                  │
│  Smoothing constant k=60                                    │
│  Dedup by chunk_hash, unified pool=40                       │
│  Output: Top 40 scale-normalized candidates                 │
│  OTel: rag.rrf ── budget < 3 ms                            │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Stage 3: Cross-Encoder Reranking                           │
│  bge-reranker-v2-m3 (ONNX FP16, batch=32)                  │
│  rerank_k=4, sigmoid threshold > 0.42, max_len=512          │
│                                                             │
│  IF top score < 0.42:                                       │
│    → Deterministic Abstention (< 90 ms, zero LLM call)     │
│  ELSE:                                                      │
│    → Top 4 verified context chunks + confidence scores      │
│                                                             │
│  OTel: rag.rerank ── budget < 45 ms                        │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
            Graphify 2-Hop Subgraph Pruning
            mcp-graphify-vault resolves query anchor node
            Traverses ≤ depth=2 [[wikilinks]] edges
            Injects ~320 tokens (vs ~1,400 tokens raw)
            Token savings ≥ 45% (KPI-26)
```

---

## Layer 5: Control Plane — Bounded ReAct Loop

```
                    ┌──────────────────────────────────────┐
                    │  Immutable GoalAnchorBlock (index 0)  │
                    │  - Original user intent               │
                    │  - Normalized entity IDs              │
                    │  - Active policy constraints          │
                    └──────────────────────────────────────┘
                                      │
                              ┌───────▼───────┐
                     ┌────────│     INIT      │
                     │        └───────┬───────┘
                     │                │
                     │        ┌───────▼───────┐
                     │        │    TRIAGE     │
                     │        └───────┬───────┘
                     │                │
                     │        ┌───────▼───────┐
                     │   ┌────│   REASONING   │◄──────────┐
                     │   │    └───────┬───────┘           │
                     │   │            │                    │
                     │   │    ┌───────▼───────┐           │
                     │   │    │  TOOL_CALL    │           │
                     │   │    │               │           │
                     │   │    │ ToolExecutionHarness:     │
                     │   │    │ 1. Pydantic v2 strict     │
                     │   │    │ 2. Regex auto-repair      │
                     │   │    │ 3. RBAC tier check        │
                     │   │    │ 4. UUIDv4 idempotency     │
                     │   │    └───────┬───────┘           │
                     │   │            │                    │
                     │   │    ┌───────▼───────┐           │
                     │   │    │  OBSERVATION  │           │
                     │   │    │ Compress ≤180 tok         │
                     │   │    │ Strip boilerplate JSON    │
                     │   │    └───────┬───────┘           │
                     │   │            │                    │
                     │   │    ┌───────▼───────┐           │
                     │   │    │    VERIFY     │           │
                     │   │    └───┬───────┬───┘           │
                     │   │        │       │                │
                     │   │   Done?│   More steps?─────────┘
                     │   │        │   (iterations < 5)
                     │   │        │
                     │   │   ┌────▼────┐
                     │   │   │  DONE   │
                     │   │   └─────────┘
                     │   │
                     │   │  Circuit Breaker trips if:
                     │   │  (tool_name, canonical_args_hash) fails ≥ 2×
                     │   │            │
                     │   └──────► HALTED_BREAKER
                     │           → Structured user remediation prompt
                     │
                     │  max_iterations=5 exceeded:
                     └──────────► HALTED_MAXSTEP
                                 → Escalate to HITL or return partial result
```

**Hard limits:**
- `max_iterations = 5`
- `per_step_timeout = 120 ms`
- `total_loop_budget = 450 ms`
- Zero recursive self-calls without state mutation

**Memory model:**
- Hot memory: Last 3 ReAct turns in Redis/RAM
- Cold memory: Verified facts in PostgreSQL `session_facts` table (keyed by `session_id`)

---

## Layer 6: HITL Gate & Egress

### HITL Asynchronous Gate (Tier 3 Operations)

```
Execution Subagent detects risk_tier = TIER_3_MUTATE
         │
         ▼
Write PENDING_APPROVAL record to PostgreSQL
         │
         ▼
Pause execution — return 202 Accepted to client
         │
Authorized reviewer acts via UI/API:
   ├─ APPROVE ──► Resume execution, complete mutation, 200 OK
   └─ REJECT  ──► Cancel operation, 403 Forbidden + reason
```

### Post-Execution Plugin Chain

```
Response Draft
      │
      ├─► [Priority 10] RAGAS Auditor
      │     - Scores: Faithfulness, Answer Relevancy, Context Entity Recall
      │     - If Faithfulness < 0.92: veto response, escalate to HITL
      │     - OTel span: hook.post.ragas
      │
      └─► OTel Exporter
            - Exports all spans to OpenTelemetry Collector
            - Collector fans out to: Prometheus (metrics) + Tempo (traces)
            - Grafana dashboards: latency waterfall, RAGAS gauges, 36-KPI scorecard
```

### Cache Write-Back

```
Final Response
      │
      ▼
IF response passed RAGAS gate AND not PII-sensitive:
  Write to Redis with key SHA256(tenant_id:user_id:norm_query)
  TTL = 300s ± 45s jitter
      │
      ▼
Return response to client
```

---

## Observability: Full OTel Span Map

Every hop emits a named span with W3C `traceparent` propagated end-to-end:

| Span Name | Layer | Description |
|---|---|---|
| `hook.pre` | Layer 1 | PII redaction + injection shield |
| `cache.lookup` | Layer 1 | Redis cache hit/miss |
| `triage.classify` | Layer 2 | Intent/domain/tier/complexity tags |
| `mcp.rag.bm25` | Layer 4 | BM25 sparse retrieval |
| `mcp.rag.biencoder` | Layer 4 | Dense bi-encoder + FAISS |
| `mcp.rag.rrf` | Layer 4 | Reciprocal Rank Fusion |
| `mcp.rag.rerank` | Layer 4 | Cross-encoder reranking |
| `mcp.graphify` | Layer 4 | Subgraph pruning + token compression |
| `agent.react.step` | Layer 5 | Each ReAct loop iteration |
| `llm.generate` | Layer 5 | LLM generation (TTFT measured here) |
| `hook.post.ragas` | Layer 6 | RAGAS scoring |
| `hitl.gate` | Layer 6 | HITL freeze/approval event |

---

## Load Testing: 50,000 VU Traffic Ramp

```
VUs
50K ┤                     ████████████████████
    │                   ██                    ██
10K ┤               ████                        ████
    │           ████                                ████
  0 ┤───────────┴────────────────────────────────────┴──► Time
    0m         2m          5m                   10m  12m

Phase 1: Ramp 0 → 10K VUs over 2 min
Phase 2: Ramp 10K → 50K VUs over 3 min
Phase 3: Sustain 50K VUs for 5 min (peak load)
Phase 4: Ramp down over 2 min
```

**SLA thresholds enforced by k6:**
- `p(50) < 250 ms`
- `p(95) < 450 ms`
- `p(99) < 600 ms`
- `http_req_failed < 0.05%`
- Zero cross-tenant cache leaks verified per-VU in `check()` assertions

---

## Incident Response Runbook

| Incident | Alert Trigger | Automated Mitigation |
|---|---|---|
| Latency breach | p95 e2e > 550 ms for 30s | Enable aggressive semantic cache (cosine ≥ 0.92); drop BM25/Dense top_k from 50 to 25 |
| Hallucination surge | RAGAS Faithfulness < 0.90 (5 min window) | Raise Cross-Encoder cutoff from 0.42 to 0.55; enforce strict extractive quote mode |
| MCP server hang | JSON-RPC timeout > 150 ms on > 1% of requests | Trip MCP bulkhead circuit; failover to cached read-replica index; restart MCP worker |
| Cache stampede / leak | Tenant key collision or Redis CPU > 85% | Enforce SHA256 prefix + jittered TTL; flush suspect key range; alert on-call |
| Infinite loop detected | Circuit breaker trips on second identical call | Transition to HALTED_BREAKER; return structured remediation; log to chaos audit |
| RAGAS gate veto | Faithfulness < 0.92 on generated response | Block response; escalate to HITL queue; Verification Subagent adds veto log entry |
