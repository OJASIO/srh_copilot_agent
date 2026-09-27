"""Streamlit chat client for the SRH AI Copilot API.

Run:  streamlit run frontend/streamlit_app.py
The frontend never talks to an LLM or a database; it only calls the API, so it
can be replaced by React or embedded in eCampus without touching the backend.

Everything task-specific comes from GET /agents: the task choice, the extra fields
a task declares in its manifest (for CV Check: review language, job description),
whether it takes a file and the label of the button that sends it. A new agent
therefore needs no UI code, except an optional result panel like the CV score row.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from pathlib import Path

import requests
import streamlit as st


def _config() -> dict:
    """Environment first, then the project's .env (the API reads the same file), so
    a real API_KEY set in .env works for the UI too."""
    values: dict = {}
    try:
        from dotenv import dotenv_values  # installed with pydantic-settings

        values = {k: v for k, v in dotenv_values(Path(__file__).resolve().parent.parent / ".env").items() if v}
    except Exception:
        pass
    return {k: os.getenv(k) or values.get(k, default) for k, default in
            (("COPILOT_API_URL", "http://localhost:8000"), ("API_KEY", "change-me"),
             ("DEMO_PASSWORD", ""), ("DEMO_BANNER", ""))}


CONFIG = _config()
API_URL = CONFIG["COPILOT_API_URL"]
# Hosted deployments set these two. Unset locally, so nothing changes in dev.
DEMO_PASSWORD = CONFIG["DEMO_PASSWORD"]
DEMO_BANNER = CONFIG["DEMO_BANNER"]

st.set_page_config(page_title="SRH AI Copilot", page_icon="🎓", layout="wide")


def require_password() -> None:
    """Shared-password gate for the public prototype. A hosted URL with a free
    LLM key is otherwise an open invitation to burn the quota. This protects the
    deployment, not the data: real per-user authentication is the API's job
    (JWT with roles) and belongs to the university SSO work."""
    if not DEMO_PASSWORD or st.session_state.get("unlocked"):
        return
    st.title("SRH AI Copilot")
    st.caption("Prototype access is password protected.")
    entered = st.text_input("Access password", type="password")
    if st.button("Enter"):
        if hmac.compare_digest(entered, DEMO_PASSWORD):
            st.session_state.unlocked = True
            st.rerun()
        st.error("Wrong password.")
    st.stop()


require_password()

# state
st.session_state.setdefault("session_id", uuid.uuid4().hex)
st.session_state.setdefault("client_id", uuid.uuid4().hex)  # rate limit per browser session
st.session_state.setdefault("messages", [])
st.session_state.setdefault("token", None)
st.session_state.setdefault("sent_uploads", set())  # (file hash, inputs) already reviewed in this session


def base_headers() -> dict:
    return {"X-API-Key": CONFIG["API_KEY"], "X-Client-Id": st.session_state.client_id}


@st.cache_data(ttl=30, show_spinner=False)
def fetch_agents(api_key: str) -> list[dict]:
    """Retry briefly: on a platform that sleeps idle instances, the API may
    still be starting when the first visitor arrives."""
    last: Exception | None = None
    for attempt in range(5):
        try:
            r = requests.get(f"{API_URL}/agents", headers={"X-API-Key": api_key}, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    raise last


def login(username: str) -> str | None:
    r = requests.post(f"{API_URL}/auth/token", data={"username": username}, headers=base_headers(), timeout=10)
    return r.json().get("access_token") if r.ok else None


def auth_headers() -> dict:
    h = base_headers()
    if st.session_state.get("token"):
        h["Authorization"] = f"Bearer {st.session_state.token}"
    return h


def new_conversation() -> None:
    st.session_state.messages, st.session_state.session_id = [], uuid.uuid4().hex
    st.session_state.sent_uploads = set()


def render_field(task_id: str, f: dict) -> str:
    key = f"field_{task_id}_{f['id']}"
    if f.get("type") == "select" and f.get("options"):
        options = f["options"]
        labels = f.get("option_labels") or {}
        index = options.index(f["default"]) if f.get("default") in options else 0
        return st.selectbox(f["label"], options, index=index, key=key, help=f.get("help") or None,
                            format_func=lambda o: labels.get(o, o))
    if f.get("type") == "textarea":
        return st.text_area(f["label"], value=f.get("default", ""), key=key, help=f.get("help") or None, height=150)
    return st.text_input(f["label"], value=f.get("default", ""), key=key, help=f.get("help") or None)


# sidebar: who, which agent, which task
with st.sidebar:
    st.title("SRH AI Copilot")
    user = st.selectbox("Demo user", ["student", "staff", "hr", "admin"])
    if st.button("Sign in"):
        st.session_state.token = login(user)
        new_conversation()
    st.caption("signed in" if st.session_state.token else "anonymous (dev mode)")

    try:
        with st.spinner("Connecting to the API"):
            agents = fetch_agents(CONFIG["API_KEY"])
    except Exception as exc:
        st.error(f"API not reachable at {API_URL}: {exc}")
        st.stop()

    options = {"auto": "Automatic routing"} | {a["id"]: a["name"] for a in agents}
    agent_id = st.selectbox("Agent", list(options), format_func=options.get)
    agent = next((a for a in agents if a["id"] == agent_id), None)

    task_id, task = None, None
    if agent and len(agent["tasks"]) > 1:
        st.markdown("**What do you need?**")
        task_id = st.radio("Task", [t["id"] for t in agent["tasks"]],
                           format_func=lambda tid: next(t["name"] for t in agent["tasks"] if t["id"] == tid),
                           label_visibility="collapsed")
    elif agent and agent["tasks"]:
        task_id = agent["tasks"][0]["id"]
    if agent:
        task = next((t for t in agent["tasks"] if t["id"] == task_id), None)
        if task:
            st.caption(task["description"])

    inputs: dict[str, str] = {}
    if task:
        for f in task.get("fields") or []:
            value = render_field(task["id"], f)
            if value and value.strip():
                inputs[f["id"]] = value.strip()

    upload = None
    if task and task.get("accepts_files"):
        upload = st.file_uploader("Upload file", type=task.get("file_types") or None, key=f"upload_{task['id']}")
    submit = False
    if upload is not None:
        submit = st.button(task.get("submit_label") or "Send file", type="primary")

    if st.button("New conversation"):
        new_conversation()
        st.rerun()


def render_cv_findings(findings: dict):
    """Score panel for CV Check results. The markdown below it comes from the API."""
    review, ats = findings.get("review", {}), findings.get("ats", {})
    c1, c2, c3 = st.columns(3)
    c1.metric("CV score", f"{review.get('overall_score', 'n/a')}/10")
    c2.metric("ATS score", f"{ats.get('score', 'n/a')}/100", help=ats.get("recommendation", ""))
    c3.metric("Language", findings.get("language_label", "?"))
    critical = len(review.get("tier_1") or [])
    if critical:
        st.error(f"{critical} critical issue(s) to fix before a counselling appointment")
    else:
        st.success("No critical issues found")
    masked = (findings.get("privacy") or {}).get("total", 0)
    if masked:
        st.caption(f"{masked} personal detail(s) were masked before the AI review.")
    with st.expander("Structured findings (JSON)"):
        st.json(findings)


def render_assistant(m: dict):
    findings = (m.get("structured") or {}).get("findings")
    if findings and "review" in findings:
        render_cv_findings(findings)
    st.markdown(m["content"])
    if m.get("citations"):
        with st.expander("Sources"):
            for c in m["citations"]:
                st.markdown(f"- `{c['source']}` (score {c.get('score')})")


# main: chat
st.header(agent["name"] if agent else "SRH AI Copilot")
if DEMO_BANNER:
    st.warning(DEMO_BANNER)
if task:
    st.subheader(task["name"])
for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        if m["role"] == "assistant":
            render_assistant(m)
        else:
            st.markdown(m["content"])

placeholder = task["input_hint"] if task and task.get("input_hint") else "Ask a question"
prompt = st.chat_input(placeholder)
if submit and not prompt:
    prompt = f"{task.get('submit_label') or 'Send file'}: {upload.name}"

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    payload = {"message": prompt, "session_id": st.session_state.session_id,
               "agent_id": None if agent_id == "auto" else agent_id, "task_id": task_id, "inputs": inputs}
    # Send the file only once per (file, fields) combination: follow-up questions go
    # without it, so they do not re-run the whole review.
    upload_key = None
    if upload is not None:
        upload_key = (hashlib.sha256(upload.getvalue()).hexdigest(), json.dumps(inputs, sort_keys=True))
        if upload_key in st.session_state.sent_uploads and not submit:
            upload_key = None
    try:
        with st.spinner("Working on it"):
            if upload_key is not None:
                files = {"file": (upload.name, upload.getvalue(), upload.type)}
                data = {k: v for k, v in payload.items() if v is not None and k != "inputs"}
                data["inputs"] = json.dumps(inputs)
                if submit:
                    data["message"] = "Please review the attached document."
                r = requests.post(f"{API_URL}/chat/upload", data=data, files=files, headers=auth_headers(),
                                  timeout=180)
            else:
                r = requests.post(f"{API_URL}/chat", json=payload, headers=auth_headers(), timeout=180)
        r.raise_for_status()
        resp = r.json()
        if upload_key is not None:
            st.session_state.sent_uploads.add(upload_key)
    except requests.HTTPError as exc:
        resp = {"content": f"API error {exc.response.status_code}: {exc.response.text}", "citations": [], "trace": {}}
    except Exception as exc:
        resp = {"content": f"Request failed: {exc}", "citations": [], "trace": {}}

    entry = {"role": "assistant", "content": resp["content"], "citations": resp.get("citations", []),
             "structured": resp.get("structured", {})}
    st.session_state.messages.append(entry)
    with st.chat_message("assistant"):
        render_assistant(entry)
        if resp.get("trace"):
            st.caption(f"agent={resp.get('agent_id')} task={resp.get('task_id')} "
                       f"route={resp['trace'].get('route')} {resp['trace'].get('latency_ms')} ms")
