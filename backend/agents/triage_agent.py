"""
Triage Agent — temperature=0.0, distilled classifier.
Tags: (intent, domain, risk_tier, complexity) → RouteDecision.
Budget: <= 25 ms p95 (KPI-05). Uses llama-3.1-8b-instant via Groq.
"""
from __future__ import annotations

import time
import logging

from models.schemas import (
    Complexity, Domain, Intent, RiskTier, RouteDecision, TriageResult
)
from llm.groq_client import triage_classify

logger = logging.getLogger(__name__)


def _decide_route(intent: Intent, domain: Domain, risk_tier: RiskTier, complexity: Complexity) -> RouteDecision:
    if intent == Intent.OUT_OF_SCOPE:
        return RouteDecision.REJECT_OOS
    if intent == Intent.TRANSACTION or risk_tier == RiskTier.TIER_3_MUTATE:
        return RouteDecision.TRANSACTION_EXEC
    # Domain-specific queries always need RAG — they reference internal documents
    if domain in (Domain.POLICY, Domain.PRODUCT, Domain.OPS):
        return RouteDecision.HYBRID_RAG
    # Only truly general/simple queries skip RAG
    if intent == Intent.FACTOID_LOOKUP and complexity == Complexity.SIMPLE:
        return RouteDecision.DIRECT_ANSWER
    return RouteDecision.HYBRID_RAG


async def run(query: str) -> TriageResult:
    t0 = time.monotonic()
    try:
        raw = await triage_classify(query)
    except Exception as e:
        logger.error("Triage classification failed: %s", e)
        raw = {}

    intent = Intent(raw.get("intent", "reasoning"))
    domain = Domain(raw.get("domain", "general"))
    risk_tier = RiskTier(raw.get("risk_tier", "TIER_1_READ"))
    complexity = Complexity(raw.get("complexity", "simple"))
    route = _decide_route(intent, domain, risk_tier, complexity)
    latency_ms = (time.monotonic() - t0) * 1000

    logger.info("Triage: intent=%s domain=%s tier=%s complexity=%s route=%s (%.1f ms)",
                intent, domain, risk_tier, complexity, route, latency_ms)

    return TriageResult(
        intent=intent,
        domain=domain,
        risk_tier=risk_tier,
        complexity=complexity,
        route=route,
        latency_ms=latency_ms,
    )
