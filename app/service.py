import hashlib
import json
import time
from datetime import UTC
from urllib.parse import urlsplit

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, pipeline_signature
from app.integrations.bitrix import BitrixClient, BitrixError
from app.integrations.crm_text import crm_plain_text
from app.models import QualificationRun, User
from app.qualification.pipeline import PIPELINE_VERSION, QualificationPipeline
from app.schemas import ApplyRequest, Category, QualificationRead, QualificationResult, WriteOption


def run_text(run: QualificationRun) -> str:
    if run.lead_id is not None and run.source_snapshot.get("text_format") != "plain-v1":
        return crm_plain_text(run.source_text)
    return run.source_text


def run_result(run: QualificationRun) -> QualificationResult:
    result = QualificationResult.model_validate(run.result)
    if run.lead_id is not None and run.source_snapshot.get("text_format") != "plain-v1":
        # Old results retain their stored analysis. Normalize their display and
        # citation strings together so history also shows plain text immediately.
        facts = result.facts
        if facts.category_evidence:
            facts.category_evidence = crm_plain_text(facts.category_evidence)
        for fact in [facts.users, facts.budget, facts.deadline, *facts.integrations]:
            if fact is not None:
                fact.evidence = crm_plain_text(fact.evidence)
                if isinstance(getattr(fact, "value", None), str):
                    fact.value = crm_plain_text(fact.value)
    return result


def read_run(run: QualificationRun) -> QualificationRead:
    result = run_result(run)
    suggestions = suggested_values(result)
    options = [
        WriteOption(
            attribute=attribute,
            suggested_value=suggestions[attribute],
            current_value=run.source_snapshot["fields"].get(field),
        )
        for attribute, field in run.source_snapshot["field_map"].items()
        if attribute in suggestions and run.lead_id is not None
    ]
    return QualificationRead(
        id=run.id,
        created_by_user_id=run.created_by_user_id,
        created_by_username=run.created_by_username,
        trigger=run.trigger,
        lead_id=run.lead_id,
        source_text=run_text(run),
        source_text_field=run.source_snapshot.get("text_field")
        if run.lead_id is not None
        else None,
        source_field_explicit=bool(run.source_snapshot.get("source_field_explicit")),
        extractor=run.extractor,
        pipeline_version=run.pipeline_version,
        status=run.status,
        result=result,
        created_at=run.created_at.replace(tzinfo=UTC)
        if run.created_at.tzinfo is None
        else run.created_at,
        duration_ms=run.duration_ms,
        token_usage=run.token_usage,
        applied_attributes=run.applied_attributes,
        write_options=options,
    )


def lead_text(lead: dict, settings: Settings) -> str:
    value = lead.get(settings.bitrix_text_field)
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(422, "Configured lead text field is empty or is not text")
    if len(value) > 20000:
        raise HTTPException(422, "Lead text exceeds 20000 characters")
    value = crm_plain_text(value)
    if not value:
        raise HTTPException(422, "Configured lead text field is empty or is not text")
    return value


async def qualify_and_store(
    session: AsyncSession,
    pipeline: QualificationPipeline,
    settings: Settings,
    text: str,
    lead_id: int | None = None,
    lead: dict | None = None,
    *,
    created_by: User,
    trigger: str = "manual",
    source_field_explicit: bool = False,
) -> QualificationRun:
    snapshot = {
        "portal": urlsplit(settings.bitrix_webhook_url.get_secret_value()).netloc.casefold(),
        "text_field": settings.bitrix_text_field,
        "field_map": settings.bitrix_field_map,
        "fields": {field: (lead or {}).get(field) for field in settings.bitrix_field_map.values()},
        "pipeline_config": json.loads(pipeline_signature(settings)),
    }
    if lead_id is not None:
        snapshot["text_format"] = "plain-v1"
        if source_field_explicit:
            snapshot["source_field_explicit"] = True
    serialized = json.dumps(
        {"text": text, "lead_id": lead_id, "snapshot": snapshot, "user_id": created_by.id},
        sort_keys=True,
        ensure_ascii=False,
    )
    source_key = hashlib.sha256(serialized.encode()).hexdigest()
    existing = await session.scalar(
        select(QualificationRun).where(QualificationRun.source_key == source_key)
    )
    if existing:
        return existing
    started = time.perf_counter()
    result, usage = await pipeline.qualify(text)
    run = QualificationRun(
        source_key=source_key,
        created_by_user_id=created_by.id,
        created_by_username=created_by.username,
        trigger=trigger,
        lead_id=lead_id,
        source_text=text,
        source_snapshot=snapshot,
        extractor=settings.extractor,
        pipeline_version=PIPELINE_VERSION,
        result=result.model_dump(mode="json"),
        token_usage=usage,
        duration_ms=round((time.perf_counter() - started) * 1000),
    )
    session.add(run)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await session.scalar(
            select(QualificationRun).where(QualificationRun.source_key == source_key)
        )
        if existing is None:
            raise
        return existing
    return run


def suggested_values(result: QualificationResult) -> dict:
    facts = result.facts
    values = {}
    if facts.category not in {Category.UNKNOWN, Category.OTHER}:
        values["category"] = facts.category.value
    if facts.users:
        values["users"] = facts.users.value
    if facts.budget:
        values.update(
            {
                "budget_min": facts.budget.minimum,
                "budget_max": facts.budget.maximum,
                "currency": facts.budget.currency,
            }
        )
    if facts.deadline:
        values["deadline_text"] = facts.deadline.value
    return {key: value for key, value in values.items() if value is not None}


async def apply_run(
    session: AsyncSession,
    client: BitrixClient,
    settings: Settings,
    run: QualificationRun,
    approval: ApplyRequest,
) -> QualificationRun:
    if run.lead_id is None:
        raise HTTPException(409, "Text-only qualification is not linked to a CRM lead")
    if run.status == "applied" and run.applied_attributes == approval.attributes:
        return run
    if run.status != "pending_review":
        raise HTTPException(
            409, "Run is already applied, being applied, or requires reconciliation"
        )
    if run.source_snapshot["field_map"] != settings.bitrix_field_map:
        raise HTTPException(409, "CRM field mapping changed; qualify the lead again")
    portal = urlsplit(settings.bitrix_webhook_url.get_secret_value()).netloc.casefold()
    if run.source_snapshot["portal"] != portal:
        raise HTTPException(409, "CRM portal changed; qualify the lead again")
    values = suggested_values(run_result(run))
    if any(
        key not in settings.bitrix_field_map or key not in values for key in approval.attributes
    ):
        raise HTTPException(422, "An attribute is unmapped or has no unambiguous suggested value")
    fields = {settings.bitrix_field_map[key]: values[key] for key in approval.attributes}

    # Commit the claim before a remote write. A crashed writer leaves 'applying';
    # never blindly repeat a potentially successful external side effect.
    claim = await session.execute(
        update(QualificationRun)
        .where(QualificationRun.id == run.id, QualificationRun.status == "pending_review")
        .values(status="applying", applied_attributes=approval.attributes)
    )
    await session.commit()
    if claim.rowcount != 1:
        raise HTTPException(409, "Another request has claimed this run")

    async def set_status(status: str) -> None:
        run.status = status
        if status == "pending_review":
            run.applied_attributes = None
        await session.commit()

    try:
        current = await client.get_lead(run.lead_id)
        if (
            run.source_snapshot["text_field"] != settings.bitrix_text_field
            or lead_text(current, settings) != run_text(run)
            or any(
                current.get(field) != run.source_snapshot["fields"].get(field) for field in fields
            )
        ):
            raise HTTPException(409, "CRM source or selected fields changed; qualify again")
        if not approval.allow_overwrite and any(
            current.get(field) not in (None, "") for field in fields
        ):
            raise HTTPException(
                409, "Selected fields are not empty; explicit overwrite is required"
            )
    except (BitrixError, HTTPException):
        await set_status("pending_review")
        raise

    try:
        await client.update_lead(run.lead_id, fields)
    except BitrixError as error:
        await set_status("apply_unknown" if error.outcome_unknown else "pending_review")
        raise
    await set_status("applied")
    return run
