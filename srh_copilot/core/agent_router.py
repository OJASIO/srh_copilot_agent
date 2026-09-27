"""Routing engine: decides which agent handles a request.

Order of precedence:
    1. explicit agent_id from the client (the UI selector). Always wins.
    2. keyword match against each manifest's routing_keywords.
    3. LLM classification using core/prompts/router.md (skipped for mock LLM).
    4. fallback: the first enabled agent, flagged low confidence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from core.providers import parse_json
from core.registry import AgentRegistry
from core.schemas import AgentRequest
from core.services import ServiceContainer

log = logging.getLogger(__name__)


@dataclass
class RoutingDecision:
    agent_id: str
    method: str
    confidence: float


class AgentRouter:
    def __init__(self, registry: AgentRegistry, services: ServiceContainer):
        self.registry = registry
        self.services = services

    async def route(self, request: AgentRequest) -> RoutingDecision:
        agents = self.registry.agents
        if not agents:
            raise RuntimeError("no agents are plugged in")

        if request.agent_id:
            if request.agent_id not in agents:
                raise ValueError(f"agent {request.agent_id!r} is not enabled")
            return RoutingDecision(request.agent_id, "explicit", 1.0)

        text = request.message.lower()
        scores = {aid: sum(1 for kw in a.manifest.routing_keywords if kw.lower() in text)
                  for aid, a in agents.items()}
        best = max(scores, key=scores.get)
        if scores[best] > 0:
            return RoutingDecision(best, "keywords", min(1.0, 0.5 + 0.1 * scores[best]))

        if self.services.settings.llm_provider != "mock" and len(agents) > 1:
            catalogue = "\n".join(f"- {a.id}: {a.manifest.description}" for a in agents.values())
            prompt = self.services.prompts.get("router").render(agents=catalogue, message=request.message)
            raw = await self.services.llm.chat(
                [{"role": "system", "content": prompt}, {"role": "user", "content": request.message}],
                temperature=0, json_mode=True)
            data = parse_json(raw)
            if data.get("agent_id") in agents:
                try:
                    confidence = float(data.get("confidence", 0.7))
                except (TypeError, ValueError):
                    confidence = 0.7
                return RoutingDecision(data["agent_id"], "llm", confidence)

        return RoutingDecision(next(iter(agents)), "fallback", 0.2)
