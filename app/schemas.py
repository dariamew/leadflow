from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Category(StrEnum):
    IMPLEMENTATION = "crm_implementation"
    INTEGRATION = "integration"
    SUPPORT = "support"
    OTHER = "other"
    UNKNOWN = "unknown"


class TextFact(StrictModel):
    value: str = Field(min_length=1, max_length=1000)
    evidence: str = Field(min_length=1, max_length=2000)


class NumberFact(StrictModel):
    value: int = Field(gt=0, le=1000000)
    evidence: str = Field(min_length=1, max_length=2000)


class BudgetFact(StrictModel):
    minimum: float | None = Field(ge=0, allow_inf_nan=False)
    maximum: float | None = Field(ge=0, allow_inf_nan=False)
    currency: Literal["RUB", "USD", "EUR"] | None
    evidence: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def valid_range(self) -> "BudgetFact":
        if self.minimum is None and self.maximum is None:
            raise ValueError("Budget needs at least one bound")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Budget minimum exceeds maximum")
        return self


class ExtractedFacts(StrictModel):
    # All fields required (including nullable ones) for strict provider JSON Schema.
    category: Category
    category_evidence: str | None
    users: NumberFact | None
    budget: BudgetFact | None
    deadline: TextFact | None
    integrations: list[TextFact]
    warnings: list[str]


class QualificationResult(StrictModel):
    facts: ExtractedFacts
    priority: Literal["normal", "elevated"]
    priority_score: int
    priority_reasons: list[str]
    completeness: float = Field(ge=0, le=1)
    missing_fields: list[str]
    questions: list[str]


class QualificationRequest(StrictModel):
    text: str = Field(min_length=1, max_length=20000)

    @field_validator("text")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Application text cannot be blank")
        return value


class WriteOption(StrictModel):
    attribute: str
    suggested_value: str | int | float
    current_value: Any


class LeadQualificationRequest(StrictModel):
    text_field: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$")


class QualificationRead(StrictModel):
    id: UUID
    created_by_user_id: UUID | None
    created_by_username: str | None
    trigger: str
    lead_id: int | None
    source_text: str
    source_text_field: str | None
    source_field_explicit: bool
    extractor: str
    pipeline_version: str
    status: str
    result: QualificationResult
    created_at: datetime
    duration_ms: int
    token_usage: dict[str, int]
    applied_attributes: list[str] | None
    write_options: list[WriteOption]


class ApplyRequest(StrictModel):
    attributes: list[
        Literal["category", "users", "budget_min", "budget_max", "currency", "deadline_text"]
    ] = Field(min_length=1, max_length=6)
    allow_overwrite: bool = False

    @field_validator("attributes")
    @classmethod
    def unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("Attributes must be unique")
        return sorted(value)
