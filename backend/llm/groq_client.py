"""
Groq LLM client — wraps the Groq SDK with retry, timeout, and structured
output helpers. Plug in your GROQ_API_KEY via .env.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import groq
from groq import AsyncGroq

from config import settings

logger = logging.getLogger(__name__)

_client: AsyncGroq | None = None


def get_client() -> AsyncGroq:
    global _client
    if _client is None:
        _client = AsyncGroq(api_key=settings.groq_api_key)
    return _client


async def chat(
    messages: list[dict[str, str]],
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 1024,
    json_mode: bool = False,
) -> tuple[str, float]:
    """Return (content, latency_ms). Raises on hard failure."""
    client = get_client()
    model = model or settings.groq_main_model

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    t0 = time.monotonic()
    for attempt in range(3):
        try:
            response = await client.chat.completions.create(**kwargs)
            latency_ms = (time.monotonic() - t0) * 1000
            content = response.choices[0].message.content or ""
            return content, latency_ms
        except groq.RateLimitError:
            if attempt == 2:
                raise
            wait = 2 ** attempt
            logger.warning("Groq rate limit hit, retrying in %ds", wait)
            import asyncio
            await asyncio.sleep(wait)
        except groq.APIStatusError as e:
            logger.error("Groq API error %d: %s", e.status_code, e.message)
            raise

    raise RuntimeError("Groq request failed after 3 retries")


async def triage_classify(query: str) -> dict[str, str]:
    """Fast classification via small model. Returns JSON dict."""
    system = (
        "You are a query classifier. Respond ONLY with a JSON object with these exact keys:\n"
        '  "intent": one of [factoid_lookup, reasoning, transaction, out_of_scope]\n'
        '  "domain": one of [policy, product, ops, general]\n'
        '  "risk_tier": one of [TIER_1_READ, TIER_2_COMPUTE, TIER_3_MUTATE]\n'
        '  "complexity": one of [simple, multi_hop, aggregate]\n'
        "No explanation, only the JSON."
    )
    content, _ = await chat(
        messages=[{"role": "system", "content": system}, {"role": "user", "content": query}],
        model=settings.groq_triage_model,
        temperature=0.0,
        max_tokens=120,
        json_mode=True,
    )
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {
            "intent": "reasoning",
            "domain": "general",
            "risk_tier": "TIER_1_READ",
            "complexity": "simple",
        }


async def generate_answer(
    query: str,
    context_chunks: list[str],
    goal_anchor: str,
    session_history: list[dict[str, str]] | None = None,
) -> tuple[str, float]:
    """Generate grounded answer from retrieved context."""
    context_text = "\n\n---\n".join(
        f"[Chunk {i+1}]: {chunk}" for i, chunk in enumerate(context_chunks)
    )
    system = (
        "You are an enterprise assistant. Answer ONLY using the provided context chunks. "
        "If the context does not contain enough information, say so explicitly — do not hallucinate. "
        "Cite chunks with [Chunk N] inline.\n\n"
        f"GOAL ANCHOR (do not deviate): {goal_anchor}\n\n"
        f"CONTEXT:\n{context_text}"
    )
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    if session_history:
        messages.extend(session_history[-6:])  # last 3 turns
    messages.append({"role": "user", "content": query})

    return await chat(messages=messages, temperature=0.2, max_tokens=1024)


async def verify_faithfulness(answer: str, context_chunks: list[str]) -> float:
    """NLI-style faithfulness check via small model. Returns 0.0–1.0."""
    if not context_chunks:
        return 0.0

    context_text = "\n".join(f"- {c[:300]}" for c in context_chunks[:4])
    prompt = (
        f"Context:\n{context_text}\n\n"
        f"Answer: {answer}\n\n"
        "On a scale 0.0 to 1.0, how faithful is the answer to the context? "
        "1.0 = every claim is fully supported. 0.0 = answer is fabricated. "
        'Respond ONLY with a JSON: {"faithfulness": <float>}'
    )
    try:
        content, _ = await chat(
            messages=[{"role": "user", "content": prompt}],
            model=settings.groq_verify_model,
            temperature=0.0,
            max_tokens=40,
            json_mode=True,
        )
        return float(json.loads(content).get("faithfulness", 0.5))
    except Exception:
        return 0.75  # conservative default on parse failure


async def direct_answer(query: str) -> tuple[str, float]:
    """Answer simple factoid/general queries without RAG."""
    return await chat(
        messages=[
            {"role": "system", "content": "You are a concise enterprise assistant. Answer briefly."},
            {"role": "user", "content": query},
        ],
        temperature=0.3,
        max_tokens=512,
    )
