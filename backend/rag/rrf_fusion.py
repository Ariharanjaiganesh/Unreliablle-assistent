"""
Reciprocal Rank Fusion — merges BM25 and dense candidate lists.
k=60, dedup by chunk_id, output pool=40.
Budget: < 3 ms p95.
"""
from __future__ import annotations

import time

from models.schemas import RankedChunk


def rrf_fuse(
    bm25_results: list[RankedChunk],
    dense_results: list[RankedChunk],
    k: int = 60,
    pool_size: int = 40,
) -> tuple[list[RankedChunk], float]:
    t0 = time.monotonic()

    scores: dict[str, float] = {}
    chunks_by_id: dict[str, RankedChunk] = {}

    for rank, chunk in enumerate(bm25_results):
        scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1.0 / (k + rank + 1)
        chunks_by_id[chunk.chunk_id] = chunk

    for rank, chunk in enumerate(dense_results):
        scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1.0 / (k + rank + 1)
        if chunk.chunk_id not in chunks_by_id:
            chunks_by_id[chunk.chunk_id] = chunk
        else:
            # merge scores
            existing = chunks_by_id[chunk.chunk_id]
            chunks_by_id[chunk.chunk_id] = existing.model_copy(
                update={"cosine_score": max(existing.cosine_score, chunk.cosine_score)}
            )

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:pool_size]

    results: list[RankedChunk] = []
    for rrf_rank, (chunk_id, _rrf_score) in enumerate(ranked):
        chunk = chunks_by_id[chunk_id]
        results.append(chunk.model_copy(update={"rrf_rank": rrf_rank + 1}))

    latency_ms = (time.monotonic() - t0) * 1000
    return results, latency_ms
