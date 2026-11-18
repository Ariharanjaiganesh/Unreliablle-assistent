"""
Plugin registry — Pluggy-style lifecycle hooks.
Stages: PRE_EXECUTION → TOOL_DISPATCH → POST_EXECUTION
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class PreExecutionResult:
    query: str
    blocked: bool = False
    block_reason: str = ""


@dataclass
class PostExecutionResult:
    answer: str
    ragas_passed: bool = True
    veto_reason: str = ""


async def run_pre_execution_pipeline(query: str, tenant_id: str) -> PreExecutionResult:
    """PII Redactor → Injection Shield (fail-closed)."""
    from plugins.pii_redactor import run_pre_execution as pii_check
    from plugins.injection_shield import run_pre_execution as injection_check

    try:
        query = pii_check(query, fail_closed=True)
    except ValueError as e:
        logger.warning("PII_001 blocked query for tenant=%s: %s", tenant_id, e)
        return PreExecutionResult(query=query, blocked=True, block_reason=str(e))

    try:
        query = injection_check(query)
    except ValueError as e:
        logger.warning("INJECT_001 blocked query for tenant=%s: %s", tenant_id, e)
        return PreExecutionResult(query=query, blocked=True, block_reason=str(e))

    return PreExecutionResult(query=query)


async def run_post_execution_pipeline(
    answer: str,
    query: str,
    context_chunks: list[str],
) -> PostExecutionResult:
    """RAGAS Auditor."""
    from plugins.ragas_auditor import score

    ragas = await score(answer, query, context_chunks, use_llm=bool(context_chunks))

    if not ragas.passed:
        logger.warning("RAGAS veto: %s", ragas.veto_reason)
        return PostExecutionResult(answer=answer, ragas_passed=False, veto_reason=ragas.veto_reason)

    return PostExecutionResult(answer=answer, ragas_passed=True)
