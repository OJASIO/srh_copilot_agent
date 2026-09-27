# How to add a new agent (plug)

1. Copy this folder: `cp -r agents/future_agent_template agents/<your_id>`
2. Edit `manifest.yaml`: set `id` (= folder name), name, description, routing_keywords,
   knowledge_collections and the list of tasks the UI should offer.
3. Implement one class per task in `tasks/` (subclass `core.agent_base.BaseTask`,
   set `id`, implement `async run(request) -> AgentResponse`).
4. List the task classes in `agent.py` -> `Agent.task_classes()`.
5. Put prompts in `prompts/*.md`, load them with `self.services.prompts.get(name, agent_id=self.agent.id)`.
6. Put source documents under `data/raw/<your_id>/<collection>/` and run
   `python scripts/ingest.py --agent <your_id>`.
7. Add `<your_id>` to `config/agents.yaml` -> `enabled`. Restart the API (or POST /admin/agents/<id>/plug).
8. Add a test in `tests/` and an eval set in `evaluation/datasets/<your_id>.jsonl`.

Unplug: remove the id from `config/agents.yaml`. The folder can stay.

Rules for a plug:
- Never read environment variables or build API clients yourself; use `self.services`.
- Never import from another agent's folder. Shared code goes into `core/`.
- Return citations for every answer that came from documents.
- Do not disable the output guardrail unless the task is a pure form/template.
