"""
RAGAS Auditor plugin — POST_EXECUTION, priority=10.
Scores: Faithfulness, Answer Relevancy, Context Entity Recall.
Uses Groq LLM for faithfulness; lightweight heuristics for other metrics.
Vetos response if Faithfulness < min_faithfulness.
"""
from __future__ import annotations

import re

from config import settings
from models.schemas import RAGASScore


def _entity_recall(answer: str, context_chunks: list[str]) -> float:
    """Fraction of named entities in context that appear in the answer."""
    entity_pattern = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b|\b[A-Z]{2,}\b|\bLOT-\d+\b|\bSKU-\d+\b")

    context_text = " ".join(context_chunks)
    context_entities = set(entity_pattern.findall(context_text))
    answer_entities = set(entity_pattern.findall(answer))

    if not context_entities:
        return 1.0
    return len(context_entities & answer_entities) / len(context_entities)


def _answer_relevancy(answer: str, query: str) -> float:
    """Rough token overlap between answer and query as relevancy proxy."""
    q_tokens = set(re.findall(r"\w+", query.lower()))
    a_tokens = set(re.findall(r"\w+", answer.lower()))
    if not q_tokens:
        return 1.0
    overlap = len(q_tokens & a_tokens) / len(q_tokens)
    # Reward longer, substantive answers
    length_bonus = min(len(answer) / 500, 0.2)
    return min(overlap + length_bonus, 1.0)


async def score(
    answer: str,
    query: str,
    context_chunks: list[str],
    use_llm: bool = True,
) -> RAGASScore:
    from llm.groq_client import verify_faithfulness

    if use_llm and context_chunks:
        faithfulness = await verify_faithfulness(answer, context_chunks)
    elif context_chunks:
        faithfulness = _entity_recall(answer, context_chunks)  # heuristic fallback
    else:
        faithfulness = 0.5

    relevancy = _answer_relevancy(answer, query)
    entity_recall = _entity_recall(answer, context_chunks)

    passed = (
        faithfulness >= settings.ragas_faithfulness_min
        and relevancy >= settings.ragas_relevancy_min
        and entity_recall >= settings.ragas_entity_recall_min
    )

    veto_reason = ""
    if not passed:
        reasons = []
        if faithfulness < settings.ragas_faithfulness_min:
            reasons.append(f"faithfulness={faithfulness:.2f} < {settings.ragas_faithfulness_min}")
        if relevancy < settings.ragas_relevancy_min:
            reasons.append(f"relevancy={relevancy:.2f} < {settings.ragas_relevancy_min}")
        if entity_recall < settings.ragas_entity_recall_min:
            reasons.append(f"entity_recall={entity_recall:.2f} < {settings.ragas_entity_recall_min}")
        veto_reason = "; ".join(reasons)

    return RAGASScore(
        faithfulness=round(faithfulness, 3),
        answer_relevancy=round(relevancy, 3),
        context_entity_recall=round(entity_recall, 3),
        passed=passed,
        veto_reason=veto_reason,
    )
