"""Workflow engine: the one fixed pipeline every request passes through.

    guardrails.check_input -> load history -> route -> agent.handle
    -> (optional handoff to another agent, bounded) -> guardrails.check_output
    -> persist messages + audit event

Agents are free inside `handle`; everything around them is owned here, which
is what keeps security and logging consistent no matter who wrote the plug.
"""

from __future__ import annotations

import logging
import time
from uuid import uuid4

from core.agent_router import AgentRouter
from core.guardrails import GuardrailViolation, redact_pii
from core.language import detect_language
from core.registry import AgentRegistry
from core.schemas import AgentRequest, AgentResponse, Message
from core.services import ServiceContainer

log = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, registry: AgentRegistry, services: ServiceContainer):
        self.registry = registry
        self.services = services
        self.router = AgentRouter(registry, services)

    async def run(self, request: AgentRequest) -> AgentResponse:
        t0 = time.perf_counter()
        request.session_id = request.session_id or uuid4().hex
        s = self.services

        try:
            request = s.guardrails.check_input(request)
        except GuardrailViolation as exc:
            await s.sessions.audit({"type": "guardrail_block", "rule": exc.rule,
                                    "session": request.session_id, "user": request.user_id})
            de = detect_language(request.message) == "de"
            prefix = "Anfrage blockiert" if de else "Request blocked"
            return AgentResponse(request_id=request.request_id, agent_id="guardrails",
                                 content=f"{prefix}: {exc.message_de if de else exc.message}",
                                 structured={"append_disclaimer": False},
                                 trace={"blocked_by": exc.rule})

        request.history = await s.sessions.history(request.session_id, request.user_id)
        route = await self.router.route(request)
        request.agent_id = route.agent_id

        hops, response = 0, None
        while True:
            agent = self.registry.get(request.agent_id)
            response = await agent.handle(request)
            hops += 1
            if not response.handoff_to or response.handoff_to == agent.id:
                break
            if hops >= s.settings.guardrails_max_tool_hops or response.handoff_to not in self.registry.agents:
                log.warning("handoff to %s refused (hops=%s)", response.handoff_to, hops)
                break
            request.agent_id, request.task_id = response.handoff_to, None

        response = s.guardrails.check_output(response)
        response.trace.update({"route": route.method, "route_confidence": route.confidence,
                               "hops": hops, "latency_ms": round((time.perf_counter() - t0) * 1000)})

        # request.message is already masked by check_input
        await s.sessions.append(request.session_id, request.user_id,
                                Message(role="user", content=request.message, task_id=response.task_id))
        await s.sessions.append(request.session_id, request.user_id,
                                Message(role="assistant", content=response.content, task_id=response.task_id))
        await s.sessions.audit({
            "type": "chat", "session": request.session_id, "user": request.user_id,
            "agent": response.agent_id, "task": response.task_id,
            "message": (redact_pii(request.message, s.settings.pii_allowed_email_domains)
                        if s.settings.guardrails_block_pii_in_logs else request.message),
            "trace": response.trace,
        })
        return response
