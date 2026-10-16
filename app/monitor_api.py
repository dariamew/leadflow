from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update

from app.administration import Administrator, lock_administration
from app.auth import Session
from app.models import LeadJob, LeadMonitor, User
from app.monitor import now, portal_key, utc

router = APIRouter(prefix="/api/v1/bitrix/monitor", tags=["lead monitoring"])


class MonitorUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    owner_id: UUID | None = None
    interval_seconds: int = Field(default=60, ge=30, le=3600)


async def monitor_read(session, settings):
    state = await session.get(LeadMonitor, 1, populate_existing=True)
    if state is None:
        raise HTTPException(503, "Lead monitoring is not initialized")
    recipients = (
        await session.scalars(
            select(User)
            .where(
                User.is_active.is_(True),
                User.password_hash.is_not(None),
            )
            .order_by(User.username)
        )
    ).all()
    key = portal_key(settings)
    counts = dict(
        (
            await session.execute(
                select(LeadJob.status, func.count())
                .where(
                    LeadJob.portal_key == key,
                )
                .group_by(LeadJob.status)
            )
        ).all()
    )
    jobs = (
        await session.execute(
            select(LeadJob, User.username)
            .outerjoin(
                User,
                User.id == LeadJob.owner_id,
            )
            .where(LeadJob.portal_key == key)
            .order_by(LeadJob.created_at.desc(), LeadJob.id)
            .limit(20)
        )
    ).all()
    return {
        "enabled": state.enabled,
        "owner_id": state.owner_id,
        "interval_seconds": state.interval_seconds,
        "cursor": state.cursor,
        "last_poll_at": utc(state.last_poll_at) if state.last_poll_at else None,
        "last_error": "PORTAL_CHANGED"
        if state.portal_key and state.portal_key != key
        else state.last_error,
        "worker_available": settings.monitor_worker_enabled,
        "worker_recent": bool(
            state.heartbeat_at and utc(state.heartbeat_at) > now() - timedelta(seconds=660)
        ),
        "bitrix_configured": key is not None,
        "recipients": [{"id": user.id, "username": user.username} for user in recipients],
        "counts": counts,
        "jobs": [
            {
                "id": job.id,
                "lead_id": job.lead_id,
                "owner_username": username,
                "status": job.status,
                "attempts": job.attempts,
                "last_error": job.last_error,
                "run_id": job.run_id,
                "created_at": utc(job.created_at),
                "available_at": utc(job.available_at),
            }
            for job, username in jobs
        ],
    }


@router.get("")
async def status(request: Request, session: Session, actor: Administrator):
    return await monitor_read(session, request.app.state.settings)


@router.put("")
async def configure(
    payload: MonitorUpdate, request: Request, session: Session, actor: Administrator
):
    settings = request.app.state.settings
    state = await session.get(LeadMonitor, 1)
    if state is None:
        raise HTTPException(503, "Lead monitoring is not initialized")
    revision = state.revision
    cursor = state.cursor
    key = portal_key(settings)
    initialize = cursor is None or state.portal_key != key
    # Release the read transaction before contacting Bitrix24.
    await session.commit()
    if payload.enabled:
        if not key:
            raise HTTPException(409, "Bitrix webhook is not configured")
        if not settings.monitor_worker_enabled:
            raise HTTPException(409, "Lead monitoring worker is disabled")
        if payload.owner_id is None:
            raise HTTPException(422, "Choose a lead recipient")
        if initialize:
            ids = await request.app.state.bitrix.list_lead_ids(newest_first=True)
            cursor = max(ids, default=0)
    await lock_administration(session, actor)
    if payload.enabled:
        owner = await session.get(User, str(payload.owner_id))
        if owner is None or not owner.is_active or owner.password_hash is None:
            raise HTTPException(422, "Choose an active lead recipient")
    changed = await session.execute(
        update(LeadMonitor)
        .where(
            LeadMonitor.id == 1,
            LeadMonitor.revision == revision,
        )
        .values(
            enabled=payload.enabled,
            owner_id=str(payload.owner_id) if payload.owner_id else None,
            interval_seconds=payload.interval_seconds,
            portal_key=key if payload.enabled else LeadMonitor.portal_key,
            cursor=cursor if payload.enabled else LeadMonitor.cursor,
            revision=LeadMonitor.revision + 1,
            next_poll_at=now(),
            last_error=None,
        )
    )
    if changed.rowcount != 1:
        raise HTTPException(409, "Monitoring settings changed; refresh")
    await session.commit()
    return await monitor_read(session, settings)


@router.post("/check")
async def check_now(request: Request, session: Session, actor: Administrator):
    await lock_administration(session, actor)
    changed = await session.execute(
        update(LeadMonitor)
        .where(
            LeadMonitor.id == 1,
            LeadMonitor.enabled.is_(True),
        )
        .values(next_poll_at=now(), revision=LeadMonitor.revision + 1)
    )
    if changed.rowcount != 1:
        raise HTTPException(409, "Enable lead monitoring first")
    await session.commit()
    return await monitor_read(session, request.app.state.settings)


@router.post("/jobs/{job_id}/retry")
async def retry(job_id: UUID, request: Request, session: Session, actor: Administrator):
    await lock_administration(session, actor)
    changed = await session.execute(
        update(LeadJob)
        .where(
            LeadJob.id == str(job_id),
            LeadJob.portal_key == portal_key(request.app.state.settings),
            LeadJob.status == "failed",
        )
        .values(status="queued", attempts=0, available_at=now(), last_error=None)
    )
    if changed.rowcount != 1:
        raise HTTPException(409, "Only failed lead jobs can be retried")
    await session.commit()
    return await monitor_read(session, request.app.state.settings)
