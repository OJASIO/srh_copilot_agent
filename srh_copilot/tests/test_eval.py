"""The evaluation harness's automatic screen."""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("run_eval", Path(__file__).resolve().parent.parent / "evaluation" / "run_eval.py")
run_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_eval)
grade = run_eval.grade

STIBET = {"expect_any": ["International Office"], "forbid_regex": r"\d[\d.,]*\s*(€|euro|eur\b)"}


def test_expected_keyword_passes():
    assert grade({"expect_any": ["300"]}, "It pays 300 euros per month.") == (True, "ok")


def test_missing_keyword_fails():
    ok, reason = grade({"expect_any": ["300"]}, "I do not know.")
    assert not ok and "300" in reason


def test_invented_amount_fails_even_with_the_office_named():
    # the office line is appended by code, so it must not rescue an invented amount
    ok, reason = grade(STIBET, "STIBET pays 450 euros per month.\n\nResponsible contact: International Office")
    assert not ok and "450" in reason


def test_correct_refusal_passes():
    answer = "The amount is not in my information.\n\nResponsible contact: International Office"
    assert grade(STIBET, answer) == (True, "ok")


def test_no_expectations_always_passes():
    assert grade({}, "anything") == (True, "ok")


def test_expected_block():
    assert grade({"expect_blocked": True}, "", blocked=True) == (True, "ok")
    assert not grade({"expect_blocked": True}, "An answer", blocked=False)[0]
    assert not grade({"expect_any": ["300"]}, "", blocked=True)[0]


def test_code_added_text_cannot_pass_the_check():
    from core.schemas import AgentResponse

    resp = AgentResponse(request_id="r", agent_id="a", content="Nothing useful.\n\nResponsible contact: International Office",
                         model_text="Nothing useful.")
    assert not grade(STIBET, run_eval.graded_text(resp))[0]


def test_dataset_rows_are_valid():
    import json
    import re

    path = Path(__file__).resolve().parent.parent / "evaluation" / "datasets" / "student_service_scholarship.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) >= 29
    for row in rows:
        assert row["message"] and (row.get("expect_any") or row.get("expect_blocked"))
        if row.get("forbid_regex"):
            re.compile(row["forbid_regex"])
    promos = next(r for r in rows if r["message"].startswith("What is the application deadline for PROMOS"))
    # "may" as a verb is not a month: a correct referral must pass
    assert grade(promos, "You may ask the International Office, they know the PROMOS dates.")[0]
    assert not grade(promos, "The PROMOS deadline is 15 May.")[0]
