"""Composition root: wires settings, DB, embedder, backend and engine together (Factory)."""
from __future__ import annotations

import logging
import warnings

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from .backends import build_backend
from .config import Settings, get_settings
from .embeddings import build_embedder
from .engine import DecisionEngine
from .policy_store import PolicyStore

log = logging.getLogger("laya_credit")


def build_engine(settings: Settings | None = None) -> tuple[DecisionEngine, psycopg.Connection]:
    settings = settings or get_settings()
    conn = psycopg.connect(settings.dsn, row_factory=dict_row, autocommit=False)
    register_vector(conn)
    embedder = build_embedder(settings.embedding_provider, settings.embedding_model)
    backend = build_backend(settings)
    if backend.name == "mock":
        warnings.warn("DECISION_BACKEND=mock: answers come from a keyword heuristic, NOT from Laya.",
                      stacklevel=2)
    log.info("backend=%s checkpoint=%s version=%s", backend.name, backend.checkpoint, backend.version)
    return DecisionEngine(conn, settings, backend, PolicyStore(conn, embedder)), conn
