"""
Stage 2: Dense Semantic Retrieval via TF-IDF + cosine similarity.

Architecture matches what a real FAISS HNSW + BGE bi-encoder would expose —
swap TfidfVectorizer for a SentenceTransformer + faiss.IndexHNSWFlat when
GPU/ONNX resources are available.

Budget: < 25 ms p95 (TF-IDF is faster than bi-encoder; real bi-encoder needs ONNX).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from models.schemas import RankedChunk


@dataclass
class DenseIndex:
    chunks: list[dict] = field(default_factory=list)
    _vectorizer: TfidfVectorizer | None = field(default=None, repr=False)
    _matrix: np.ndarray | None = field(default=None, repr=False)

    def build(self, documents: list[dict]) -> None:
        self.chunks = documents
        texts = [doc["text"] for doc in documents]
        self._vectorizer = TfidfVectorizer(
            ngram_range=(1, 2),
            max_features=50_000,
            sublinear_tf=True,
            strip_accents="unicode",
        )
        self._matrix = self._vectorizer.fit_transform(texts)

    def retrieve(self, query: str, top_k: int = 50) -> tuple[list[RankedChunk], float]:
        t0 = time.monotonic()
        if self._vectorizer is None or self._matrix is None or not self.chunks:
            return [], (time.monotonic() - t0) * 1000

        q_vec = self._vectorizer.transform([query])
        sims = cosine_similarity(q_vec, self._matrix)[0]
        top_indices = np.argsort(sims)[::-1][:top_k]

        results: list[RankedChunk] = []
        for idx in top_indices:
            score = float(sims[idx])
            if score <= 0.0:
                continue
            doc = self.chunks[idx]
            results.append(
                RankedChunk(
                    chunk_id=doc.get("chunk_id", str(idx)),
                    text=doc["text"],
                    cosine_score=score,
                    source_doc=doc.get("source_doc", ""),
                    page=doc.get("page", 0),
                )
            )

        latency_ms = (time.monotonic() - t0) * 1000
        return results, latency_ms

    @property
    def doc_count(self) -> int:
        return len(self.chunks)


_global_dense = DenseIndex()


def get_dense_index() -> DenseIndex:
    return _global_dense
