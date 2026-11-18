"""
Retrieval Subagent — read-only, bound to mcp-hybrid-rag + mcp-graphify-vault.
Runs the 3-stage Hybrid RAG pipeline and returns grounded context chunks.
"""
from __future__ import annotations

import logging
import time

from models.schemas import HybridRetrieveInput, HybridRetrieveOutput, RankedChunk
from rag.bm25_retriever import get_bm25_index
from rag.dense_retriever import get_dense_index
from rag.rrf_fusion import rrf_fuse
from rag.reranker import rerank
from observability.otel import span

logger = logging.getLogger(__name__)


async def retrieve(request: HybridRetrieveInput) -> HybridRetrieveOutput:
    t_total = time.monotonic()

    # Stage 1: BM25
    with span("mcp.rag.bm25", {"tenant_id": request.tenant_id, "query": request.query[:80]}):
        bm25_results, bm25_ms = get_bm25_index().retrieve(request.query, top_k=50)
        logger.debug("BM25: %d candidates in %.1f ms", len(bm25_results), bm25_ms)

    # Stage 2: Dense
    with span("mcp.rag.biencoder", {"query": request.query[:80]}):
        dense_results, dense_ms = get_dense_index().retrieve(request.query, top_k=50)
        logger.debug("Dense: %d candidates in %.1f ms", len(dense_results), dense_ms)

    # RRF Fusion
    with span("mcp.rag.rrf"):
        fused, rrf_ms = rrf_fuse(bm25_results, dense_results, k=60, pool_size=40)
        logger.debug("RRF: %d fused candidates in %.1f ms", len(fused), rrf_ms)

    # Stage 3: Cross-encoder rerank
    with span("mcp.rag.rerank"):
        reranked, rerank_ms, abstained = rerank(
            request.query, fused,
            top_k=request.top_k,
            threshold=request.rerank_threshold,
        )
        logger.debug("Rerank: %d chunks, abstained=%s in %.1f ms", len(reranked), abstained, rerank_ms)

    total_ms = (time.monotonic() - t_total) * 1000

    return HybridRetrieveOutput(
        chunks=reranked,
        retrieval_ms=total_ms,
        cache_hit=False,
        abstained=abstained,
    )
