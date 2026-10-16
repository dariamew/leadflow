import json
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

WRITABLE_ATTRIBUTES = {"category", "users", "budget_min", "budget_max", "currency", "deadline_text"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(min_length=1)
    auth_cookie_secure: bool = True
    auth_session_hours: int = Field(default=12, ge=1, le=168)
    auth_invite_hours: int = Field(default=24, ge=1, le=168)
    auth_public_origin: str = ""
    extractor: Literal["rules", "llm"] = "rules"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""
    llm_timeout_seconds: float = Field(default=45, gt=0, le=120)
    bitrix_webhook_url: SecretStr = SecretStr("")
    bitrix_text_field: str = "comments"
    bitrix_timeout_seconds: float = Field(default=15, gt=0, le=60)
    monitor_worker_enabled: bool = True
    bitrix_field_map: dict[str, str] = Field(default_factory=dict)
    priority_budget_threshold_rub: int = Field(default=100000, gt=0)

    @field_validator("auth_public_origin")
    @classmethod
    def valid_origin(cls, value: str) -> str:
        if value:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("AUTH_PUBLIC_ORIGIN must be an HTTP(S) origin")
            return value.rstrip("/")
        return value

    @field_validator("bitrix_webhook_url")
    @classmethod
    def validate_webhook(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if not raw:
            return value
        parsed = urlsplit(raw)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not parsed.path.startswith("/rest/")
        ):
            raise ValueError("BITRIX_WEBHOOK_URL must be an HTTPS incoming webhook URL")
        return value

    @field_validator("bitrix_field_map")
    @classmethod
    def validate_map(cls, value: dict[str, str]) -> dict[str, str]:
        if set(value) - WRITABLE_ATTRIBUTES:
            raise ValueError("Unknown qualification attribute in BITRIX_FIELD_MAP")
        if any(not field.startswith("UF_CRM_") for field in value.values()):
            raise ValueError("Only custom UF_CRM_ fields can be mapped")
        if len(set(value.values())) != len(value):
            raise ValueError("Each attribute needs a separate CRM field")
        return value

    @model_validator(mode="after")
    def validate_provider(self) -> "Settings":
        if self.extractor == "llm" and (
            not self.llm_model.strip() or not self.llm_api_key.get_secret_value()
        ):
            raise ValueError("EXTRACTOR=llm requires LLM_MODEL and LLM_API_KEY")
        return self


def pipeline_signature(settings: Settings) -> str:
    return json.dumps(
        {
            "version": "0.1.0",
            "extractor": settings.extractor,
            "model": settings.llm_model if settings.extractor == "llm" else "rules-v1",
            "endpoint": settings.llm_base_url if settings.extractor == "llm" else None,
            "budget_threshold": settings.priority_budget_threshold_rub,
        },
        sort_keys=True,
    )
