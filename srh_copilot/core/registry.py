"""Agent registry: discovers plugs on disk and loads the enabled ones.

Plug discovery rule:
    agents/<id>/manifest.yaml   must exist and its `id` must equal the folder name
    agents/<id>/agent.py        must define `Agent` (subclass of BaseAgent)

Enable/disable via config/agents.yaml or ENABLED_AGENTS=a,b,c. A folder that is
present but not enabled is reported as "available" so the admin endpoint can
show it, but it is never imported, so a broken unplugged agent cannot crash
the platform.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path

import yaml

from config.settings import PROJECT_ROOT, Settings
from core.agent_base import BaseAgent
from core.schemas import AgentInfo, AgentManifest
from core.services import ServiceContainer

log = logging.getLogger(__name__)
AGENTS_DIR = PROJECT_ROOT / "agents"


class AgentRegistry:
    def __init__(self, services: ServiceContainer, agents_dir: Path = AGENTS_DIR):
        self.services = services
        self.agents_dir = agents_dir
        self.agents: dict[str, BaseAgent] = {}
        self.available: dict[str, AgentManifest] = {}
        self.load_errors: dict[str, str] = {}

    # discovery

    def discover(self) -> dict[str, AgentManifest]:
        found: dict[str, AgentManifest] = {}
        for folder in sorted(self.agents_dir.iterdir()):
            mf = folder / "manifest.yaml"
            if not folder.is_dir() or not mf.exists():
                continue
            try:
                data = yaml.safe_load(mf.read_text(encoding="utf-8")) or {}
                manifest = AgentManifest(**data)
            except Exception as exc:  # malformed manifest must not kill the app
                self.load_errors[folder.name] = f"manifest error: {exc}"
                log.error("agent %s: %s", folder.name, exc)
                continue
            if manifest.id != folder.name:
                self.load_errors[folder.name] = f"manifest id {manifest.id!r} != folder name"
                continue
            found[manifest.id] = manifest
        self.available = found
        return found

    def enabled_ids(self, settings: Settings) -> list[str]:
        if settings.enabled_agents:
            return list(settings.enabled_agents)
        cfg = settings.agents_config_file
        if cfg.exists():
            data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
            return list(data.get("enabled") or [])
        return list(self.available)

    def overrides(self, settings: Settings) -> dict[str, dict]:
        cfg = settings.agents_config_file
        if cfg.exists():
            data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
            return dict(data.get("overrides") or {})
        return {}

    # loading

    def load(self) -> dict[str, BaseAgent]:
        settings = self.services.settings
        self.discover()
        overrides = self.overrides(settings)
        for agent_id in self.enabled_ids(settings):
            manifest = self.available.get(agent_id)
            if manifest is None:
                self.load_errors[agent_id] = "enabled but no valid manifest found"
                log.error("agent %s enabled but not found under %s", agent_id, self.agents_dir)
                continue
            try:
                module = importlib.import_module(f"agents.{agent_id}.agent")
                cls = getattr(module, "Agent")
                if not (isinstance(cls, type) and issubclass(cls, BaseAgent)):
                    raise TypeError("agent.py must define class Agent(BaseAgent)")
                self.agents[agent_id] = cls(manifest, self.services, overrides.get(agent_id))
                log.info("plugged agent %s v%s with tasks %s", agent_id, manifest.version,
                         [t.id for t in manifest.tasks])
            except Exception as exc:
                self.load_errors[agent_id] = f"load error: {exc}"
                log.exception("failed to load agent %s", agent_id)
        return self.agents

    def unplug(self, agent_id: str) -> bool:
        return self.agents.pop(agent_id, None) is not None

    def plug(self, agent_id: str) -> BaseAgent:
        """Hot-plug an agent at runtime (admin endpoint)."""
        self.discover()
        manifest = self.available[agent_id]
        module = importlib.import_module(f"agents.{agent_id}.agent")
        module = importlib.reload(module)
        agent = module.Agent(manifest, self.services, self.overrides(self.services.settings).get(agent_id))
        self.agents[agent_id] = agent
        return agent

    # views

    def get(self, agent_id: str) -> BaseAgent | None:
        return self.agents.get(agent_id)

    def infos(self) -> list[AgentInfo]:
        return [AgentInfo(id=a.id, name=a.manifest.name, description=a.manifest.description,
                          version=a.manifest.version, tasks=a.manifest.tasks) for a in self.agents.values()]
