"""Prompt management. Prompts live as files, not as string literals in code,
so they can be reviewed, versioned and A/B tested without touching Python.

Layout:
    core/prompts/<name>.md                 global prompts (router, guardrails)
    agents/<agent_id>/prompts/<name>.md    agent-specific prompts

A prompt file may start with a YAML front matter block:
    ---
    version: 2
    description: system prompt for scholarship answers
    ---
Placeholders use {name} and are filled with .format(**kwargs).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from config.settings import PROJECT_ROOT

log = logging.getLogger(__name__)
_FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


@dataclass
class Prompt:
    name: str
    text: str
    meta: dict

    def render(self, **kwargs) -> str:
        """Simple placeholder substitution. Only {name} tokens that match a
        kwarg are replaced, so JSON examples with braces stay intact."""
        out = self.text
        for k, v in kwargs.items():
            out = out.replace("{" + k + "}", str(v))
        return out


class PromptStore:
    def __init__(self, root: Path = PROJECT_ROOT):
        self.root = root
        self._cache: dict[str, Prompt] = {}

    def get(self, name: str, agent_id: str | None = None) -> Prompt:
        key = f"{agent_id or 'core'}:{name}"
        if key in self._cache:
            return self._cache[key]
        base = self.root / "agents" / agent_id / "prompts" if agent_id else self.root / "core" / "prompts"
        path = base / f"{name}.md"
        if not path.exists():
            raise FileNotFoundError(f"prompt {name!r} not found at {path}")
        raw = path.read_text(encoding="utf-8")
        meta: dict = {}
        m = _FRONT.match(raw)
        if m:
            meta = yaml.safe_load(m.group(1)) or {}
            raw = raw[m.end():]
        prompt = Prompt(name=name, text=raw.strip(), meta=meta)
        self._cache[key] = prompt
        return prompt

    def clear(self):
        self._cache.clear()

    def all_texts(self) -> list[str]:
        """Text of every prompt file (core and all agents). The output guardrail uses
        it to notice an answer that repeats a prompt instead of answering."""
        paths = list((self.root / "core" / "prompts").glob("*.md"))
        paths += list((self.root / "agents").glob("*/prompts/*.md"))
        texts = []
        for path in sorted(paths):
            raw = path.read_text(encoding="utf-8")
            m = _FRONT.match(raw)
            texts.append(raw[m.end():] if m else raw)
        return texts
