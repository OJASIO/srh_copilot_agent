"""RAG helper shared by all agents: embed the query, search the declared
collections, and return hits plus a ready-to-paste context block."""

from __future__ import annotations

import re

from core.providers import BaseEmbedder
from core.schemas import Citation
from core.vector_store import BaseVectorStore, Hit

_CONTEXT_TAG = re.compile(r"</?\s*context\s*>", re.I)


class Retriever:
    def __init__(self, embedder: BaseEmbedder, store: BaseVectorStore):
        self.embedder = embedder
        self.store = store

    async def search(self, query: str, collections: list[str], top_k: int = 5) -> list[Hit]:
        [vec] = await self.embedder.embed([query])
        return await self.store.search(vec, collections, top_k)

    async def search_many(self, queries: list[str], collections: list[str], top_k: int = 5) -> list[Hit]:
        """Search with several phrasings of one question and merge the results,
        keeping each chunk once with its best score. Used for follow-up questions
        ("and what is the deadline for it?"), where the last message alone lacks
        the topic: one query is the message, one is the message plus the previous one."""
        queries = [q for q in dict.fromkeys(queries) if q.strip()]
        if len(queries) <= 1:
            return await self.search(queries[0] if queries else "", collections, top_k)
        vectors = await self.embedder.embed(queries)
        best: dict[str, Hit] = {}
        for vec in vectors:
            for hit in await self.store.search(vec, collections, top_k):
                if hit.chunk.id not in best or hit.score > best[hit.chunk.id].score:
                    best[hit.chunk.id] = hit
        return sorted(best.values(), key=lambda h: -h.score)[:top_k]

    @staticmethod
    def as_context(hits: list[Hit], max_chars: int = 6000) -> str:
        parts, used = [], 0
        for i, h in enumerate(hits, 1):
            text = _CONTEXT_TAG.sub("", h.chunk.text.strip())  # a chunk cannot close the context block
            block = f"[{i}] Source: {h.chunk.source}\n{text}\n"
            if used + len(block) > max_chars:
                break
            parts.append(block)
            used += len(block)
        return "\n".join(parts) if parts else "(no relevant documents found)"

    @staticmethod
    def as_citations(hits: list[Hit]) -> list[Citation]:
        return [Citation(source=h.chunk.source, snippet=h.chunk.text[:200], score=round(h.score, 3)) for h in hits]
