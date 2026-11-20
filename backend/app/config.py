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
    llm_provider: Literal["bedrock", "anthropic"] = "bedrock"
    # Bedrock (Mantle endpoint) model id. Swap freely: nothing else depends on it.
    llm_model: str = "anthropic.claude-haiku-5-5"
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

    # --- Agent ---
    agent_max_steps: int = 5  # model calls per turn, tool rounds included
    agent_history_messages: int = 20  # earlier messages sent to the model each turn

    @field_validator("jwt_secret")
    @classmethod
    def _jwt_secret_long_enough(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < MIN_JWT_SECRET_LENGTH:
            raise ValueError(f"JWT_SECRET must be at least {MIN_JWT_SECRET_LENGTH} characters")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
