from __future__ import annotations

import re
import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


# ── Enums ─────────────────────────────────────────────────────────────────────

class RiskTier(str, Enum):
    TIER_1_READ = "TIER_1_READ"
    TIER_2_COMPUTE = "TIER_2_COMPUTE"
    TIER_3_MUTATE = "TIER_3_MUTATE"


class Intent(str, Enum):
    FACTOID_LOOKUP = "factoid_lookup"
    REASONING = "reasoning"
    TRANSACTION = "transaction"
    OUT_OF_SCOPE = "out_of_scope"


class Domain(str, Enum):
    POLICY = "policy"
    PRODUCT = "product"
    OPS = "ops"
    GENERAL = "general"


class Complexity(str, Enum):
    SIMPLE = "simple"
    MULTI_HOP = "multi_hop"
    AGGREGATE = "aggregate"


class RouteDecision(str, Enum):
    DIRECT_ANSWER = "DIRECT_ANSWER"
    HYBRID_RAG = "HYBRID_RAG"
    TRANSACTION_EXEC = "TRANSACTION_EXEC"
    REJECT_OOS = "REJECT_OOS"


class LoopState(str, Enum):
    INIT = "INIT"
    TRIAGE = "TRIAGE"
    REASONING = "REASONING"
    TOOL_CALL = "TOOL_CALL"
    OBSERVATION = "OBSERVATION"
    VERIFY = "VERIFY"
    DONE = "DONE"
    HALTED_BREAKER = "HALTED_BREAKER"
    HALTED_MAXSTEP = "HALTED_MAXSTEP"
    HALTED_ERROR = "HALTED_ERROR"


class HITLStatus(str, Enum):
    PENDING = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


# ── RAG schemas ───────────────────────────────────────────────────────────────

class RankedChunk(BaseModel):
    chunk_id: str
    text: str
    bm25_score: float = 0.0
    cosine_score: float = 0.0
    rrf_rank: int = 0
    rerank_score: float = 0.0
    source_doc: str = ""
    page: int = 0


class HybridRetrieveInput(BaseModel):
    tenant_id: str = Field(pattern=r"^[a-z0-9_\-]+$")
    query: str = Field(min_length=3, max_length=512)
    top_k: int = Field(default=4, ge=1, le=10)
    rerank_threshold: float = Field(default=0.42, ge=0.0, le=1.0)


class HybridRetrieveOutput(BaseModel):
    chunks: list[RankedChunk]
    retrieval_ms: float
    cache_hit: bool
    abstained: bool = False
    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()))


# ── State store schemas ───────────────────────────────────────────────────────

class StateWriteInput(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str
    key: str = Field(pattern=r"^[a-zA-Z0-9_:\-\.]+$")
    value: dict[str, Any]
    ttl_seconds: int = Field(default=3600, le=86400)
    tier: RiskTier


# ── Triage schemas ────────────────────────────────────────────────────────────

class TriageResult(BaseModel):
    intent: Intent
    domain: Domain
    risk_tier: RiskTier
    complexity: Complexity
    route: RouteDecision
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    latency_ms: float = 0.0


# ── RAGAS schemas ─────────────────────────────────────────────────────────────

class RAGASScore(BaseModel):
    faithfulness: float = Field(ge=0.0, le=1.0, default=0.0)
    answer_relevancy: float = Field(ge=0.0, le=1.0, default=0.0)
    context_entity_recall: float = Field(ge=0.0, le=1.0, default=0.0)
    passed: bool = False
    veto_reason: str = ""


# ── ReAct loop schemas ────────────────────────────────────────────────────────

class GoalAnchorBlock(BaseModel):
    original_query: str
    normalized_entities: dict[str, str] = {}
    policy_constraints: list[str] = []
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str = ""


class ReActStep(BaseModel):
    step_number: int
    state: LoopState
    thought: str = ""
    tool_name: str = ""
    tool_args: dict[str, Any] = {}
    observation: str = ""
    latency_ms: float = 0.0


class ReActResult(BaseModel):
    answer: str
    steps: list[ReActStep] = []
    final_state: LoopState
    total_latency_ms: float
    ragas: RAGASScore | None = None
    abstained: bool = False
    hitl_pending: bool = False
    hitl_record_id: str = ""


# ── HITL schemas ──────────────────────────────────────────────────────────────

class HITLRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    tenant_id: str
    action: str
    payload: dict[str, Any]
    status: HITLStatus = HITLStatus.PENDING
    reviewer_id: str = ""
    reason: str = ""


# ── API request/response schemas ──────────────────────────────────────────────

class QueryRequest(BaseModel):
    tenant_id: str = Field(pattern=r"^[a-z0-9_\-]+$", default="default")
    user_id: str = Field(default="user_1")
    query: str = Field(min_length=3, max_length=512)
    session_id: str | None = None

    @field_validator("query")
    @classmethod
    def auto_repair_query(cls, v: str) -> str:
        # SKU normalization: SKU99 → SKU-099
        v = re.sub(r"\bSKU(\d+)\b", lambda m: f"SKU-{int(m.group(1)):03d}", v, flags=re.IGNORECASE)
        # LOT normalization: lot 44 → LOT-00044
        v = re.sub(r"\blot\s*(\d+)\b", lambda m: f"LOT-{int(m.group(1)):05d}", v, flags=re.IGNORECASE)
        return v


class QueryResponse(BaseModel):
    answer: str
    session_id: str
    tenant_id: str
    route: str
    final_state: str
    total_latency_ms: float
    ragas: RAGASScore | None = None
    abstained: bool = False
    hitl_pending: bool = False
    hitl_record_id: str = ""
    chunks_used: int = 0
    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()))


class HITLApprovalRequest(BaseModel):
    record_id: str
    reviewer_id: str
    approved: bool
    reason: str = ""


class IngestRequest(BaseModel):
    tenant_id: str = Field(pattern=r"^[a-z0-9_\-]+$")
    documents: list[dict[str, str]]


class HealthResponse(BaseModel):
    status: str
    redis: str
    database: str
    groq: str
    documents_indexed: int


# ── Tool harness schemas ──────────────────────────────────────────────────────

class ToolCall(BaseModel):
    tool_name: str
    args: dict[str, Any]
    idempotency_key: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tier: RiskTier = RiskTier.TIER_1_READ


class ToolResult(BaseModel):
    tool_name: str
    success: bool
    result: Any = None
    error_code: str = ""
    error_message: str = ""
    latency_ms: float = 0.0
