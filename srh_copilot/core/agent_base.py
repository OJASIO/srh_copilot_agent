"""Base classes every plug implements.

A domain agent = one folder under agents/ with:
    manifest.yaml   describes id, name, tasks, routing keywords
    agent.py        exposes `Agent(BaseAgent)`
    tasks/          one BaseTask subclass per selectable task (optional but
                    recommended when an agent does more than one thing)

The core only talks to BaseAgent.handle(). How an agent solves its task
(RAG, tool calls, another framework, a remote service) is its own business.
"""

from __future__ import annotations

import abc
import logging
from typing import TYPE_CHECKING

from core.schemas import AgentManifest, AgentRequest, AgentResponse, TaskSpec

if TYPE_CHECKING:  # avoids import cycles at runtime
    from core.services import ServiceContainer

log = logging.getLogger(__name__)


class BaseTask(abc.ABC):
    """A single capability inside an agent (e.g. 'cv_check')."""

    id: str = ""

    def __init__(self, agent: "BaseAgent"):
        self.agent = agent
        self.services = agent.services

    @property
    def spec(self) -> TaskSpec:
        for t in self.agent.manifest.tasks:
            if t.id == self.id:
                return t
        raise KeyError(f"task {self.id!r} is not declared in manifest of {self.agent.id}")

    @abc.abstractmethod
    async def run(self, request: AgentRequest) -> AgentResponse: ...


class BaseAgent(abc.ABC):
    """Contract for a pluggable domain agent."""

    def __init__(self, manifest: AgentManifest, services: "ServiceContainer", overrides: dict | None = None):
        self.manifest = manifest
        self.services = services
        self.overrides = overrides or {}
        self.tasks: dict[str, BaseTask] = {}
        for task_cls in self.task_classes():
            task = task_cls(self)
            self.tasks[task.id] = task
        declared = {t.id for t in manifest.tasks}
        missing = declared - set(self.tasks)
        if missing:
            log.warning("agent %s declares tasks without implementation: %s", self.id, sorted(missing))

    # Plug authors override these two.

    def task_classes(self) -> list[type[BaseTask]]:
        """Return the BaseTask subclasses this agent provides."""
        return []

    async def handle(self, request: AgentRequest) -> AgentResponse:
        """Default behaviour: dispatch to the selected task. Agents with
        custom flows (single-task agents, agent graphs) can override this."""
        task_id = request.task_id or self.manifest.default_task
        if task_id is None and len(self.tasks) == 1:
            task_id = next(iter(self.tasks))
        if task_id is None:
            task_id = await self.choose_task(request)
        task = self.tasks.get(task_id)
        if task is None:
            raise ValueError(f"agent {self.id} has no task {task_id!r}")
        request.task_id = task_id
        response = await task.run(request)
        response.agent_id = self.id
        response.task_id = task_id
        return response

    async def choose_task(self, request: AgentRequest) -> str:
        """Pick a task when the user did not select one. Default: keyword
        match on the task descriptions, then the first declared task."""
        text = request.message.lower()
        for spec in self.manifest.tasks:
            words = {w for w in (spec.name + " " + spec.description).lower().split() if len(w) > 3}
            if any(w in text for w in words):
                return spec.id
        return self.manifest.tasks[0].id

    # Convenience

    @property
    def id(self) -> str:
        return self.manifest.id

    def setting(self, key: str, default=None):
        """Per-agent override from config/agents.yaml, else global setting."""
        if key in self.overrides:
            return self.overrides[key]
        return getattr(self.services.settings, key, default)

    async def healthcheck(self) -> dict:
        return {"agent": self.id, "tasks": list(self.tasks), "status": "ok"}
