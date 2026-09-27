"""Conversation sessions and audit trail.

In-memory implementation for the prototype; PostgresSessionStore uses the same
tables docker-compose creates (see scripts/init_db.sql). The orchestrator only
depends on the SessionStore interface.

A session belongs to the user who created it: history is always looked up by
(user_id, session_id), so knowing someone else's session id reveals nothing.
Messages are stored after the input guardrail, so personal data such as email
addresses and phone numbers is already masked.
"""

from __future__ import annotations

import abc
import json
from collections import defaultdict
from datetime import datetime, timezone

from core.schemas import Message


class SessionStore(abc.ABC):
    @abc.abstractmethod
    async def history(self, session_id: str, user_id: str, limit: int = 20) -> list[Message]: ...

    @abc.abstractmethod
    async def append(self, session_id: str, user_id: str, message: Message) -> None: ...

    @abc.abstractmethod
    async def audit(self, event: dict) -> None: ...


class InMemorySessionStore(SessionStore):
    def __init__(self):
        self._msgs: dict[tuple[str, str], list[Message]] = defaultdict(list)
        self.events: list[dict] = []

    async def history(self, session_id, user_id, limit=20):
        return self._msgs[(user_id, session_id)][-limit:]

    async def append(self, session_id, user_id, message):
        self._msgs[(user_id, session_id)].append(message)

    async def audit(self, event):
        event.setdefault("ts", datetime.now(timezone.utc).isoformat())
        self.events.append(event)


class PostgresSessionStore(SessionStore):
    def __init__(self, database_url: str):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        self.engine = create_async_engine(database_url, pool_pre_ping=True)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def init_schema(self):
        from sqlalchemy import text

        async with self.engine.begin() as conn:
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS messages (
                    id BIGSERIAL PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    task_id TEXT,
                    created_at TIMESTAMPTZ DEFAULT now())"""))
            # databases created before task_id existed
            await conn.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS task_id TEXT"))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS messages_user_session_idx ON messages (user_id, session_id, id)"))
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id BIGSERIAL PRIMARY KEY,
                    ts TIMESTAMPTZ DEFAULT now(),
                    event JSONB NOT NULL)"""))

    async def history(self, session_id, user_id, limit=20):
        from sqlalchemy import text

        async with self.Session() as s:
            res = await s.execute(text(
                "SELECT role, content, task_id, created_at FROM messages "
                "WHERE session_id = :sid AND user_id = :uid ORDER BY id DESC LIMIT :lim"),
                {"sid": session_id, "uid": user_id, "lim": limit})
            rows = list(res)[::-1]
            return [Message(role=r.role, content=r.content, task_id=r.task_id, created_at=r.created_at) for r in rows]

    async def append(self, session_id, user_id, message):
        from sqlalchemy import text

        async with self.Session() as s:
            await s.execute(text(
                "INSERT INTO messages (session_id, user_id, role, content, task_id) "
                "VALUES (:sid, :uid, :role, :content, :task)"),
                {"sid": session_id, "uid": user_id, "role": message.role, "content": message.content,
                 "task": message.task_id})
            await s.commit()

    async def audit(self, event):
        from sqlalchemy import text

        async with self.Session() as s:
            await s.execute(text("INSERT INTO audit_log (event) VALUES (CAST(:e AS jsonb))"),
                            {"e": json.dumps(event, default=str)})
            await s.commit()
