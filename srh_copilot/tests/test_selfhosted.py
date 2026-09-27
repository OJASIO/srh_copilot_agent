"""Self-hosted provider (vLLM on the GPU node). Offline: the OpenAI client is
replaced by a recorder, so these tests check what we would send, not a server."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agents.student_service.cv_check.schema import CV_REVIEW_SCHEMA
from config.settings import Settings
from core.providers import OpenAICompatibleLLM, build_llm_and_embedder


class Recorder:
    """Stands in for AsyncOpenAI().chat.completions; optionally rejects response_format types."""

    def __init__(self, reject: tuple[str, ...] = ()):
        self.calls: list[dict] = []
        self.reject = reject
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kw):
        self.calls.append(kw)
        fmt = kw.get("response_format", {}).get("type")
        if fmt in self.reject:
            err = Exception(f"response_format {fmt} not supported")
            err.status_code = 400
            raise err
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))])


def _settings(**kw) -> Settings:
    base = dict(llm_provider="selfhosted", selfhosted_api_key="k", embedding_backend="mock",
                vector_backend="memory", app_env="test", api_key="test-key")
    return Settings(**{**base, **kw})


def test_selfhosted_builds_client_for_local_server():
    llm, _ = build_llm_and_embedder(_settings(selfhosted_base_url="http://127.0.0.1:8001/v1"))
    assert isinstance(llm, OpenAICompatibleLLM)
    assert llm.model == "srh-llm"
    assert str(llm.client.base_url).startswith("http://127.0.0.1:8001/v1")
    assert llm.supports_json_schema and llm.supports_json_mode
    assert llm.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}


def test_selfhosted_thinking_can_be_left_on():
    llm, _ = build_llm_and_embedder(_settings(selfhosted_disable_thinking=False))
    assert llm.extra_body == {}


def test_selfhosted_requires_key():
    with pytest.raises(RuntimeError, match="SELFHOSTED_API_KEY"):
        build_llm_and_embedder(_settings(selfhosted_api_key=""))


def test_selfhosted_refuses_remote_embeddings():
    # the vLLM chat server does not serve embeddings; fail at startup, not at the first question
    with pytest.raises(RuntimeError, match="EMBEDDING_BACKEND=local"):
        build_llm_and_embedder(_settings(embedding_backend="remote"))


def test_request_carries_schema_and_thinking_switch():
    rec = Recorder()
    llm = OpenAICompatibleLLM(rec, "srh-llm", _settings(), supports_json_schema=True,
                              extra_body={"chat_template_kwargs": {"enable_thinking": False}})
    asyncio.run(llm.chat([{"role": "user", "content": "x"}], json_mode=True, json_schema=CV_REVIEW_SCHEMA,
                         max_tokens=2500))
    sent = rec.calls[0]
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["schema"] is CV_REVIEW_SCHEMA
    assert sent["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert sent["max_tokens"] == 2500


def test_schema_rejected_steps_down_to_json_mode_then_plain():
    rec = Recorder(reject=("json_schema", "json_object"))
    llm = OpenAICompatibleLLM(rec, "m", _settings(), supports_json_mode=True, supports_json_schema=True)
    with pytest.raises(Exception, match="json_object"):
        asyncio.run(llm.chat([{"role": "user", "content": "x"}], json_schema=CV_REVIEW_SCHEMA))
    assert [c["response_format"]["type"] for c in rec.calls] == ["json_schema", "json_object"]
    assert llm.supports_json_schema is False
    # the next call remembers the downgrade and does not retry the schema
    rec2 = Recorder(reject=("json_schema",))
    llm2 = OpenAICompatibleLLM(rec2, "m", _settings(), supports_json_mode=True, supports_json_schema=True)
    out = asyncio.run(llm2.chat([{"role": "user", "content": "x"}], json_schema=CV_REVIEW_SCHEMA))
    assert out == '{"ok": true}'
    asyncio.run(llm2.chat([{"role": "user", "content": "y"}], json_schema=CV_REVIEW_SCHEMA))
    assert [c["response_format"]["type"] for c in rec2.calls] == ["json_schema", "json_object", "json_object"]


def test_gemini_ignores_schema():
    rec = Recorder()
    llm = OpenAICompatibleLLM(rec, "gemini-2.5-flash", _settings(), supports_json_mode=False,
                              supports_json_schema=False)
    asyncio.run(llm.chat([{"role": "user", "content": "x"}], json_mode=True, json_schema=CV_REVIEW_SCHEMA))
    assert "response_format" not in rec.calls[0] and "extra_body" not in rec.calls[0]


def test_cv_task_passes_schema_and_budget(tmp_path):
    from tests.test_cv_check import EN_CV, StubLLM, make_docx
    from core.orchestrator import Orchestrator
    from core.registry import AgentRegistry
    from core.schemas import AgentRequest, Attachment
    from core.services import ServiceContainer

    s = Settings(llm_provider="mock", vector_backend="memory", app_env="test", api_key="test-key",
                 vector_index_path=tmp_path / "i.json", enabled_agents=[])
    services = ServiceContainer.build(s)
    services.settings = s.model_copy(update={"llm_provider": "selfhosted"})  # task reads the provider name
    stub = StubLLM()
    services.llm = stub
    reg = AgentRegistry(services)
    reg.load()
    req = AgentRequest(session_id="s", user_id="u", agent_id="student_service", message="review",
                       attachments=[Attachment(filename="cv.docx", content_type="x", data=make_docx(EN_CV))])
    asyncio.run(Orchestrator(reg, services).run(req))
    assert stub.calls[-1]["json_schema"] is CV_REVIEW_SCHEMA
    assert stub.calls[-1]["max_tokens"] == 2500
