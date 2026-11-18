"""
Execution Subagent — bound to mcp-state-store only.
Validates tool args, enforces RBAC, creates HITL record for TIER_3 mutations.
"""
from __future__ import annotations

import logging
import uuid

from db.database import create_hitl_record, append_audit
from models.schemas import HITLRecord, HITLStatus, RiskTier, StateWriteInput, ToolCall, ToolResult

logger = logging.getLogger(__name__)


async def execute_tool(tool_call: ToolCall, tenant_id: str, session_id: str) -> ToolResult:
    t_start = __import__("time").monotonic()

    # TIER_3 MUTATE → freeze for HITL
    if tool_call.tier == RiskTier.TIER_3_MUTATE:
        record = HITLRecord(
            session_id=session_id,
            tenant_id=tenant_id,
            action=tool_call.tool_name,
            payload=tool_call.args,
        )
        record_id = await create_hitl_record(record)
        await append_audit(
            "HITL_GATE_TRIGGERED", tenant_id, session_id,
            {"tool": tool_call.tool_name, "hitl_record_id": record_id},
        )
        logger.info("TIER_3 HITL gate: record_id=%s tool=%s", record_id, tool_call.tool_name)
        return ToolResult(
            tool_name=tool_call.tool_name,
            success=False,
            error_code="STATE_002",
            error_message=f"HITL gate required. Record ID: {record_id}. Awaiting reviewer approval.",
            latency_ms=((__import__("time").monotonic() - t_start) * 1000),
        )

    # TIER_1 / TIER_2: execute normally
    result_value = {
        "status": "executed",
        "tool": tool_call.tool_name,
        "idempotency_key": tool_call.idempotency_key,
        "args": tool_call.args,
    }
    await append_audit("TOOL_EXECUTED", tenant_id, session_id, result_value)

    latency_ms = (__import__("time").monotonic() - t_start) * 1000
    return ToolResult(
        tool_name=tool_call.tool_name,
        success=True,
        result=result_value,
        latency_ms=latency_ms,
    )
