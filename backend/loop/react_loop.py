"""
Bounded ReAct Loop — deterministic state machine.
States: INIT → TRIAGE → REASONING → TOOL_CALL → OBSERVATION → VERIFY → DONE/HALTED_*
Hard limits: max_iterations=5, per_step_timeout=120ms, total_budget=450ms.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from config import settings
from models.schemas import (
    GoalAnchorBlock, LoopState, RAGASScore, ReActResult, ReActStep, RiskTier
)
from llm.groq_client import generate_answer, direct_answer
from agents.triage_agent import run as triage_run
from agents.retrieval_subagent import retrieve
from agents.verification_subagent import verify
from models.schemas import HybridRetrieveInput
from db.database import save_session_fact, get_session_facts, append_audit
from observability.otel import span

logger = logging.getLogger(__name__)


def _compress_observation(text: str, max_tokens: int = 180) -> str:
    """Strip boilerplate and truncate to max_tokens (approx 4 chars/token)."""
    import re
    text = re.sub(r'"[a-z_]+"\s*:\s*(?:null|true|false|\[\]|\{\})', "", text)
    text = re.sub(r"\s+", " ", text).strip()
    char_limit = max_tokens * 4
    return text[:char_limit] + ("…" if len(text) > char_limit else "")


async def run(
    query: str,
    tenant_id: str,
    user_id: str,
    session_id: str | None = None,
) -> ReActResult:
    session_id = session_id or str(uuid.uuid4())
    t_total = time.monotonic()

    with span("agent.react.loop", {"session_id": session_id, "tenant_id": tenant_id}):

        # ── GoalAnchorBlock (immutable, index 0) ──────────────────────────────
        anchor = GoalAnchorBlock(
            original_query=query,
            session_id=session_id,
            tenant_id=tenant_id,
        )
        steps: list[ReActStep] = []
        state = LoopState.INIT

        # ── TRIAGE ────────────────────────────────────────────────────────────
        state = LoopState.TRIAGE
        with span("triage.classify"):
            triage = await triage_run(query)

        await append_audit("TRIAGE_COMPLETE", tenant_id, session_id, {
            "intent": triage.intent, "domain": triage.domain,
            "risk_tier": triage.risk_tier, "route": triage.route,
        })

        steps.append(ReActStep(step_number=0, state=LoopState.TRIAGE,
                                thought=f"Route={triage.route} intent={triage.intent}",
                                latency_ms=triage.latency_ms))

        # ── REJECT OOS ────────────────────────────────────────────────────────
        if triage.route.value == "REJECT_OOS":
            total_ms = (time.monotonic() - t_total) * 1000
            return ReActResult(
                answer="I'm sorry, that query is outside my operational scope. Please contact your system administrator.",
                steps=steps, final_state=LoopState.DONE, total_latency_ms=total_ms,
            )

        # ── DIRECT ANSWER ─────────────────────────────────────────────────────
        if triage.route.value == "DIRECT_ANSWER":
            with span("llm.generate", {"mode": "direct"}):
                answer, gen_ms = await direct_answer(query)
            steps.append(ReActStep(step_number=1, state=LoopState.DONE,
                                    thought="Direct answer, no RAG needed", observation=answer[:200],
                                    latency_ms=gen_ms))
            total_ms = (time.monotonic() - t_total) * 1000
            return ReActResult(answer=answer, steps=steps, final_state=LoopState.DONE,
                                total_latency_ms=total_ms)

        # ── HYBRID RAG / TRANSACTION EXEC LOOP ───────────────────────────────
        context_chunks: list[str] = []
        session_history: list[dict[str, str]] = []
        final_answer = ""
        ragas_result: RAGASScore | None = None
        hitl_pending = False
        hitl_record_id = ""

        for iteration in range(settings.max_react_iterations):
            if (time.monotonic() - t_total) * 1000 > settings.total_loop_budget_ms:
                state = LoopState.HALTED_MAXSTEP
                break

            state = LoopState.REASONING
            step = ReActStep(step_number=iteration + 1, state=state)
            t_step = time.monotonic()

            # REASONING: decide what to do this step
            if triage.route.value == "HYBRID_RAG" and not context_chunks:
                step.thought = "Need to retrieve relevant context via Hybrid RAG"
                step.tool_name = "hybrid_retrieve"
                step.tool_args = {"query": anchor.original_query}
                state = LoopState.TOOL_CALL
            elif triage.route.value == "TRANSACTION_EXEC":
                step.thought = "Transaction execution requested — checking tier"
                step.tool_name = "workflow_execute"
                step.tool_args = {"action": query, "session_id": session_id}
                state = LoopState.TOOL_CALL
            elif context_chunks:
                step.thought = "Context retrieved, generating answer"
                state = LoopState.VERIFY
            else:
                step.thought = "No retrieval needed, generating direct answer"
                state = LoopState.VERIFY

            # TOOL_CALL
            if state == LoopState.TOOL_CALL:
                state = LoopState.TOOL_CALL
                from loop.tool_harness import execute as harness_execute

                tool_result = await harness_execute(
                    tool_name=step.tool_name,
                    raw_args=step.tool_args,
                    caller_tier=triage.risk_tier,
                    session_id=session_id,
                    tenant_id=tenant_id,
                )

                if not tool_result.success:
                    if tool_result.error_code == "LOOP_001":
                        state = LoopState.HALTED_BREAKER
                        step.observation = tool_result.error_message
                        step.latency_ms = (time.monotonic() - t_step) * 1000
                        steps.append(step)
                        break
                    if tool_result.error_code == "STATE_002":
                        # HITL freeze
                        hitl_pending = True
                        hitl_record_id = tool_result.error_message.split("Record ID: ")[-1].split(".")[0]
                        step.observation = tool_result.error_message
                        step.latency_ms = (time.monotonic() - t_step) * 1000
                        steps.append(step)
                        total_ms = (time.monotonic() - t_total) * 1000
                        return ReActResult(
                            answer="Your request requires human review and has been queued for approval.",
                            steps=steps, final_state=LoopState.DONE,
                            total_latency_ms=total_ms, hitl_pending=True,
                            hitl_record_id=hitl_record_id,
                        )

                # For hybrid_retrieve: run the actual RAG pipeline
                if step.tool_name == "hybrid_retrieve":
                    with span("agent.react.step", {"step": iteration + 1, "tool": "hybrid_retrieve"}):
                        rag_input = HybridRetrieveInput(
                            tenant_id=tenant_id,
                            query=anchor.original_query,
                        )
                        rag_output = await retrieve(rag_input)

                    if rag_output.abstained:
                        # Deterministic abstention
                        total_ms = (time.monotonic() - t_total) * 1000
                        return ReActResult(
                            answer="I could not find sufficiently relevant information to answer this query reliably.",
                            steps=steps, final_state=LoopState.DONE,
                            total_latency_ms=total_ms, abstained=True,
                        )

                    context_chunks = [c.text for c in rag_output.chunks]
                    observation = f"Retrieved {len(context_chunks)} context chunks (top score={rag_output.chunks[0].rerank_score:.2f})"
                else:
                    observation = str(tool_result.result)[:500]

                # OBSERVATION: compress
                state = LoopState.OBSERVATION
                step.observation = _compress_observation(observation)

            # VERIFY → generate answer
            if state in (LoopState.VERIFY, LoopState.OBSERVATION):
                state = LoopState.VERIFY
                if not final_answer:
                    with span("llm.generate"):
                        final_answer, gen_ms = await generate_answer(
                            query=anchor.original_query,
                            context_chunks=context_chunks,
                            goal_anchor=anchor.original_query,
                            session_history=session_history,
                        )
                    # Verification subagent
                    ragas_result = await verify(
                        answer=final_answer,
                        query=anchor.original_query,
                        context_chunks=context_chunks,
                        session_id=session_id,
                        tenant_id=tenant_id,
                    )

                    if not ragas_result.passed:
                        # RAGAS veto — escalate
                        logger.warning("RAGAS veto in loop iter=%d: %s", iteration, ragas_result.veto_reason)
                        # Try once more with higher quality, then accept
                        if iteration < settings.max_react_iterations - 1:
                            context_chunks = []  # force re-retrieval
                            final_answer = ""
                            step.thought = f"RAGAS veto: {ragas_result.veto_reason} — re-retrieving"
                            step.latency_ms = (time.monotonic() - t_step) * 1000
                            steps.append(step)
                            continue

                state = LoopState.DONE
                step.state = LoopState.DONE
                step.latency_ms = (time.monotonic() - t_step) * 1000
                steps.append(step)
                break

            step.latency_ms = (time.monotonic() - t_step) * 1000
            steps.append(step)

        # Persist verified facts
        if final_answer:
            await save_session_fact(session_id, tenant_id, "last_answer", final_answer[:500])

        total_ms = (time.monotonic() - t_total) * 1000

        if not final_answer:
            if state == LoopState.HALTED_BREAKER:
                final_answer = "Request halted: circuit breaker tripped on repeated identical tool call. Please rephrase your request."
            elif state == LoopState.HALTED_MAXSTEP:
                final_answer = "Request could not be completed within the allowed number of steps. Please simplify your query."
            else:
                final_answer = "Unable to generate a response at this time."

        return ReActResult(
            answer=final_answer,
            steps=steps,
            final_state=state,
            total_latency_ms=total_ms,
            ragas=ragas_result,
            hitl_pending=hitl_pending,
            hitl_record_id=hitl_record_id,
        )
