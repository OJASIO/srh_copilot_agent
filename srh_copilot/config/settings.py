"""Central configuration. Every value can be overridden through environment
variables or a .env file (see .env.example). Nothing else in the codebase
reads os.environ directly; import `settings` from here instead."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Application
    app_name: str = "SRH AI Copilot"
    app_env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:8501"]

    # Security
    api_key: str = Field(default="change-me", description="Shared key for the frontend to call the API")
    jwt_secret: str = Field(default="change-me-too-use-32-plus-random-bytes", description="Secret used to sign user tokens")
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    rate_limit: str = "60/minute"
    max_message_chars: int = 4000
    max_input_chars: int = 8000  # per task field, e.g. a pasted job description
    max_upload_mb: int = 10

    # Agents: which plugs are active. Empty list means "everything with a manifest".
    enabled_agents: Annotated[list[str], NoDecode] = []
    agents_config_file: Path = PROJECT_ROOT / "config" / "agents.yaml"

    # LLM provider
    llm_provider: Literal["openai", "azure", "gemini", "selfhosted", "mock"] = "mock"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_deployment: str = ""
    azure_openai_embedding_deployment: str = ""
    # Gemini is reached through its OpenAI-compatible endpoint, so it needs no extra SDK.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_embedding_model: str = "gemini-embedding-001"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    # Optional: "none" | "low" | "medium" | "high". Empty sends nothing (Google's default,
    # thinking on). For a fair comparison with the self-hosted model, which runs with
    # thinking off, set "none" (Gemini 2.5 Flash). Check once with scripts/check_provider.py.
    gemini_reasoning_effort: str = ""
    # Self-hosted: any OpenAI-compatible server on university hardware (vLLM, Ollama).
    # deploy/selfhosted/inhouse.sh starts one and `bash inhouse.sh info` prints these values.
    selfhosted_base_url: str = "http://127.0.0.1:8001/v1"
    selfhosted_model: str = "srh-llm"
    selfhosted_api_key: str = ""
    # Qwen3.x reasons at length before answering unless told not to. Our tasks do not
    # need it and it multiplies latency, so it is off by default.
    selfhosted_disable_thinking: bool = True
    llm_temperature: float = 0.1
    llm_max_tokens: int = 1024

    # Embeddings. "remote" calls the LLM_PROVIDER vendor's embedding API; "local" runs a
    # sentence-transformers model here (no API cost, documents never leave the server);
    # "mock" is offline hashing for tests.
    embedding_backend: Literal["remote", "local", "mock"] = "remote"
    local_embedding_model: str = "intfloat/multilingual-e5-small"
    # "cpu", "cuda" or "" (auto). On a GPU node where vLLM holds the GPU, use cpu.
    local_embedding_device: str = ""
    embedding_dim: int = 1536
    embedding_batch_size: int = 32

    # Storage
    database_url: str = "postgresql+psycopg://copilot:copilot@localhost:5432/copilot"
    vector_backend: Literal["pgvector", "memory"] = "memory"
    vector_index_path: Path = PROJECT_ROOT / "data" / "processed" / "vector_index.json"

    # Retrieval
    chunk_size: int = 800
    chunk_overlap: int = 120
    retrieval_top_k: int = 5

    # Guardrails
    guardrails_block_pii_in_logs: bool = True
    # Mask emails, phone numbers, IBANs and matriculation numbers in chat messages and
    # task fields before any model sees them (and before they are stored as history).
    guardrails_mask_pii_in_messages: bool = True
    # Email domains that are institutional, not personal, and stay readable (exact match).
    pii_allowed_email_domains: Annotated[list[str], NoDecode] = ["srh.de"]
    guardrails_max_tool_hops: int = 5

    @field_validator("enabled_agents", "cors_origins", "pii_allowed_email_domains", mode="before")
    @classmethod
    def _split_csv(cls, v):
        """Accept 'a,b,c' or JSON lists from the environment."""
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("["):
                import json
                return json.loads(v)
            return [x.strip() for x in v.split(",") if x.strip()]
        return v

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"

    def assert_production_safe(self) -> None:
        """Refuse to start in prod with placeholder secrets or prototype backends."""
        problems = []
        if self.api_key.startswith("change-me"):
            problems.append("API_KEY is the placeholder")
        if self.jwt_secret.startswith("change-me") or len(self.jwt_secret) < 32:
            problems.append("JWT_SECRET is weak (need 32+ random chars)")
        if self.llm_provider == "mock":
            problems.append("LLM_PROVIDER=mock")
        if self.vector_backend == "memory":
            problems.append("VECTOR_BACKEND=memory")
        if self.embedding_backend == "mock":
            problems.append("EMBEDDING_BACKEND=mock")
        if problems:
            raise RuntimeError("unsafe production config: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
