from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import AuthContext, CurrentUser, authenticate
from app.db import get_session
from app.models import QualificationRun
from app.schemas import (
    ApplyRequest,
    LeadQualificationRequest,
    QualificationRead,
    QualificationRequest,
)
from app.service import apply_run, lead_text, qualify_and_store, read_run
from app.source_settings import effective_settings, text_fields, validate_text_field

health_router = APIRouter(tags=["health"])
router = APIRouter(prefix="/api/v1", dependencies=[Depends(authenticate)])
Session = Annotated[AsyncSession, Depends(get_session)]
PositiveLeadId = Annotated[int, Path(gt=0)]


@router.get("/config", tags=["configuration"])
async def frontend_config(request: Request, session: Session):
    settings = await effective_settings(session, request.app.state.settings)
    return {
        "extractor": settings.extractor,
        "bitrix_configured": bool(settings.bitrix_webhook_url.get_secret_value()),
        "writable_attributes": list(settings.bitrix_field_map),
        "bitrix_text_field": settings.bitrix_text_field,
    }


@health_router.get("/health/live")
async def live():
    return {"status": "ok"}


@health_router.get("/health/ready")
async def ready(session: Session):
    try:
        # Also verifies that migrations ran, not only that PostgreSQL accepts connections.
        await session.execute(text("SELECT id, created_by_user_id FROM qualification_runs LIMIT 1"))
        await session.execute(text("SELECT trigger FROM qualification_runs LIMIT 1"))
        await session.execute(text("SELECT id FROM lead_jobs LIMIT 1"))
        if await session.scalar(text("SELECT id FROM bitrix_source WHERE id = 1")) != 1:
            raise HTTPException(503, "Bitrix source settings are not initialized")
        if await session.scalar(text("SELECT id FROM lead_monitor WHERE id = 1")) != 1:
            raise HTTPException(503, "Lead monitoring is not initialized")
        await session.execute(text("SELECT id FROM users LIMIT 1"))
        await session.execute(text("SELECT token_hash FROM user_sessions LIMIT 1"))
        await session.execute(text("SELECT key FROM login_limits LIMIT 1"))
        await session.execute(text("SELECT role FROM users LIMIT 1"))
        await session.execute(text("SELECT token_hash, purpose FROM password_invitations LIMIT 1"))
        if await session.scalar(text("SELECT id FROM user_admin_lock WHERE id = 1")) != 1:
            raise HTTPException(503, "User administration is not initialized")
    except SQLAlchemyError:
        raise HTTPException(503, "Database unavailable or migrations not applied") from None
    return {"status": "ready"}


@router.post("/qualifications", response_model=QualificationRead, tags=["qualification"])
async def qualify_text(
    payload: QualificationRequest, request: Request, session: Session, context: CurrentUser
):
    run = await qualify_and_store(
        session,
        request.app.state.pipeline,
        request.app.state.settings,
        payload.text,
        created_by=context.user,
    )
    return read_run(run)


@router.post(
    "/bitrix/leads/{lead_id}/qualify",
    response_model=QualificationRead,
    tags=["bitrix"],
)
async def qualify_lead(
    lead_id: PositiveLeadId,
    request: Request,
    session: Session,
    context: CurrentUser,
    payload: LeadQualificationRequest | None = None,
):
    settings = await effective_settings(session, request.app.state.settings)
    explicit = payload is not None and payload.text_field is not None
    if explicit:
        await validate_text_field(request.app.state.bitrix, payload.text_field)
        settings = settings.model_copy(update={"bitrix_text_field": payload.text_field})
    lead = await request.app.state.bitrix.get_lead(lead_id)
    run = await qualify_and_store(
        session,
        request.app.state.pipeline,
        settings,
        lead_text(lead, settings),
        lead_id,
        lead,
        created_by=context.user,
        source_field_explicit=explicit,
    )
    return read_run(run)


@router.get("/bitrix/fields", tags=["bitrix"])
async def bitrix_fields(request: Request):
    return await request.app.state.bitrix.get_lead_fields()


@router.get("/bitrix/text-fields", tags=["bitrix"])
async def manual_text_fields(request: Request):
    return await text_fields(request.app.state.bitrix)


@router.get("/qualifications", response_model=list[QualificationRead], tags=["qualification"])
async def list_runs(
    session: Session,
    context: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    lead_id: Annotated[int | None, Query(gt=0, le=9007199254740991)] = None,
    status: Literal["pending_review", "applying", "applied", "apply_unknown"] | None = None,
    priority: Literal["normal", "elevated"] | None = None,
):
    query = visible_runs(context)
    if lead_id is not None:
        query = query.where(QualificationRun.lead_id == lead_id)
    if status is not None:
        query = query.where(QualificationRun.status == status)
    if priority is not None:
        query = query.where(QualificationRun.result["priority"].as_string() == priority)
    runs = await session.scalars(
        query.order_by(QualificationRun.created_at.desc(), QualificationRun.id)
        .offset(offset)
        .limit(limit)
    )
    return [read_run(run) for run in runs]


def visible_runs(context: AuthContext):
    query = select(QualificationRun)
    if context.user.role != "admin":
        query = query.where(QualificationRun.created_by_user_id == context.user.id)
    return query


async def find_run(run_id: UUID, session: AsyncSession, context: AuthContext) -> QualificationRun:
    run = await session.scalar(visible_runs(context).where(QualificationRun.id == str(run_id)))
    if run is None:
        raise HTTPException(404, "Qualification not found")
    return run


@router.get("/qualifications/{run_id}", response_model=QualificationRead, tags=["qualification"])
async def get_run(run_id: UUID, session: Session, context: CurrentUser):
    return read_run(await find_run(run_id, session, context))


@router.post(
    "/qualifications/{run_id}/apply",
    response_model=QualificationRead,
    tags=["bitrix"],
    summary="Explicitly approve selected suggestions and write them to custom CRM fields",
)
async def approve_run(
    run_id: UUID, payload: ApplyRequest, request: Request, session: Session, context: CurrentUser
):
    run = await find_run(run_id, session, context)
    settings = await effective_settings(session, request.app.state.settings)
    if run.source_snapshot.get("source_field_explicit"):
        # Manual choices are bound to this saved run, independently of the
        # default source used by other requests and the background worker.
        settings = settings.model_copy(
            update={"bitrix_text_field": run.source_snapshot["text_field"]}
        )
    run = await apply_run(
        session,
        request.app.state.bitrix,
        settings,
        run,
        payload,
    )
    return read_run(run)
