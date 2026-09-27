"""Model providers behind one interface (the deck's Shared AI Services layer).

Switching provider is a config change (LLM_PROVIDER=openai|azure|gemini|selfhosted|mock), which
is what "prevents vendor lock-in" in the architecture deck means in practice.
`mock` needs no API key and lets the whole stack run offline in tests and demos.

Gemini is reached through its OpenAI-compatible endpoint, so it reuses the same
client class with a different base URL and needs no additional SDK. The same holds
for `selfhosted`: a vLLM or Ollama server on university hardware, where no text
leaves SRH.

Structured output: callers may pass `json_schema`. Providers that support it
(OpenAI, Azure, vLLM) then enforce the schema during generation, so the result is
always valid JSON; the others fall back to JSON mode or plain text plus parse_json().

Embeddings are configured separately (EMBEDDING_BACKEND=remote|local|mock).
"local" keeps document text on the university's own machine, which matters for
GDPR and costs nothing per query.
"""

from __future__ import annotations

import abc
import hashlib
import json
import logging
import math
import re

from config.settings import Settings

log = logging.getLogger(__name__)

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)


def parse_json(raw: str) -> dict:
    """Parse a model's JSON answer, tolerating code fences and surrounding prose.

    Not every provider honours a strict JSON mode, so callers must never assume
    clean output. Returns {} when nothing parsable is found.
    """
    if not raw:
        return {}
    text = raw.strip()
    m = _FENCE.match(text)
    if m:
        text = m.group(1)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            data = json.loads(text[start:end + 1])
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            pass
    log.warning("could not parse JSON from model output: %r", raw[:200])
    return {}


class LLMText(str):
    """The answer text, plus why generation stopped. "length" means the answer was
    cut off at max_tokens, which for JSON output means it is incomplete. Behaves
    exactly like str everywhere else; plain strings (mocks) simply lack the field."""

    finish_reason: str | None = None


def finish_reason(text: str) -> str | None:
    return getattr(text, "finish_reason", None)


class BaseLLM(abc.ABC):
    @abc.abstractmethod
    async def chat(self, messages: list[dict], *, temperature: float | None = None,
                   max_tokens: int | None = None, json_mode: bool = False,
                   json_schema: dict | None = None) -> str: ...


class BaseEmbedder(abc.ABC):
    dim: int

    @abc.abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


# Mock implementations (offline)

class MockLLM(BaseLLM):
    async def chat(self, messages, *, temperature=None, max_tokens=None, json_mode=False,
                   json_schema=None) -> str:
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        if json_mode:
            return '{"task": null, "reason": "mock"}'
        preview = user[:300].replace("\n", " ")
        return (
            "[mock LLM, set LLM_PROVIDER=gemini, selfhosted, openai or azure for real answers]\n"
            f"System prompt length: {len(system)} chars. Your input was: {preview}"
        )


class MockEmbedder(BaseEmbedder):
    """Deterministic hashed bag-of-words embedding. Good enough to exercise the
    retrieval path end to end without a network call."""

    def __init__(self, dim: int = 1536):
        self.dim = dim

    async def embed(self, texts):
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            for tok in text.lower().split():
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
                vec[h % self.dim] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


# OpenAI-compatible providers: OpenAI, Azure OpenAI, Gemini and self-hosted servers

class OpenAICompatibleLLM(BaseLLM):
    """Works against any endpoint that speaks the OpenAI chat completions API.

    supports_json_mode    endpoint accepts response_format={"type": "json_object"}
    supports_json_schema  endpoint enforces response_format={"type": "json_schema", ...}
    extra_body            sent with every request (e.g. vLLM chat_template_kwargs)
    """

    def __init__(self, client, model: str, settings: Settings, supports_json_mode: bool = True,
                 supports_json_schema: bool = False, extra_body: dict | None = None):
        self.client, self.model, self.settings = client, model, settings
        self.supports_json_mode = supports_json_mode
        self.supports_json_schema = supports_json_schema
        self.extra_body = extra_body or {}

    def _response_format(self, json_mode: bool, json_schema: dict | None) -> dict | None:
        if json_schema is not None and self.supports_json_schema:
            return {"type": "json_schema", "json_schema": {"name": "response", "schema": json_schema}}
        if (json_mode or json_schema is not None) and self.supports_json_mode:
            return {"type": "json_object"}
        return None

    async def chat(self, messages, *, temperature=None, max_tokens=None, json_mode=False,
                   json_schema=None) -> str:
        kwargs = dict(
            model=self.model,
            messages=messages,
            temperature=self.settings.llm_temperature if temperature is None else temperature,
            max_tokens=max_tokens or self.settings.llm_max_tokens,
        )
        if self.extra_body:
            kwargs["extra_body"] = self.extra_body
        fmt = self._response_format(json_mode, json_schema)
        if fmt:
            kwargs["response_format"] = fmt
        try:
            resp = await self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            # Some compatible endpoints reject response_format. Step down once (schema to
            # JSON mode to plain) and let parse_json() deal with fences or stray prose.
            if not fmt or not _looks_like_bad_request(exc):
                raise
            if fmt["type"] == "json_schema":
                log.warning("%s rejected json_schema, falling back to JSON mode", self.model)
                self.supports_json_schema = False
            else:
                log.warning("%s rejected json_mode, falling back to plain completion", self.model)
                self.supports_json_mode = False
            fmt = self._response_format(json_mode, json_schema)
            kwargs.pop("response_format", None)
            if fmt:
                kwargs["response_format"] = fmt
            resp = await self.client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        text = LLMText(choice.message.content or "")
        text.finish_reason = getattr(choice, "finish_reason", None)
        if text.finish_reason == "length":
            log.warning("%s stopped at max_tokens=%s; the answer is cut off", self.model, kwargs["max_tokens"])
        return text


def _looks_like_bad_request(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    return status in (400, 404, 422) or "response_format" in str(exc).lower()


class OpenAICompatibleEmbedder(BaseEmbedder):
    """Embeddings over the OpenAI embeddings API. The real vector width is taken
    from the first response, because providers differ (OpenAI 1536, Gemini 3072)."""

    def __init__(self, client, model: str, configured_dim: int, batch_size: int = 32):
        self.client, self.model = client, model
        self.dim = configured_dim
        self.batch_size = batch_size
        self._dim_checked = False

    async def embed(self, texts):
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i:i + self.batch_size]
            resp = await self.client.embeddings.create(model=self.model, input=batch)
            out.extend(d.embedding for d in resp.data)
        if out and not self._dim_checked:
            self._dim_checked = True
            actual = len(out[0])
            if actual != self.dim:
                log.warning("embedding model %s returns %d dimensions, EMBEDDING_DIM is %d. "
                            "Using %d. Set EMBEDDING_DIM=%d before switching to pgvector.",
                            self.model, actual, self.dim, actual, actual)
                self.dim = actual
        return out


# Local embeddings (no API, no cost, text stays on this machine)

class LocalEmbedder(BaseEmbedder):
    def __init__(self, model_name: str, batch_size: int = 32, device: str = ""):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "EMBEDDING_BACKEND=local needs sentence-transformers: "
                "pip install sentence-transformers"
            ) from exc
        log.info("loading local embedding model %s on %s (first run downloads it)", model_name, device or "auto")
        self.model = SentenceTransformer(model_name, device=device or None)
        # sentence-transformers 5.x renamed the method; support both
        dim_of = getattr(self.model, "get_embedding_dimension", None) or self.model.get_sentence_embedding_dimension
        self.dim = dim_of()
        self.batch_size = batch_size

    async def embed(self, texts):
        import asyncio

        def _run():
            vecs = self.model.encode(texts, batch_size=self.batch_size,
                                     normalize_embeddings=True, show_progress_bar=False)
            return [v.tolist() for v in vecs]

        return await asyncio.to_thread(_run)


# Factory

def _build_client(settings: Settings):
    """Return (async client, chat model, embedding model, supports_json_mode, supports_json_schema)."""
    from openai import AsyncAzureOpenAI, AsyncOpenAI  # lazy: only needed for real providers

    provider = settings.llm_provider
    if provider == "openai":
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        return client, settings.openai_model, settings.openai_embedding_model, True, True
    if provider == "gemini":
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is required when LLM_PROVIDER=gemini. "
                               "Get a free key at https://aistudio.google.com/apikey")
        client = AsyncOpenAI(api_key=settings.gemini_api_key, base_url=settings.gemini_base_url)
        return client, settings.gemini_model, settings.gemini_embedding_model, False, False
    if provider == "selfhosted":
        if not settings.selfhosted_api_key:
            raise RuntimeError("SELFHOSTED_API_KEY is required when LLM_PROVIDER=selfhosted. "
                               "On the GPU node, `bash deploy/selfhosted/inhouse.sh info` prints it.")
        client = AsyncOpenAI(api_key=settings.selfhosted_api_key, base_url=settings.selfhosted_base_url)
        # vLLM supports both JSON mode and schema-enforced output. Embeddings are not
        # served by the chat server; build_llm_and_embedder() requires EMBEDDING_BACKEND=local.
        return client, settings.selfhosted_model, "", True, True
    if provider == "azure":
        if not (settings.azure_openai_endpoint and settings.azure_openai_api_key):
            raise RuntimeError("AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY are required")
        client = AsyncAzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
        )
        return client, settings.azure_openai_deployment, settings.azure_openai_embedding_deployment, True, True
    raise ValueError(f"unknown LLM provider {provider!r}")


def build_llm_and_embedder(settings: Settings) -> tuple[BaseLLM, BaseEmbedder]:
    backend = settings.embedding_backend

    if settings.llm_provider == "mock":
        log.warning("LLM_PROVIDER=mock: answers are placeholders")
        llm: BaseLLM = MockLLM()
        if backend == "local":
            return llm, LocalEmbedder(settings.local_embedding_model, settings.embedding_batch_size,
                                      settings.local_embedding_device)
        return llm, MockEmbedder(settings.embedding_dim)

    if settings.llm_provider == "selfhosted" and backend == "remote":
        raise RuntimeError("LLM_PROVIDER=selfhosted serves only the chat model. Set EMBEDDING_BACKEND=local "
                           "(and LOCAL_EMBEDDING_MODEL to the bge-m3 folder) so embeddings run in this process.")

    client, chat_model, embed_model, json_mode, json_schema = _build_client(settings)
    extra = None
    if settings.llm_provider == "selfhosted" and settings.selfhosted_disable_thinking:
        extra = {"chat_template_kwargs": {"enable_thinking": False}}
    elif settings.llm_provider == "gemini" and settings.gemini_reasoning_effort:
        # Sent in the request body, so it works with every openai client version.
        extra = {"reasoning_effort": settings.gemini_reasoning_effort}
    llm = OpenAICompatibleLLM(client, chat_model, settings, supports_json_mode=json_mode,
                              supports_json_schema=json_schema, extra_body=extra)

    if backend == "local":
        embedder: BaseEmbedder = LocalEmbedder(settings.local_embedding_model, settings.embedding_batch_size,
                                               settings.local_embedding_device)
    elif backend == "mock":
        log.warning("EMBEDDING_BACKEND=mock: retrieval quality will be poor")
        embedder = MockEmbedder(settings.embedding_dim)
    else:
        embedder = OpenAICompatibleEmbedder(client, embed_model, settings.embedding_dim,
                                            settings.embedding_batch_size)
    return llm, embedder
