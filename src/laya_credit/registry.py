"""Model registry gate (MLOps): decisions are served only by an APPROVED, version-pinned model."""
from __future__ import annotations

from typing import Optional

import psycopg


class UnregisteredModelError(RuntimeError):
    pass


class ModelRegistry:
    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn

    def register(self, backend: str, checkpoint: str, revision: Optional[str], laya_version: str,
                 min_confidence: float, approver: str, notes: str = "") -> int:
        with self.conn.cursor() as cur:
            cur.execute("""UPDATE model_registry SET status='RETIRED'
                           WHERE backend=%s AND checkpoint=%s AND status='APPROVED'""",
                        (backend, checkpoint))
            cur.execute("""INSERT INTO model_registry (backend, checkpoint, revision, laya_version,
                               min_confidence, status, approved_by, approved_at, notes)
                           VALUES (%s,%s,%s,%s,%s,'APPROVED',%s,now(),%s) RETURNING registry_id""",
                        (backend, checkpoint, revision, laya_version, min_confidence, approver, notes))
            return cur.fetchone()["registry_id"]

    def approved(self, backend: str, checkpoint: str, revision: Optional[str]) -> Optional[dict]:
        with self.conn.cursor() as cur:
            cur.execute("""SELECT * FROM model_registry
                           WHERE backend=%s AND checkpoint=%s AND status='APPROVED'
                             AND revision IS NOT DISTINCT FROM %s
                           ORDER BY registry_id DESC LIMIT 1""", (backend, checkpoint, revision))
            return cur.fetchone()

    def require(self, backend: str, checkpoint: str, revision: Optional[str]) -> dict:
        entry = self.approved(backend, checkpoint, revision)
        if entry is None:
            raise UnregisteredModelError(
                f"No APPROVED registry entry for backend={backend} checkpoint={checkpoint} "
                f"revision={revision}. Register it first: python scripts/01_create_dummy_data.py "
                f"--register-only")
        return entry
