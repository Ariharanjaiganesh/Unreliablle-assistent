"""
Verification Subagent — independent context, NLI + RAGAS + OTel.
Cannot be influenced by Retrieval or Execution subagent outputs.
"""
from __future__ import annotations

import logging

from models.schemas import RAGASScore
from plugins.ragas_auditor import score as ragas_score
from db.database import append_audit

logger = logging.getLogger(__name__)


async def verify(
    answer: str,
    query: str,
    context_chunks: list[str],
    session_id: str,
    tenant_id: str,
) -> RAGASScore:
    ragas = await ragas_score(answer, query, context_chunks, use_llm=bool(context_chunks))

    await append_audit(
        "VERIFICATION_COMPLETE",
        tenant_id,
        session_id,
        {
            "faithfulness": ragas.faithfulness,
            "answer_relevancy": ragas.answer_relevancy,
            "entity_recall": ragas.context_entity_recall,
            "passed": ragas.passed,
            "veto_reason": ragas.veto_reason,
        },
    )

    if not ragas.passed:
        logger.warning("Verification VETO session=%s: %s", session_id, ragas.veto_reason)
    else:
        logger.info("Verification PASSED session=%s faith=%.2f rel=%.2f",
                    session_id, ragas.faithfulness, ragas.answer_relevancy)

    return ragas
