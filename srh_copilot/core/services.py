"""Container for the Shared AI Services layer. Built once at startup and injected into every
agent, so agents never construct clients themselves. Swap an implementation
here (or via settings) and every plug picks it up."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from config.settings import Settings
from core.guardrails import Guardrails
from core.providers import BaseEmbedder, BaseLLM, build_llm_and_embedder
from core.sessions import InMemorySessionStore, PostgresSessionStore, SessionStore
from core.prompts import PromptStore
from core.retrieval import Retriever
from core.vector_store import BaseVectorStore, PgVectorStore, build_vector_store

log = logging.getLogger(__name__)


@dataclass
class ServiceContainer:
    settings: Settings
    llm: BaseLLM
    embedder: BaseEmbedder
    vector_store: BaseVectorStore
    retriever: Retriever
    prompts: PromptStore
    guardrails: Guardrails
    sessions: SessionStore

    @classmethod
    def build(cls, settings: Settings) -> "ServiceContainer":
        llm, embedder = build_llm_and_embedder(settings)
        store = build_vector_store(settings)
        sessions: SessionStore = (
            PostgresSessionStore(settings.database_url)
            if settings.vector_backend == "pgvector" else InMemorySessionStore()
        )
        prompts = PromptStore()
        guardrails = Guardrails(settings.max_message_chars, max_input_chars=settings.max_input_chars,
                                mask_pii=settings.guardrails_mask_pii_in_messages,
                                allowed_email_domains=settings.pii_allowed_email_domains,
                                prompt_texts=prompts.all_texts())
        return cls(
            settings=settings,
            llm=llm,
            embedder=embedder,
            vector_store=store,
            retriever=Retriever(embedder, store),
            prompts=prompts,
            guardrails=guardrails,
            sessions=sessions,
        )

    async def startup(self):
        if isinstance(self.vector_store, PgVectorStore):
            await self.vector_store.init_schema()
        if isinstance(self.sessions, PostgresSessionStore):
            await self.sessions.init_schema()
        log.info("services ready: llm=%s vector=%s", self.settings.llm_provider, self.settings.vector_backend)
