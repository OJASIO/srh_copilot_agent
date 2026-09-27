"""Minimal plug. The core imports `Agent` from this module."""
from core.agent_base import BaseAgent
from agents.future_agent_template.tasks.answer_question import AnswerQuestionTask


class Agent(BaseAgent):
    def task_classes(self):
        return [AnswerQuestionTask]
