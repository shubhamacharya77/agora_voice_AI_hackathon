"""Environment-based configuration.

Values come from real environment variables first, then from the repo-root `.env` file.
Keys in `.env` that this module doesn't know about are ignored, so later phases can add
their own settings without breaking earlier ones.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = Path(__file__).resolve().parents[1]
MIN_JWT_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    log_level: str = "INFO"
    company_name: str = "Alrium"
    # Markdown documents loaded into the knowledge base by `python -m app.seed`.
    knowledge_dir: Path = BACKEND_DIR / "knowledge" / "alrium"
    # Browser origins allowed to call the API (the Vite dev server by default).
    cors_origins: list[str] = ["http://localhost:5173"]

    # Required: no default, so a missing value fails loudly at startup instead of
    # silently pointing at the wrong database.
    database_url: str
    db_connect_timeout_seconds: int = 3

    # --- Auth ---
    jwt_secret: SecretStr
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_ttl_minutes: int = 720
    # One-click demo logins (no password). Off unless explicitly enabled in .env.
    demo_login_enabled: bool = False
    # Optional passwords for the demo users' normal email + password login.
    demo_admin_password: SecretStr | None = None
    demo_employee_password: SecretStr | None = None

    # --- AI model ---
    llm_provider: Literal["groq", "anthropic", "bedrock"] = "groq"
    # The provider's model id. Swap freely: nothing else depends on it.
    # groq: openai/gpt-oss-120b | anthropic: claude-haiku-5-5 | bedrock: anthropic.claude-haiku-5-5
    llm_model: str = "openai/gpt-oss-120b"
    # Lower effort = faster replies, which matters most for voice.
    llm_effort: Literal["low", "medium", "high"] = "low"
    llm_max_tokens: int = 8192
    llm_timeout_seconds: float = 30.0
    aws_region: str | None = None
    aws_profile: str | None = None
    # Bedrock API key; when set it's used instead of the AWS profile.
    aws_bearer_token_bedrock: SecretStr | None = None
    # Only for llm_provider="anthropic" (the first-party Claude API).
    anthropic_api_key: SecretStr | None = None
    # Only for llm_provider="groq" (OpenAI-compatible API).
    groq_api_key: SecretStr | None = None
    groq_base_url: str = "https://api.groq.com/openai/v1"
    # Wait out a provider rate limit that clears within this many seconds (Groq free tier).
    llm_rate_limit_max_wait_seconds: float = 20.0

    # --- Agent ---
    agent_max_steps: int = 5  # model calls per turn, tool rounds included
    # Earlier messages sent to the model each turn. Kept small: every token counts against
    # per-minute limits, and older context is still reachable through recall and summaries.
    agent_history_messages: int = 10
    # How long a "yes" counts after a proposal is read out. Later, it's shown again first.
    approval_window_minutes: int = 15
    # Reopening CoO within this many hours continues the latest conversation; after a
    # longer gap a fresh one starts (earlier ones come back only when asked for).
    conversation_continue_hours: float = 2.0
    # Refresh a conversation's recap after this many new messages (0 = never).
    summary_every_messages: int = 6
    # Save LangGraph checkpoints in Postgres.
    langgraph_checkpoints: bool = True

    # --- Voice: Agora Conversational AI Engine + Murf (see the setup checklist) ---
    agora_app_id: str | None = None
    agora_app_certificate: SecretStr | None = None
    # Optional: Basic auth for Agora's REST API. Without them, a token built from the
    # App ID + App Certificate authenticates instead.
    agora_customer_id: str | None = None
    agora_customer_secret: SecretStr | None = None
    agora_api_base: str = "https://api.agora.io/api/conversational-ai-agent/v2"
    agora_asr_language: str = "en-US"
    agora_idle_timeout_seconds: int = 120
    murf_api_key: SecretStr | None = None
    murf_voice_id: str = "en-IN-isha"
    murf_model: str = "falcon-2.1"
    murf_locale: str = "en-IN"
    # Public HTTPS address of this backend (the ngrok URL): Agora's cloud calls it.
    public_base_url: str | None = None
    # Shared secret Agora sends with every call to our custom-LLM endpoint.
    custom_llm_api_key: SecretStr | None = None

    @property
    def voice_missing(self) -> list[str]:
        """The .env keys still needed before Agora voice can run (empty = ready)."""
        required = {
            "AGORA_APP_ID": self.agora_app_id,
            "AGORA_APP_CERTIFICATE": self.agora_app_certificate,
            "MURF_API_KEY": self.murf_api_key,
            "PUBLIC_BASE_URL": self.public_base_url,
            "CUSTOM_LLM_API_KEY": self.custom_llm_api_key,
        }
        return [name for name, value in required.items() if not _filled(value)]

    @field_validator("jwt_secret")
    @classmethod
    def _jwt_secret_long_enough(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < MIN_JWT_SECRET_LENGTH:
            raise ValueError(f"JWT_SECRET must be at least {MIN_JWT_SECRET_LENGTH} characters")
        return value


def _filled(value: str | SecretStr | None) -> bool:
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    return bool(value and value.strip())


def secret(value: SecretStr | None) -> str:
    """The secret's value, or "" when unset."""
    return value.get_secret_value().strip() if value is not None else ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
