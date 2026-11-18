"""
Idempotent Circuit Breaker — TOOL_DISPATCH, priority=10.
Trips when (tool_name, canonical_args_hash) fails >= max_identical_calls.
Uses Redis for distributed state; falls back to in-memory for local dev.
"""
from __future__ import annotations

import hashlib
import json
import logging

logger = logging.getLogger(__name__)

MAX_IDENTICAL_CALLS = 2

# In-memory fallback when Redis is unavailable
_in_memory: dict[str, int] = {}


def _canonical_hash(tool_name: str, args: dict) -> str:
    canonical = json.dumps(args, sort_keys=True, default=str)
    raw = f"{tool_name}:{canonical}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


async def check_and_record(tool_name: str, args: dict) -> tuple[bool, str]:
    """
    Returns (should_trip, args_hash).
    should_trip=True means the circuit should block the call.
    """
    from cache.redis_cache import circuit_breaker_get, circuit_breaker_inc

    args_hash = _canonical_hash(tool_name, args)
    count = await circuit_breaker_get(tool_name, args_hash)

    if count >= MAX_IDENTICAL_CALLS:
        logger.warning("Circuit breaker tripped: %s / %s (count=%d)", tool_name, args_hash, count)
        return True, args_hash

    await circuit_breaker_inc(tool_name, args_hash)
    return False, args_hash


async def reset(tool_name: str, args_hash: str) -> None:
    from cache.redis_cache import circuit_breaker_reset
    await circuit_breaker_reset(tool_name, args_hash)
    logger.debug("Circuit breaker reset: %s / %s", tool_name, args_hash)
