"""A generic RAG task. Most new agents can start by copying this file."""
from core.agent_base import BaseTask
from core.schemas import AgentRequest, AgentResponse


class AnswerQuestionTask(BaseTask):
    id = "answer_question"

    async def run(self, request: AgentRequest) -> AgentResponse:
        s = self.services
        hits = await s.retriever.search(request.message, self.agent.manifest.knowledge_collections,
                                        top_k=self.agent.setting("retrieval_top_k", 5))
        system = s.prompts.get("system", agent_id=self.agent.id).render(context=s.retriever.as_context(hits))
        messages = [{"role": "system", "content": system}]
        messages += [{"role": m.role, "content": m.content} for m in request.history[-6:]]
        messages.append({"role": "user", "content": request.message})
        answer = await s.llm.chat(messages)
        return AgentResponse(request_id=request.request_id, agent_id=self.agent.id, task_id=self.id,
                             content=answer, citations=s.retriever.as_citations(hits))
