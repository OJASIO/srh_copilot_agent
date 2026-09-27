"""Shared data contracts between the API, the orchestrator and the agents.
Agents never see FastAPI objects; they only receive and return these models."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Attachment(BaseModel):
    """A file the user uploaded with the message (e.g. a CV)."""

    filename: str
    content_type: str
    data: bytes = Field(repr=False)


class TaskField(BaseModel):
    """An extra input a task asks for, rendered by the UI from the manifest (no
    task-specific UI code). Values arrive in AgentRequest.inputs under `id`."""

    id: str
    label: str
    type: Literal["text", "textarea", "select"] = "text"
    options: list[str] = []
    option_labels: dict[str, str] = {}
    default: str = ""
    help: str = ""


class TaskSpec(BaseModel):
    """One selectable capability of an agent, shown as a choice in the UI."""

    id: str
    name: str
    description: str = ""
    accepts_files: bool = False
    file_types: list[str] = []
    input_hint: str = ""
    submit_label: str = ""  # button that sends an uploaded file without typing a message
    fields: list[TaskField] = []


class AgentManifest(BaseModel):
    """Loaded from agents/<id>/manifest.yaml. Describes the plug to the core."""

    id: str
    name: str
    description: str = ""
    version: str = "0.1.0"
    owner: str = ""
    tasks: list[TaskSpec] = []
    routing_keywords: list[str] = []
    knowledge_collections: list[str] = []
    default_task: str | None = None


class Message(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str
    task_id: str | None = None  # which task produced or received it; lets a task read only its own turns
    created_at: datetime = Field(default_factory=_now)


class AgentRequest(BaseModel):
    """What an agent receives for one turn."""

    request_id: str = Field(default_factory=lambda: uuid4().hex)
    session_id: str
    user_id: str
    agent_id: str | None = None
    task_id: str | None = None
    message: str
    history: list[Message] = []
    attachments: list[Attachment] = []
    language: str = "auto"
    inputs: dict[str, str] = {}  # values of the task's declared fields (TaskSpec.fields)
    metadata: dict[str, Any] = {}


class Citation(BaseModel):
    source: str
    snippet: str = ""
    score: float | None = None


class AgentResponse(BaseModel):
    """What an agent returns. The orchestrator adds trace info and stores it."""

    request_id: str
    agent_id: str
    task_id: str | None = None
    content: str
    # The model's own text before code added anything (contact block, disclaimer).
    # Not sent to clients; the evaluation grades this so appended text cannot pass a check.
    model_text: str | None = None
    citations: list[Citation] = []
    handoff_to: str | None = None
    confidence: float | None = None
    structured: dict[str, Any] = {}
    trace: dict[str, Any] = {}


MAX_INPUT_FIELDS = 10


def validate_inputs(value: dict[str, str]) -> dict[str, str]:
    """Shape check for task inputs: at most 10 short keys with string values.
    Value length is enforced by the input guardrail (MAX_INPUT_CHARS)."""
    if len(value) > MAX_INPUT_FIELDS:
        raise ValueError(f"at most {MAX_INPUT_FIELDS} input fields")
    for key in value:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key):
            raise ValueError(f"invalid input field name {key!r}")
    return value


class ChatRequest(BaseModel):
    """Public API payload (POST /chat)."""

    message: str = Field(min_length=1)
    session_id: str | None = Field(default=None, max_length=64)
    agent_id: str | None = None
    task_id: str | None = None
    language: str = "auto"
    inputs: dict[str, str] = {}

    @field_validator("inputs")
    @classmethod
    def _check_inputs(cls, v: dict[str, str]) -> dict[str, str]:
        return validate_inputs(v)


class ChatResponse(BaseModel):
    session_id: str
    agent_id: str
    task_id: str | None
    content: str
    citations: list[Citation] = []
    structured: dict[str, Any] = {}
    trace: dict[str, Any] = {}


class AgentInfo(BaseModel):
    id: str
    name: str
    description: str
    version: str
    tasks: list[TaskSpec]
