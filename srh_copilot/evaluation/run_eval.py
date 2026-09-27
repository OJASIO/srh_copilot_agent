"""Evaluation harness: replays a JSONL dataset through the orchestrator, checks each
answer, and saves every answer for human grading.

    python evaluation/run_eval.py evaluation/datasets/student_service_scholarship.jsonl
    python evaluation/run_eval.py <dataset> --out evaluation/results/my_run

Dataset line format:
    {"message": "...", "agent_id": "student_service", "task_id": "scholarship_info",
     "expect_any": ["Deutschlandstipendium", "scholarship.hsg@srh.de"],
     "forbid_regex": "\\d+\\s*(€|euro)",
     "setup": ["What is the Deutschlandstipendium?"],
     "expect_blocked": false}

expect_any      the answer passes if it contains at least one of these (case-insensitive)
forbid_regex    the answer fails if this matches, e.g. an invented amount for a question
                the knowledge base cannot answer
setup           earlier messages sent first in the same session; the last message is the
                follow-up that is graded (tests retrieval and history for follow-ups)
expect_blocked  the input guardrail must block the message (prompt injection tests)

The automatic check runs on the model's own text (`model_text`), not on the final
answer: the contact block and the disclaimer are added by code and would otherwise
satisfy "expect_any" even when the model answered nothing useful.

Each question runs in its own session, so earlier questions never leak in as history.
Output: <out>.jsonl (machine-readable) and <out>.md (one block per answer, with a line to
grade it by hand: correct / partly / wrong). The keyword check is a quick screen; the human
grades are the accuracy number to report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import settings  # noqa: E402
from core.orchestrator import Orchestrator  # noqa: E402
from core.registry import AgentRegistry  # noqa: E402
from core.schemas import AgentRequest, AgentResponse  # noqa: E402
from core.services import ServiceContainer  # noqa: E402


def graded_text(resp: AgentResponse) -> str:
    return resp.model_text if resp.model_text is not None else resp.content


def grade(row: dict, answer: str, blocked: bool = False) -> tuple[bool, str]:
    """Automatic screen for one answer. Returns (passed, reason)."""
    if row.get("expect_blocked"):
        return (True, "ok") if blocked else (False, "expected the guardrail to block this message")
    if blocked:
        return False, "blocked by the guardrail"
    text = answer.lower()
    forbid = row.get("forbid_regex")
    if forbid:
        m = re.search(forbid, answer, re.I)
        if m:
            return False, f"forbidden content: {m.group(0)!r}"
    expected = row.get("expect_any") or []
    if expected and not any(k.lower() in text for k in expected):
        return False, f"none of {expected} found"
    return True, "ok"


async def main(path: Path, out: Path) -> None:
    services = ServiceContainer.build(settings)
    await services.startup()
    registry = AgentRegistry(services)
    registry.load()
    orch = Orchestrator(registry, services)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    results = []
    for i, row in enumerate(rows, 1):
        session = f"eval-{i}-{uuid4().hex[:8]}"

        def request(message: str) -> AgentRequest:
            return AgentRequest(session_id=session, user_id="eval", message=message,
                                agent_id=row.get("agent_id"), task_id=row.get("task_id"))

        for earlier in row.get("setup") or []:
            await orch.run(request(earlier))
        t0 = time.perf_counter()
        resp = await orch.run(request(row["message"]))
        latency = time.perf_counter() - t0
        blocked = resp.agent_id == "guardrails"
        passed, reason = grade(row, graded_text(resp), blocked)
        results.append({"id": i, "message": row["message"], "setup": row.get("setup") or [], "passed": passed,
                        "reason": reason, "expect_any": row.get("expect_any", []),
                        "forbid_regex": row.get("forbid_regex"), "expect_blocked": bool(row.get("expect_blocked")),
                        "route": f"{resp.agent_id}/{resp.task_id}", "latency_ms": round(latency * 1000),
                        "citations": [c.source for c in resp.citations], "model_text": graded_text(resp),
                        "answer": resp.content})
        mark = "ok  " if passed else "MISS"
        print(f"[{mark}] {i:2d} {row['message'][:58]!r} -> {resp.agent_id}/{resp.task_id}"
              + ("" if passed else f"   ({reason})"))

    n = len(results) or 1
    passed = sum(r["passed"] for r in results)
    answered = [r for r in results if not r["expect_blocked"]]
    lat = sorted(r["latency_ms"] for r in answered) or [0]
    p95 = lat[min(len(lat) - 1, int(round(0.95 * (len(lat) - 1))))]
    summary = (f"automatic check passed {passed}/{len(results)} ({passed / n:.0%}), graded on the model's own text; "
               f"avg latency {sum(lat) / len(lat):.0f} ms  p95 {p95} ms over {len(answered)} answered questions "
               f"(llm={settings.llm_provider}, embeddings={settings.embedding_backend})")
    print("\n" + summary)

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.with_suffix(".jsonl").open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    md = [f"# Evaluation run {out.name}", "", f"Dataset: `{path}`", "", summary, "",
          "Grade each answer by replacing the `Grade:` line with correct, partly or wrong.", ""]
    for r in results:
        md += [f"## {r['id']}. {r['message']}", ""]
        if r["setup"]:
            md += ["Earlier in the same session: " + " / ".join(r["setup"]), ""]
        md += [f"Automatic check: {'passed' if r['passed'] else 'FAILED'} ({r['reason']}); "
               f"{r['latency_ms']} ms; route {r['route']}", "",
               "```text", r["answer"].strip(), "```", "",
               "Grade: ", ""]
    out.with_suffix(".md").write_text("\n".join(md), encoding="utf-8")
    print(f"answers saved: {out.with_suffix('.md')} (for grading) and {out.with_suffix('.jsonl')}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset", type=Path)
    ap.add_argument("--out", type=Path, default=None, help="output path without extension")
    args = ap.parse_args()
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out = args.out or Path("evaluation/results") / f"{args.dataset.stem}_{settings.llm_provider}_{stamp}"
    asyncio.run(main(args.dataset, out))
