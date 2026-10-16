"""Single-portal polling with a durable cursor, queue, retries and a DB lease."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import or_, select, update
from sqlalchemy.exc import SQLAlchemyError

from app.integrations.bitrix import BitrixError
from app.models import LeadJob, LeadMonitor, User
from app.qualification.extractors import ExtractionError
from app.service import lead_text, qualify_and_store
from app.source_settings import effective_settings, portal_key

LEASE_SECONDS = 600
MAX_ATTEMPTS = 5
logger = logging.getLogger(__name__)


def now():
    return datetime.now(UTC)


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


async def acquire(factory):
    token = str(uuid4())
    stamp = now()
    async with factory() as session:
        claim = await session.execute(
            update(LeadMonitor)
            .where(
                LeadMonitor.id == 1,
                LeadMonitor.enabled.is_(True),
                or_(LeadMonitor.lease_until.is_(None), LeadMonitor.lease_until < stamp),
            )
            .values(
                lease_token=token,
                lease_until=stamp + timedelta(seconds=LEASE_SECONDS),
                heartbeat_at=stamp,
            )
        )
        await session.commit()
        return token if claim.rowcount == 1 else None


async def poll(application, token, state):
    factory = application.state.session_factory
    client = application.state.bitrix
    ids = await client.list_lead_ids(after=state.cursor or 0)
    stamp = now()
    async with factory() as session:
        # Cursor advance and insertion are atomic. Configuration changes fence
        # responses that were already in flight when polling was paused/edited.
        saved = await session.execute(
            update(LeadMonitor)
            .where(
                LeadMonitor.id == 1,
                LeadMonitor.enabled.is_(True),
                LeadMonitor.lease_token == token,
                LeadMonitor.lease_until > stamp,
                LeadMonitor.revision == state.revision,
            )
            .values(
                cursor=max(ids) if ids else state.cursor,
                last_poll_at=stamp,
                last_error=None,
                next_poll_at=stamp
                + timedelta(seconds=0 if len(ids) == 50 else state.interval_seconds),
            )
        )
        if saved.rowcount != 1:
            await session.rollback()
            return
        existing = set(
            await session.scalars(
                select(LeadJob.lead_id).where(
                    LeadJob.portal_key == state.portal_key,
                    LeadJob.lead_id.in_(ids),
                )
            )
        )
        for identity in ids:
            if identity not in existing:
                session.add(
                    LeadJob(
                        portal_key=state.portal_key,
                        lead_id=identity,
                        owner_id=state.owner_id,
                        available_at=stamp,
                    )
                )
        await session.commit()


async def process_one(application, token):
    factory = application.state.session_factory
    settings = application.state.settings
    async with factory() as session:
        state = await session.get(LeadMonitor, 1)
        if not state.enabled or state.lease_token != token or utc(state.lease_until) <= now():
            return
        job = await session.scalar(
            select(LeadJob)
            .where(
                LeadJob.portal_key == portal_key(settings),
                or_(
                    (LeadJob.status.in_(["queued", "retry"])) & (LeadJob.available_at <= now()),
                    LeadJob.status == "processing",
                ),
            )
            .order_by(LeadJob.created_at, LeadJob.lead_id, LeadJob.id)
            .limit(1)
        )
        if job is None:
            return
        job.status = "processing"
        job.claim_token = token
        job.attempts += 1
        await session.commit()
        identity, attempts = job.id, job.attempts
        error = None
        run_id = None
        try:
            settings = await effective_settings(session, settings)
            owner = await session.get(User, job.owner_id) if job.owner_id else None
            if owner is None or not owner.is_active or owner.password_hash is None:
                error = "OWNER_UNAVAILABLE"
            else:
                lead = await application.state.bitrix.get_lead(job.lead_id)
                run = await qualify_and_store(
                    session,
                    application.state.pipeline,
                    settings,
                    lead_text(lead, settings),
                    job.lead_id,
                    lead,
                    created_by=owner,
                    trigger="automatic",
                )
                run_id = run.id
        except BitrixError as failure:
            error = failure.code
        except ExtractionError:
            error = "EXTRACTION_FAILED"
        except HTTPException:
            # Lead text is often filled shortly after the lead itself is created.
            error = "LEAD_TEXT_UNAVAILABLE"
        except SQLAlchemyError:
            await session.rollback()
            error = "DATABASE_UNAVAILABLE"
        except Exception:
            # Do not persist provider messages, request URLs or source text.
            await session.rollback()
            error = "PROCESSING_FAILED"
        await session.rollback()

    async with factory() as session:
        # A worker whose lease expired cannot overwrite its successor's status.
        alive = (
            select(LeadMonitor.id)
            .where(
                LeadMonitor.lease_token == token,
                LeadMonitor.lease_until > now(),
            )
            .exists()
        )
        await session.execute(
            update(LeadJob)
            .where(
                LeadJob.id == identity,
                LeadJob.claim_token == token,
                alive,
            )
            .values(
                status="done"
                if run_id
                else "failed"
                if attempts >= MAX_ATTEMPTS or error == "OWNER_UNAVAILABLE"
                else "retry",
                run_id=run_id,
                last_error=error,
                claim_token=None,
                available_at=now() + timedelta(seconds=30 * 2 ** min(attempts - 1, 4)),
            )
        )
        await session.commit()


async def tick(application):
    factory = application.state.session_factory
    token = await acquire(factory)
    if token is None:
        return
    try:
        async with factory() as session:
            state = await session.get(LeadMonitor, 1)
        if state.portal_key != portal_key(application.state.settings):
            async with factory() as session:
                await session.execute(
                    update(LeadMonitor)
                    .where(
                        LeadMonitor.lease_token == token,
                    )
                    .values(enabled=False, last_error="PORTAL_CHANGED")
                )
                await session.commit()
            return
        if state.next_poll_at is None or utc(state.next_poll_at) <= now():
            try:
                await poll(application, token, state)
            except BitrixError as failure:
                async with factory() as session:
                    await session.execute(
                        update(LeadMonitor)
                        .where(
                            LeadMonitor.lease_token == token,
                            LeadMonitor.revision == state.revision,
                        )
                        .values(
                            last_error=failure.code,
                            next_poll_at=now() + timedelta(seconds=state.interval_seconds),
                        )
                    )
                    await session.commit()
        await process_one(application, token)
    finally:
        async with factory() as session:
            await session.execute(
                update(LeadMonitor)
                .where(LeadMonitor.lease_token == token)
                .values(
                    lease_token=None,
                    lease_until=None,
                    heartbeat_at=now(),
                )
            )
            await session.commit()


async def run_monitor(application):
    while True:
        try:
            await tick(application)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Missing migrations or an unavailable DB must not kill the loop or
            # expose webhook URLs/SQL parameters through exception tracebacks.
            logger.warning("Lead monitor cycle failed; retrying")
        await asyncio.sleep(2)
