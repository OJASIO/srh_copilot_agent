"""Scholarship Information task (Subodh's part).

Flow: retrieve from the student_service/scholarship collection -> answer with
the LLM strictly from that context -> attach the responsible contact so the
student can act even when the answer is incomplete.

Retrieval uses the question and, for a follow-up ("and the deadline for it?"),
the question together with the previous one. Only this task's earlier turns are
used as history, so a CV review in the same session never leaks into the prompt.

The contact block is rule-based: each office has the programme names and topics it
is responsible for (hotline FAQ sections 8 and 9, SRH financing page). Matching
runs on the question and the answer with email addresses and links removed, so an
"@srh.de" address in the answer no longer pulls in every office.

Knowledge comes from data/raw/student_service/scholarship/*.md, ingested with
`python scripts/ingest.py --agent student_service`.
"""

from __future__ import annotations

import re

from core.agent_base import BaseTask
from core.language import detect_language, today_text
from core.schemas import AgentRequest, AgentResponse

# office id: (name, contact, patterns). Order is the display order.
_OFFICES: dict[str, tuple[str, str, list[str]]] = {
    "career_service": ("Career Service & Development", "scholarship.hsg@srh.de", [
        r"deutschlandstipendi\w*", r"besonderen lebenslagen", r"special (life )?situations?",
        r"external scholarships?", r"externe[nr]? stipendi\w*", r"valucon",
    ]),
    "international_office": ("International Office", "international.hsg@srh.de / scholarshipexchange.hsg@srh.de", [
        r"stibet", r"erasmus", r"promos", r"baw[üu]e?\b", r"baw[üu]e?[- ]stipendi\w*",
        r"baden-w[üu]e?rttemberg[- ]stipendi\w*", r"haw\.?\s?international", r"exchange scholarships?",
        r"austauschstipendi\w*", r"semester abroad", r"auslandssemester", r"study abroad", r"studium im ausland",
        r"internship abroad", r"auslandspraktikum",
    ]),
    "admission": ("Admission", "apply.hsg@srh.de", [
        r"performance scholarships?", r"entrepreneurship scholarships?", r"women for leadership",
        r"talent scholarships?", r"women in sound", r"future of tech", r"women in tech",
        r"srh[- ]scholarships?\b(?! overview)", r"srh[- ]stipendi(?:um|en)\b(?! f[üu]r begabte)",
        r"fee reduction", r"first[- ]year tuition",
        r"gebührenreduzierung", r"ielts",
    ]),
    "examination_office": ("Examination Office",
                           "your campus Examination Office (Student Service tells you the address: "
                           "student-service.hsg@srh.de)", [
        r"studienstiftung", r"german academic scholarship foundation", r"formblatt 5",
    ]),
    "student_service": ("Student Service (BAföG forms)", "student-service.hsg@srh.de", [
        r"baf[öo]e?g", r"formblatt",
    ]),
}
_COMPILED = {k: [re.compile(rf"\b{p}", re.I) for p in v[2]] for k, v in _OFFICES.items()}
_NOISE = re.compile(r"\S+@\S+|https?://\S+|www\.\S+")
_HEADING = {"en": "Responsible contact:", "de": "Zuständiger Kontakt:"}
_FALLBACK = {
    "en": ["- Student Service (general questions): student-service.hsg@srh.de",
           "- Financing overview: https://www.srh-university.de/en/you-want-to-study/financing/"],
    "de": ["- Student Service (allgemeine Fragen): student-service.hsg@srh.de",
           "- Überblick Finanzierung: https://www.srh-university.de/en/you-want-to-study/financing/"],
}


def contacts_for(question: str, answer: str, lang: str = "en", previous: str = "") -> str:
    """`previous` is the student's earlier question in a follow-up ("and the deadline?")."""
    text = _NOISE.sub(" ", f"{previous}\n{question}\n{answer}")
    lines = [f"- {name}: {contact}" for office, (name, contact, _) in _OFFICES.items()
             if any(rx.search(text) for rx in _COMPILED[office])]
    lines = lines or _FALLBACK.get(lang, _FALLBACK["en"])
    return _HEADING.get(lang, _HEADING["en"]) + "\n" + "\n".join(lines)


class ScholarshipInfoTask(BaseTask):
    id = "scholarship_info"
    collection = "student_service/scholarship"

    async def run(self, request: AgentRequest) -> AgentResponse:
        s = self.services
        turns = [m for m in request.history if m.task_id in (None, self.id)]
        previous = next((m.content for m in reversed(turns) if m.role == "user"), "")
        queries = [request.message] + ([f"{previous}\n{request.message}"] if previous else [])
        hits = await s.retriever.search_many(queries, [self.collection],
                                             top_k=self.agent.setting("retrieval_top_k", 5))
        context = s.retriever.as_context(hits)
        lang = detect_language(request.message)
        system = s.prompts.get("scholarship_system", agent_id=self.agent.id).render(
            today=today_text("en"), context=context)

        messages = [{"role": "system", "content": system}]
        messages += [{"role": m.role, "content": m.content} for m in turns[-6:]]
        messages.append({"role": "user", "content": request.message})
        answer = await s.llm.chat(messages)

        content = f"{answer}\n\n{contacts_for(request.message, answer, lang, previous)}"
        return AgentResponse(
            request_id=request.request_id, agent_id=self.agent.id, task_id=self.id,
            content=content, model_text=str(answer), citations=s.retriever.as_citations(hits),
            confidence=max((h.score for h in hits), default=None),
            structured={"retrieved": len(hits), "language": lang},
        )
