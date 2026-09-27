"""Knowledge & Data layer: vector storage with two backends.

- memory   JSON file on disk, cosine search in Python. Zero setup, prototype only.
- pgvector Postgres + pgvector, the production path (docker-compose starts it).

Every chunk carries a `collection` (e.g. "student_service/scholarship") so an
agent only retrieves from the collections its manifest declares.
"""

from __future__ import annotations

import abc
import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from config.settings import Settings

log = logging.getLogger(__name__)


@dataclass
class Chunk:
    text: str
    source: str
    collection: str
    metadata: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid4().hex)


@dataclass
class Hit:
    chunk: Chunk
    score: float


class BaseVectorStore(abc.ABC):
    @abc.abstractmethod
    async def add(self, chunks: list[Chunk], embeddings: list[list[float]]) -> int: ...

    @abc.abstractmethod
    async def search(self, embedding: list[float], collections: list[str], top_k: int) -> list[Hit]: ...

    @abc.abstractmethod
    async def delete_collection(self, collection: str) -> int: ...

    @abc.abstractmethod
    async def count(self, collection: str | None = None) -> int: ...


class InMemoryVectorStore(BaseVectorStore):
    """Rows live in RAM and in a JSON file. The API process reloads the file when
    scripts/ingest.py has rewritten it, so re-ingesting takes effect without a restart."""

    def __init__(self, path: Path):
        self.path = path
        self._rows: list[dict] = []
        self._mtime: float | None = None
        self._load()

    def _load(self):
        if self.path.exists():
            self._mtime = self.path.stat().st_mtime
            self._rows = json.loads(self.path.read_text(encoding="utf-8"))

    def _reload_if_changed(self):
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            return
        if mtime != self._mtime:
            log.info("vector index changed on disk, reloading %s", self.path.name)
            self._load()

    def _flush(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._rows), encoding="utf-8")
        tmp.replace(self.path)  # atomic: a reader never sees a half-written file
        self._mtime = self.path.stat().st_mtime

    async def add(self, chunks, embeddings):
        for c, e in zip(chunks, embeddings):
            self._rows.append({"id": c.id, "text": c.text, "source": c.source,
                               "collection": c.collection, "metadata": c.metadata, "embedding": e})
        self._flush()
        return len(chunks)

    async def search(self, embedding, collections, top_k):
        def cos(a, b):
            dot = sum(x * y for x, y in zip(a, b))
            na = math.sqrt(sum(x * x for x in a)) or 1.0
            nb = math.sqrt(sum(x * x for x in b)) or 1.0
            return dot / (na * nb)

        self._reload_if_changed()
        rows = [r for r in self._rows if not collections or r["collection"] in collections]
        scored = sorted(((cos(embedding, r["embedding"]), r) for r in rows), key=lambda t: -t[0])
        return [Hit(Chunk(r["text"], r["source"], r["collection"], r["metadata"], r["id"]), s)
                for s, r in scored[:top_k]]

    async def delete_collection(self, collection):
        before = len(self._rows)
        self._rows = [r for r in self._rows if r["collection"] != collection]
        self._flush()
        return before - len(self._rows)

    async def count(self, collection=None):
        self._reload_if_changed()
        return sum(1 for r in self._rows if collection is None or r["collection"] == collection)


class PgVectorStore(BaseVectorStore):
    def __init__(self, database_url: str, dim: int):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        self.engine = create_async_engine(database_url, pool_pre_ping=True)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.dim = dim

    async def init_schema(self):
        from sqlalchemy import text

        async with self.engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY,
                    collection TEXT NOT NULL,
                    source TEXT NOT NULL,
                    text TEXT NOT NULL,
                    metadata JSONB DEFAULT '{{}}'::jsonb,
                    embedding vector({self.dim}) NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT now()
                )"""))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS chunks_collection_idx ON chunks (collection)"))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS chunks_embedding_idx ON chunks "
                "USING hnsw (embedding vector_cosine_ops)"))

    async def add(self, chunks, embeddings):
        from sqlalchemy import text

        async with self.Session() as s:
            for c, e in zip(chunks, embeddings):
                await s.execute(text(
                    "INSERT INTO chunks (id, collection, source, text, metadata, embedding) "
                    "VALUES (:id, :collection, :source, :text, CAST(:metadata AS jsonb), CAST(:embedding AS vector)) "
                    "ON CONFLICT (id) DO NOTHING"),
                    {"id": c.id, "collection": c.collection, "source": c.source, "text": c.text,
                     "metadata": json.dumps(c.metadata), "embedding": json.dumps(e)})
            await s.commit()
        return len(chunks)

    async def search(self, embedding, collections, top_k):
        from sqlalchemy import text

        where = "WHERE collection = ANY(:cols)" if collections else ""
        async with self.Session() as s:
            res = await s.execute(text(
                f"SELECT id, collection, source, text, metadata, "
                f"1 - (embedding <=> CAST(:emb AS vector)) AS score FROM chunks {where} "
                f"ORDER BY embedding <=> CAST(:emb AS vector) LIMIT :k"),
                {"emb": json.dumps(embedding), "cols": collections, "k": top_k})
            return [Hit(Chunk(r.text, r.source, r.collection, r.metadata or {}, r.id), float(r.score))
                    for r in res]

    async def delete_collection(self, collection):
        from sqlalchemy import text

        async with self.Session() as s:
            res = await s.execute(text("DELETE FROM chunks WHERE collection = :c"), {"c": collection})
            await s.commit()
            return res.rowcount or 0

    async def count(self, collection=None):
        from sqlalchemy import text

        async with self.Session() as s:
            if collection:
                res = await s.execute(text("SELECT count(*) FROM chunks WHERE collection = :c"), {"c": collection})
            else:
                res = await s.execute(text("SELECT count(*) FROM chunks"))
            return int(res.scalar_one())


def build_vector_store(settings: Settings) -> BaseVectorStore:
    if settings.vector_backend == "memory":
        log.warning("VECTOR_BACKEND=memory: prototype store, not for production")
        return InMemoryVectorStore(settings.vector_index_path)
    return PgVectorStore(settings.database_url, settings.embedding_dim)
