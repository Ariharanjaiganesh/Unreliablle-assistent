"""
"The Unreliable Assistant" — Enterprise Backend
FastAPI async application with 3-stage Hybrid RAG, Groq LLM, bounded ReAct loop,
HITL gate, tenant-isolated Redis cache, and OpenTelemetry instrumentation.

Usage:
  1. Copy .env.example to .env and add GROQ_API_KEY=<your key>
  2. pip install -r requirements.txt
  3. uvicorn main:app --reload
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from db.database import init_db
from observability.otel import setup_otel
from api.routes import router

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting Unreliable Assistant backend (env=%s)", settings.app_env)
    setup_otel(settings.otel_service_name, settings.otel_endpoint)
    await init_db()
    _load_sample_knowledge()
    logger.info("Backend ready. Groq model: %s", settings.groq_main_model)
    yield
    # Shutdown
    logger.info("Shutting down backend")


def _load_sample_knowledge():
    """Auto-index knowledge vault documents on startup."""
    vault_dir = os.path.join(os.path.dirname(__file__), "knowledge_vault")
    if not os.path.isdir(vault_dir):
        return

    from rag.bm25_retriever import get_bm25_index
    from rag.dense_retriever import get_dense_index

    documents: list[dict] = []
    chunk_id = 0
    for fname in os.listdir(vault_dir):
        if not fname.endswith(".md"):
            continue
        fpath = os.path.join(vault_dir, fname)
        with open(fpath, "r", encoding="utf-8") as f:
            text = f.read()

        # Strip YAML frontmatter so it doesn't dilute retrieval scores
        if text.startswith("---"):
            end = text.find("---", 3)
            if end != -1:
                text = text[end + 3:].lstrip()

        # Split into ~512-token chunks (approx 2000 chars) with 64-token overlap
        chunk_size = 2000
        overlap = 256
        pos = 0
        page = 1
        while pos < len(text):
            chunk_text = text[pos : pos + chunk_size]
            if chunk_text.strip():
                documents.append({
                    "chunk_id": f"{fname}:chunk_{chunk_id}",
                    "text": chunk_text.strip(),
                    "source_doc": fname,
                    "page": page,
                })
                chunk_id += 1
                page += 1
            pos += chunk_size - overlap

    if documents:
        get_bm25_index().build(documents)
        get_dense_index().build(documents)
        logger.info("Knowledge vault: indexed %d chunks from %s", len(documents), vault_dir)


app = FastAPI(
    title="The Unreliable Assistant — Enterprise API",
    description=(
        "MCP-enabled, plugin-extensible agentic system with 3-stage Hybrid RAG, "
        "Groq LLM, bounded ReAct loop, tenant-isolated Redis cache, HITL gate, "
        "and OpenTelemetry observability. Verified against 36 enterprise KPIs."
    ),
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1")


@app.get("/", tags=["Root"])
async def root():
    return {
        "service": "unreliable-assistant",
        "version": "2.0.0",
        "docs": "/docs",
        "health": "/api/v1/health",
    }
