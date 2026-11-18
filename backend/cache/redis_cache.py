"""
Tenant-isolated Redis semantic cache.
Key = SHA256(tenant_id:user_id:normalized_query)
TTL = 300s ± 45s jitter to prevent stampede.
Falls back gracefully when Redis is unavailable.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import re

import redis.asyncio as aioredis

from config import settings

logger = logging.getLogger(__name__)

_pool: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis | None:
    global _pool
    if _pool is None:
        try:
            _pool = aioredis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=2)
            await _pool.ping()
        except Exception as e:
            logger.warning("Redis unavailable: %s — cache disabled", e)
            _pool = None
    return _pool


def _normalize_query(query: str) -> str:
    q = query.lower().strip()
    q = re.sub(r"[^\w\s]", " ", q)
    return re.sub(r"\s+", " ", q).strip()


def _make_key(tenant_id: str, user_id: str, query: str) -> str:
    normalized = _normalize_query(query)
    raw = f"{tenant_id}:{user_id}:{normalized}"
    return "cache:" + hashlib.sha256(raw.encode()).hexdigest()


def _jittered_ttl() -> int:
    base = settings.redis_cache_ttl
    jitter = settings.redis_ttl_jitter
    return base + random.randint(-jitter, jitter)


async def cache_get(tenant_id: str, user_id: str, query: str) -> dict | None:
    r = await get_redis()
    if r is None:
        return None
    key = _make_key(tenant_id, user_id, query)
    try:
        raw = await r.get(key)
        if raw:
            data = json.loads(raw)
            if data.get("tenant_id") != tenant_id:
                logger.error("CACHE ISOLATION VIOLATION detected for key %s", key)
                await r.delete(key)
                return None
            return data
    except Exception as e:
        logger.warning("Cache get error: %s", e)
    return None


async def cache_set(tenant_id: str, user_id: str, query: str, value: dict) -> None:
    r = await get_redis()
    if r is None:
        return
    key = _make_key(tenant_id, user_id, query)
    value["tenant_id"] = tenant_id  # embed for isolation check on read
    try:
        await r.setex(key, _jittered_ttl(), json.dumps(value))
    except Exception as e:
        logger.warning("Cache set error: %s", e)


async def cache_delete(tenant_id: str, user_id: str, query: str) -> None:
    r = await get_redis()
    if r is None:
        return
    key = _make_key(tenant_id, user_id, query)
    try:
        await r.delete(key)
    except Exception as e:
        logger.warning("Cache delete error: %s", e)


async def circuit_breaker_get(tool_name: str, args_hash: str) -> int:
    """Return failure count for (tool_name, args_hash) pair."""
    r = await get_redis()
    if r is None:
        return 0
    key = f"cb:{tool_name}:{args_hash}"
    try:
        val = await r.get(key)
        return int(val) if val else 0
    except Exception:
        return 0


async def circuit_breaker_inc(tool_name: str, args_hash: str) -> int:
    """Increment and return new failure count. TTL 60s per window."""
    r = await get_redis()
    if r is None:
        return 0
    key = f"cb:{tool_name}:{args_hash}"
    try:
        count = await r.incr(key)
        await r.expire(key, 60)
        return count
    except Exception:
        return 0


async def circuit_breaker_reset(tool_name: str, args_hash: str) -> None:
    r = await get_redis()
    if r is None:
        return
    key = f"cb:{tool_name}:{args_hash}"
    try:
        await r.delete(key)
    except Exception:
        pass


async def redis_health() -> str:
    r = await get_redis()
    if r is None:
        return "unavailable"
    try:
        await r.ping()
        return "ok"
    except Exception:
        return "unavailable"
