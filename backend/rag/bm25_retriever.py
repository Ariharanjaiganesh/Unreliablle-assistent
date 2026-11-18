"""
Stage 1: BM25 Sparse Retrieval — k1=1.25, b=0.75, top_k=50.
Budget: < 12 ms p95.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from rank_bm25 import BM25Okapi

from models.schemas import RankedChunk


def _tokenize(text: str) -> list[str]:
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return text.split()


@dataclass
class BM25Index:
    chunks: list[dict] = field(default_factory=list)
    _bm25: BM25Okapi | None = field(default=None, repr=False)

    def build(self, documents: list[dict]) -> None:
        """Build index from list of {chunk_id, text, source_doc, page}."""
        self.chunks = documents
        tokenized = [_tokenize(doc["text"]) for doc in documents]
        self._bm25 = BM25Okapi(tokenized, k1=1.25, b=0.75)

    def retrieve(self, query: str, top_k: int = 50) -> tuple[list[RankedChunk], float]:
        t0 = time.monotonic()
        if self._bm25 is None or not self.chunks:
            return [], (time.monotonic() - t0) * 1000

        tokens = _tokenize(query)
        scores = self._bm25.get_scores(tokens)
        scored = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:top_k]

        results: list[RankedChunk] = []
        for idx, score in scored:
            if score <= 0:
                continue
            doc = self.chunks[idx]
            results.append(
                RankedChunk(
                    chunk_id=doc.get("chunk_id", str(idx)),
                    text=doc["text"],
                    bm25_score=float(score),
                    source_doc=doc.get("source_doc", ""),
                    page=doc.get("page", 0),
                )
            )

        latency_ms = (time.monotonic() - t0) * 1000
        return results, latency_ms

    @property
    def doc_count(self) -> int:
        return len(self.chunks)


# Global index (one per process; in production, one per tenant shard)
_global_index = BM25Index()


def get_bm25_index() -> BM25Index:
    return _global_index
