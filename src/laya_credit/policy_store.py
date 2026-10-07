"""Policy knowledge store: clause-level chunking, vector indexing and retrieval (RAG)."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

import psycopg

from .embeddings import Embedder

_FRONT_MATTER = re.compile(r"^---\n(.*?)\n---\n", re.S)
_CLAUSE = re.compile(r"^([A-Z]{2}-\d+\.\d+)\s+(.*)$")
_SECTION = re.compile(r"^##\s+(.*)$")


@dataclass(frozen=True)
class PolicyChunk:
    product_code: str
    clause_ref: str
    section_title: str
    content: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


def parse_front_matter(text: str) -> dict:
    m = _FRONT_MATTER.match(text)
    if not m:
        return {}
    pairs = (line.split(":", 1) for line in m.group(1).splitlines() if ":" in line)
    return {k.strip(): v.strip() for k, v in pairs}


def chunk_policy(path: Path, product_code: str) -> List[PolicyChunk]:
    """One chunk per numbered clause. Clauses are short (~40 tokens), which keeps the RAG
    context inside Laya's input window (512 tokens on the English checkpoint)."""
    text = _FRONT_MATTER.sub("", path.read_text(encoding="utf-8"))
    section, chunks = "", []
    for line in text.splitlines():
        line = line.strip()
        if s := _SECTION.match(line):
            section = s.group(1)
        elif c := _CLAUSE.match(line):
            chunks.append(PolicyChunk(product_code, c.group(1), section, f"{c.group(1)} {c.group(2)}"))
    return chunks


class PolicyStore:
    def __init__(self, conn: psycopg.Connection, embedder: Embedder) -> None:
        self.conn = conn
        self.embedder = embedder

    def index(self, chunks: Iterable[PolicyChunk], policy_version: str) -> int:
        chunks = list(chunks)
        vectors = self.embedder.embed([c.content for c in chunks])
        with self.conn.cursor() as cur:
            for c, v in zip(chunks, vectors):
                cur.execute(
                    """INSERT INTO policy_chunk (product_code, clause_ref, section_title, content,
                           content_sha256, policy_version, embedding_model, embedding)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (product_code, clause_ref, policy_version) DO UPDATE
                       SET content = EXCLUDED.content, content_sha256 = EXCLUDED.content_sha256,
                           embedding = EXCLUDED.embedding, embedding_model = EXCLUDED.embedding_model""",
                    (c.product_code, c.clause_ref, c.section_title, c.content, c.sha256,
                     policy_version, self.embedder.name, v))
        return len(chunks)

    def retrieve(self, product_code: str, policy_version: str, query: str, k: int = 1) -> List[dict]:
        """Cosine-similarity search, hard-filtered to ONE product and ONE policy version, so a
        question can never be grounded in another product's (or a superseded) policy."""
        qvec = self.embedder.embed([query])[0]
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT clause_ref, content, 1 - (embedding <=> %s) AS similarity
                   FROM policy_chunk
                   WHERE product_code = %s AND policy_version = %s
                   ORDER BY embedding <=> %s LIMIT %s""",
                (qvec, product_code, policy_version, qvec, k))
            return list(cur.fetchall())

    def get_clause(self, product_code: str, policy_version: str, clause_ref: str) -> dict | None:
        with self.conn.cursor() as cur:
            cur.execute("""SELECT clause_ref, content, 1.0 AS similarity FROM policy_chunk
                           WHERE product_code=%s AND policy_version=%s AND clause_ref=%s""",
                        (product_code, policy_version, clause_ref))
            return cur.fetchone()
