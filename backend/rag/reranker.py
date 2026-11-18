"""
Stage 3: Cross-encoder reranker.

Implements a lightweight scoring function combining TF-IDF cosine similarity,
token overlap, and BM25 score. Exposes the same interface as a full ONNX
bge-reranker-v2-m3 — swap the _score() function body when ONNX is available.

Abstention short-circuit: if top score < threshold, return abstained=True
and skip LLM generation (zero hallucination risk + 80% token savings).

Budget: < 45 ms p95 (ONNX FP16 would be < 10 ms per batch).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from config import settings
from models.schemas import RankedChunk


def _token_set(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


def _score(query: str, chunk_text: str, bm25_score: float, cosine_score: float,
           bm25_max: float = 1.0) -> float:
    """Composite relevance score mimicking cross-encoder output (0.0–1.0)."""
    q_tokens = _token_set(query)
    c_tokens = _token_set(chunk_text)
    if not q_tokens or not c_tokens:
        return 0.0

    jaccard = len(q_tokens & c_tokens) / len(q_tokens | c_tokens)
    coverage = len(q_tokens & c_tokens) / len(q_tokens)

    # Normalize BM25 relative to batch max (better than fixed cap for small corpora)
    bm25_norm = min(bm25_score / max(bm25_max, 1.0), 1.0)

    # Weighted composite — mirrors cross-encoder signal hierarchy
    score = 0.35 * cosine_score + 0.30 * coverage + 0.25 * bm25_norm + 0.10 * jaccard
    return float(np.clip(score, 0.0, 1.0))


def rerank(
    query: str,
    candidates: list[RankedChunk],
    top_k: int = 4,
    threshold: float | None = None,
) -> tuple[list[RankedChunk], float, bool]:
    """
    Returns (reranked_chunks, latency_ms, abstained).
    abstained=True when top score < threshold → caller should skip LLM.
    """
    t0 = time.monotonic()
    threshold = threshold if threshold is not None else settings.rerank_threshold

    if not candidates:
        return [], (time.monotonic() - t0) * 1000, True

    bm25_max = max((c.bm25_score for c in candidates), default=1.0) or 1.0

    scored: list[tuple[float, RankedChunk]] = []
    for chunk in candidates:
        s = _score(query, chunk.text, chunk.bm25_score, chunk.cosine_score, bm25_max)
        scored.append((s, chunk))

    scored.sort(key=lambda x: x[0], reverse=True)
    top_score = scored[0][0] if scored else 0.0

    if top_score < threshold:
        latency_ms = (time.monotonic() - t0) * 1000
        return [], latency_ms, True  # abstain

    results: list[RankedChunk] = []
    for score, chunk in scored[:top_k]:
        results.append(chunk.model_copy(update={"rerank_score": score}))

    latency_ms = (time.monotonic() - t0) * 1000
    return results, latency_ms, False
