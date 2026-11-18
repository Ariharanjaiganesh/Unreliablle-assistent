"""
FastAPI route definitions — all endpoints for the enterprise agentic system.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from cache.redis_cache import cache_get, cache_set, redis_health
from db.database import (
    db_health, get_hitl_record, list_pending_hitl, update_hitl_status, append_audit
)
from models.schemas import (
    HITLApprovalRequest, HITLStatus, HealthResponse,
    IngestRequest, QueryRequest, QueryResponse,
)
from observability.otel import span
from plugins.registry import run_pre_execution_pipeline
from loop.react_loop import run as react_run
from rag.bm25_retriever import get_bm25_index
from rag.dense_retriever import get_dense_index

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Health ────────────────────────────────────────────────────────────────────

@router.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check():
    bm25 = get_bm25_index()
    return HealthResponse(
        status="ok",
        redis=await redis_health(),
        database=await db_health(),
        groq="configured (key not validated)",
        documents_indexed=bm25.doc_count,
    )


# ── Document Ingestion ────────────────────────────────────────────────────────

@router.post("/ingest", tags=["Knowledge"])
async def ingest_documents(req: IngestRequest):
    """
    Ingest documents into BM25 + Dense indexes.
    Each document: {"chunk_id": str, "text": str, "source_doc": str, "page": int}
    """
    bm25 = get_bm25_index()
    dense = get_dense_index()
    bm25.build(req.documents)
    dense.build(req.documents)
    logger.info("Ingested %d chunks for tenant=%s", len(req.documents), req.tenant_id)
    return {"status": "ok", "chunks_indexed": len(req.documents)}


# ── Main Query Endpoint ───────────────────────────────────────────────────────

@router.post("/agent/query", response_model=QueryResponse, tags=["Agent"])
async def agent_query(req: QueryRequest):
    session_id = req.session_id or str(uuid.uuid4())

    with span("hook.pre", {"tenant_id": req.tenant_id}):
        pre = await run_pre_execution_pipeline(req.query, req.tenant_id)

    if pre.blocked:
        raise HTTPException(status_code=400, detail=pre.block_reason)

    # Redis cache check
    cached = await cache_get(req.tenant_id, req.user_id, pre.query)
    if cached:
        logger.info("Cache HIT for tenant=%s", req.tenant_id)
        return QueryResponse(**{
            **cached,
            "session_id": session_id,
            "tenant_id": req.tenant_id,
        })

    # ReAct loop
    result = await react_run(
        query=pre.query,
        tenant_id=req.tenant_id,
        user_id=req.user_id,
        session_id=session_id,
    )

    response = QueryResponse(
        answer=result.answer,
        session_id=session_id,
        tenant_id=req.tenant_id,
        route=result.steps[1].thought if len(result.steps) > 1 else "DIRECT",
        final_state=result.final_state.value,
        total_latency_ms=result.total_latency_ms,
        ragas=result.ragas,
        abstained=result.abstained,
        hitl_pending=result.hitl_pending,
        hitl_record_id=result.hitl_record_id,
        chunks_used=sum(1 for s in result.steps if s.tool_name == "hybrid_retrieve"),
    )

    # Cache write-back (skip if HITL pending, abstained, or no context)
    if not result.hitl_pending and not result.abstained and result.ragas and result.ragas.passed:
        await cache_set(req.tenant_id, req.user_id, pre.query, response.model_dump(mode="json"))

    return response


# ── HITL Management ───────────────────────────────────────────────────────────

@router.get("/hitl/pending", tags=["HITL"])
async def list_pending(tenant_id: str):
    records = await list_pending_hitl(tenant_id)
    return {"records": [r.model_dump() for r in records]}


@router.post("/hitl/approve", tags=["HITL"])
async def approve_hitl(req: HITLApprovalRequest):
    record = await get_hitl_record(req.record_id)
    if not record:
        raise HTTPException(status_code=404, detail="HITL record not found")

    new_status = HITLStatus.APPROVED if req.approved else HITLStatus.REJECTED
    updated = await update_hitl_status(req.record_id, new_status, req.reviewer_id, req.reason)

    if updated:
        await append_audit(
            "HITL_DECISION", record.tenant_id, record.session_id,
            {"record_id": req.record_id, "decision": new_status.value, "reviewer": req.reviewer_id},
        )

    return {"status": "ok", "record_id": req.record_id, "new_status": new_status.value}


@router.get("/hitl/{record_id}", tags=["HITL"])
async def get_hitl(record_id: str):
    record = await get_hitl_record(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="HITL record not found")
    return record.model_dump()


# ── KPI / Metrics ─────────────────────────────────────────────────────────────

@router.get("/metrics/kpi", tags=["Observability"])
async def kpi_snapshot():
    """Returns a snapshot of measurable KPIs from live system state."""
    bm25 = get_bm25_index()
    redis_ok = await redis_health() == "ok"
    return {
        "kpi_01_redis_available": redis_ok,
        "documents_indexed": bm25.doc_count,
        "kpi_target_p95_ms": 450,
        "kpi_target_rps": 8500,
        "note": "Connect Grafana + OTel collector for full 36-KPI dashboard",
    }
