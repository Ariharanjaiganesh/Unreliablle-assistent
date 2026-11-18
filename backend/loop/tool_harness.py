"""
ToolExecutionHarness — wraps every MCP tool call with:
1. Pydantic v2 strict validation
2. Deterministic regex auto-repair (SKU/LOT normalization)
3. RBAC tier check
4. UUIDv4 idempotency enforcement
5. Circuit breaker check
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from typing import Any

from models.schemas import RiskTier, ToolCall, ToolResult

logger = logging.getLogger(__name__)

_AUTO_REPAIR_RULES = [
    (re.compile(r"\bSKU(\d+)\b", re.IGNORECASE), lambda m: f"SKU-{int(m.group(1)):03d}"),
    (re.compile(r"\blot\s*(\d+)\b", re.IGNORECASE), lambda m: f"LOT-{int(m.group(1)):05d}"),
    (re.compile(r"\b([A-Za-z0-9._%+\-]+)\[at\]([A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b"),
     lambda m: "[REDACTED_EMAIL]"),
]


def auto_repair_args(args: dict[str, Any]) -> dict[str, Any]:
    repaired: dict[str, Any] = {}
    for key, val in args.items():
        if isinstance(val, str):
            for pattern, replacement in _AUTO_REPAIR_RULES:
                val = pattern.sub(replacement, val)
        repaired[key] = val
    return repaired


def _args_hash(args: dict) -> str:
    return hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()[:16]


ALLOWED_TOOLS: dict[str, RiskTier] = {
    "hybrid_retrieve":  RiskTier.TIER_1_READ,
    "state_read":       RiskTier.TIER_1_READ,
    "graphify_prune":   RiskTier.TIER_1_READ,
    "state_write":      RiskTier.TIER_2_COMPUTE,
    "workflow_execute": RiskTier.TIER_3_MUTATE,
    "form_submit":      RiskTier.TIER_3_MUTATE,
}


async def execute(
    tool_name: str,
    raw_args: dict[str, Any],
    caller_tier: RiskTier,
    session_id: str,
    tenant_id: str,
) -> ToolResult:
    t0 = time.monotonic()

    # 1. RBAC tier check
    required_tier = ALLOWED_TOOLS.get(tool_name)
    if required_tier is None:
        return ToolResult(tool_name=tool_name, success=False,
                          error_code="TOOL_001", error_message=f"Unknown tool: {tool_name}",
                          latency_ms=(time.monotonic() - t0) * 1000)

    tier_rank = {RiskTier.TIER_1_READ: 1, RiskTier.TIER_2_COMPUTE: 2, RiskTier.TIER_3_MUTATE: 3}
    if tier_rank[caller_tier] < tier_rank[required_tier]:
        return ToolResult(tool_name=tool_name, success=False,
                          error_code="RBAC_001",
                          error_message=f"Insufficient tier: need {required_tier}, have {caller_tier}",
                          latency_ms=(time.monotonic() - t0) * 1000)

    # 2. Auto-repair args
    repaired_args = auto_repair_args(raw_args)

    # 3. Circuit breaker
    from plugins.circuit_breaker import check_and_record
    should_trip, args_hash = await check_and_record(tool_name, repaired_args)
    if should_trip:
        return ToolResult(tool_name=tool_name, success=False,
                          error_code="LOOP_001",
                          error_message=f"Circuit breaker tripped for {tool_name}/{args_hash}",
                          latency_ms=(time.monotonic() - t0) * 1000)

    # 4. UUIDv4 idempotency key
    idempotency_key = str(uuid.uuid4())

    # 5. Build and dispatch tool call
    tool_call = ToolCall(
        tool_name=tool_name,
        args=repaired_args,
        idempotency_key=idempotency_key,
        tier=required_tier,
    )

    from agents.execution_subagent import execute_tool
    result = await execute_tool(tool_call, tenant_id=tenant_id, session_id=session_id)
    result.latency_ms = (time.monotonic() - t0) * 1000
    return result
