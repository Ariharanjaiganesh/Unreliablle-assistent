"""
SQLite-backed persistence for HITL records and audit log.
Schema-compatible with PostgreSQL — swap DATABASE_URL to postgres DSN for prod.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

import aiosqlite

from config import settings
from models.schemas import HITLRecord, HITLStatus

logger = logging.getLogger(__name__)

DB_PATH = "unreliable_assistant.db"

_CREATE_HITL = """
CREATE TABLE IF NOT EXISTS hitl_records (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    action TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING_APPROVAL',
    reviewer_id TEXT DEFAULT '',
    reason TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_CREATE_AUDIT = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    session_id TEXT,
    payload TEXT,
    prev_hash TEXT,
    created_at TEXT NOT NULL
)
"""

_CREATE_SESSION_FACTS = """
CREATE TABLE IF NOT EXISTS session_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    fact_key TEXT NOT NULL,
    fact_value TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_CREATE_HITL)
        await db.execute(_CREATE_AUDIT)
        await db.execute(_CREATE_SESSION_FACTS)
        await db.commit()
    logger.info("Database initialized at %s", DB_PATH)


async def db_health() -> str:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("SELECT 1")
        return "ok"
    except Exception as e:
        return f"error: {e}"


# ── HITL ──────────────────────────────────────────────────────────────────────

async def create_hitl_record(record: HITLRecord) -> str:
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO hitl_records (id, session_id, tenant_id, action, payload, status, reviewer_id, reason, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (record.id, record.session_id, record.tenant_id, record.action,
             json.dumps(record.payload), record.status.value,
             record.reviewer_id, record.reason, now, now),
        )
        await db.commit()
    logger.info("HITL record created: %s (tenant=%s)", record.id, record.tenant_id)
    return record.id


async def get_hitl_record(record_id: str) -> HITLRecord | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, session_id, tenant_id, action, payload, status, reviewer_id, reason FROM hitl_records WHERE id = ?",
            (record_id,),
        ) as cursor:
            row = await cursor.fetchone()
    if not row:
        return None
    return HITLRecord(
        id=row[0], session_id=row[1], tenant_id=row[2], action=row[3],
        payload=json.loads(row[4]), status=HITLStatus(row[5]),
        reviewer_id=row[6], reason=row[7],
    )


async def update_hitl_status(record_id: str, status: HITLStatus, reviewer_id: str, reason: str) -> bool:
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE hitl_records SET status = ?, reviewer_id = ?, reason = ?, updated_at = ? WHERE id = ?",
            (status.value, reviewer_id, reason, now, record_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def list_pending_hitl(tenant_id: str) -> list[HITLRecord]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, session_id, tenant_id, action, payload, status, reviewer_id, reason "
            "FROM hitl_records WHERE tenant_id = ? AND status = 'PENDING_APPROVAL' ORDER BY created_at DESC",
            (tenant_id,),
        ) as cursor:
            rows = await cursor.fetchall()
    return [
        HITLRecord(
            id=r[0], session_id=r[1], tenant_id=r[2], action=r[3],
            payload=json.loads(r[4]), status=HITLStatus(r[5]),
            reviewer_id=r[6], reason=r[7],
        )
        for r in rows
    ]


# ── Audit log ─────────────────────────────────────────────────────────────────

async def append_audit(event_type: str, tenant_id: str, session_id: str, payload: dict) -> None:
    import hashlib
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT prev_hash FROM audit_log ORDER BY id DESC LIMIT 1") as cursor:
            last = await cursor.fetchone()
        prev_hash = last[0] if last else "genesis"
        event_str = json.dumps({"event_type": event_type, "tenant_id": tenant_id, "payload": payload, "created_at": now})
        new_hash = hashlib.sha256(f"{prev_hash}:{event_str}".encode()).hexdigest()
        await db.execute(
            "INSERT INTO audit_log (event_type, tenant_id, session_id, payload, prev_hash, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (event_type, tenant_id, session_id, json.dumps(payload), new_hash, now),
        )
        await db.commit()


# ── Session facts ─────────────────────────────────────────────────────────────

async def save_session_fact(session_id: str, tenant_id: str, key: str, value: str) -> None:
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO session_facts (session_id, tenant_id, fact_key, fact_value, created_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, tenant_id, key, value, now),
        )
        await db.commit()


async def get_session_facts(session_id: str) -> dict[str, str]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT fact_key, fact_value FROM session_facts WHERE session_id = ? ORDER BY created_at DESC",
            (session_id,),
        ) as cursor:
            rows = await cursor.fetchall()
    seen: dict[str, str] = {}
    for key, value in rows:
        if key not in seen:
            seen[key] = value
    return seen
