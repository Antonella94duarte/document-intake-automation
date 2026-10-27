from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

Effort = Literal["low", "medium", "high", "xhigh", "max"]

# backend/ -> repository root, where .env and the default storage folder live.
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Application settings, read from environment variables or the repository's .env file."""

    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "Document Intake Automation"
    environment: str = "development"
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://intake:intake@localhost:5435/document_intake"

    # Uploaded files. A local folder now; the Storage interface allows S3 later.
    storage_dir: Path = REPO_ROOT / "storage"
    max_upload_mb: int = 15

    # --- Claude ---
    # Optional so the API starts without it; processing then fails with a clear error.
    anthropic_api_key: SecretStr | None = None
    llm_model: str = "claude-opus-5-5"
    # Classification is an easy call; extraction needs more care with numbers and dates.
    classify_effort: Effort = "low"
    extract_effort: Effort = "medium"
    llm_max_tokens: int = 16000
    llm_timeout_seconds: float = 180.0
    # Server-side fallback when the primary model declines a request (Anthropic beta).
    llm_fallbacks_enabled: bool = True

    # --- Review policy ---
    # Below this confidence a field (or the document type) goes to human review.
    review_confidence_threshold: float = 0.8
    # Money comparisons (line items vs subtotal, invoice vs PO) tolerate this absolute difference.
    amount_tolerance: float = 0.01
    # Minutes a person needs to key in one document by hand; used for the "time saved" metric.
    manual_minutes_per_document: float = 6.0

    # --- Worker ---
    worker_poll_seconds: float = 2.0
    worker_max_attempts: int = 3

    # --- Auth ---
    jwt_secret: SecretStr = SecretStr("dev-secret-change-me")
    jwt_expire_minutes: int = 8 * 60
    demo_user_email: str | None = None
    demo_user_password: SecretStr | None = None
    # Origins allowed to call the API from a browser (the Next.js dashboard).
    cors_origins: str = "http://localhost:3000"


@lru_cache
def get_settings() -> Settings:
    return Settings()
