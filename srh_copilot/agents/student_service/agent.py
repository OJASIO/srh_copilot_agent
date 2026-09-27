"""Student Service agent: one plug, two tasks.

    cv_check          logic from the teammate's CV Optimizer Agent, ported into cv_check/ (tasks/cv_check.py wires it)
    scholarship_info  owned by Subodh (tasks/scholarship_info.py)

The base class dispatches on request.task_id, which the frontend sets from the
task selector. When no task is selected, choose_task() decides: an attached
file means CV check, otherwise a small keyword rule, otherwise the LLM.
"""

from __future__ import annotations

from core.agent_base import BaseAgent
from core.providers import parse_json
from core.schemas import AgentRequest
from agents.student_service.tasks.cv_check import CvCheckTask
from agents.student_service.tasks.scholarship_info import ScholarshipInfoTask

_SCHOLARSHIP_WORDS = ("scholarship", "stipend", "funding", "financ", "bafög", "bafoeg", "grant", "förder")
_CV_WORDS = ("cv", "resume", "lebenslauf", "cover letter", "anschreiben", "application document")


class Agent(BaseAgent):
    def task_classes(self):
        return [CvCheckTask, ScholarshipInfoTask]

    async def choose_task(self, request: AgentRequest) -> str:
        if request.attachments:
            return "cv_check"
        text = request.message.lower()
        if any(w in text for w in _SCHOLARSHIP_WORDS):
            return "scholarship_info"
        if any(w in text for w in _CV_WORDS):
            return "cv_check"
        if self.services.settings.llm_provider != "mock":
            prompt = self.services.prompts.get("task_router", agent_id=self.id).render(message=request.message)
            raw = await self.services.llm.chat([{"role": "system", "content": prompt},
                                                {"role": "user", "content": request.message}],
                                               temperature=0, json_mode=True)
            task = parse_json(raw).get("task")
            if task in self.tasks:
                return task
        return "scholarship_info"
