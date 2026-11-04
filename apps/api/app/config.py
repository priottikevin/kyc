from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # repo-root .env (shared with docker compose), then apps/api/.env (wins); real env vars win over both
    model_config = SettingsConfigDict(env_file=("../../.env", ".env"), env_file_encoding="utf-8", extra="ignore")

    # Security
    api_keys: str = ""  # comma-separated; empty = no auth
    cors_origins: str = "*"
    database_url: str = ""  # PostgreSQL: clients, API keys, usage. Empty = API_KEYS only, no admin data
    admin_password: str = ""  # empty = /admin login disabled

    # Upload limits
    max_upload_mb: int = 15
    max_pdf_pages: int = 2

    # OCR
    ocr_fast_model: Literal["tiny", "small"] = "tiny"
    ocr_accurate_model: Literal["small", "medium", "none"] = "small"
    ocr_max_side: int = 2000
    ocr_concurrency: int = 2
    ocr_threads: int = 0  # 0 = onnxruntime default

    # LLM fallback (Claude)
    llm_mode: Literal["fallback", "off", "always"] = "fallback"
    llm_model: str = "claude-haiku-5-5"
    llm_effort: Literal["low", "medium", "high"] = "low"
    llm_thinking: bool = False  # off = faster; reading values needs no reasoning
    llm_check_uncertain: bool = True  # send crops of low-confidence values to the LLM (fallback mode)
    llm_timeout_s: float = 30.0
    # USD per million tokens, for engine.llm_cost_usd (Claude Haiku 5.5, prompts up to 100K tokens)
    llm_price_input: float = 0.10
    llm_price_output: float = 0.50
    llm_image_max_side: int = 1568
    anthropic_api_key: str = ""

    # Dev: serve the web UI from the API (prod uses nginx in apps/web)
    static_dir: str = ""

    @property
    def api_key_set(self) -> set[str]:
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
