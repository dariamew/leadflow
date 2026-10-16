import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import event, func, select, update

from app.integrations.bitrix import BitrixError
from app.models import LeadJob, LeadMonitor, QualificationRun, User
from app.monitor import acquire, now, tick


@pytest_asyncio.fixture
async def monitor_api(api, password_hash):
    app, client = api
    async with app.state.session_factory() as session:
        await session.execute(update(User).where(User.username == "manager").values(role="admin"))
        recipient = User(username="recipient", password_hash=password_hash)
        session.add(recipient)
        await session.commit()
        owner_id = recipient.id
    # The fixture starts the application without the loop; tests drive durable
    # worker cycles explicitly instead of relying on timing or background sleeps.
    app.state.settings.monitor_worker_enabled = True
    app.state.settings.bitrix_webhook_url = SecretStr("https://portal.test/rest/1/secret/")
    app.state.bitrix = AsyncMock()
    app.state.bitrix.list_lead_ids.return_value = [10]
    app.state.bitrix.get_lead.return_value = {"comments": "Нужна CRM на 15 человек"}
    yield app, client, owner_id
    app.state.bitrix.update_lead.assert_not_awaited()


async def enable(fixture):
    app, client, owner = fixture
    response = await client.put(
        "/api/v1/bitrix/monitor",
        json={
            "enabled": True,
            "owner_id": owner,
            "interval_seconds": 60,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def jobs(app):
    async with app.state.session_factory() as session:
        return list(await session.scalars(select(LeadJob).order_by(LeadJob.lead_id)))


async def due(app):
    async with app.state.session_factory() as session:
        await session.execute(update(LeadJob).values(available_at=now() - timedelta(seconds=1)))
        await session.commit()


async def test_manager_cannot_control_or_read_monitor(api):
    _, client = api
    for method, path, payload in [
        ("GET", "", None),
        ("PUT", "", {"enabled": False}),
        ("POST", "/check", None),
        ("POST", "/jobs/00000000-0000-0000-0000-000000000000/retry", None),
    ]:
        assert (
            await client.request(method, "/api/v1/bitrix/monitor" + path, json=payload)
        ).status_code == 403


async def test_first_enable_sets_baseline_without_qualifying_old_leads(monitor_api):
    app, client, owner = monitor_api
    data = await enable(monitor_api)
    assert data["enabled"] and data["cursor"] == 10
    assert data["owner_id"] == owner
    assert data["jobs"] == []
    app.state.bitrix.list_lead_ids.assert_awaited_once_with(newest_first=True)
    app.state.bitrix.list_lead_ids.return_value = []
    await tick(app)
    app.state.bitrix.get_lead.assert_not_awaited()
    result = (await client.get("/api/v1/bitrix/monitor")).json()
    assert result["last_poll_at"] and result["worker_recent"]


async def test_new_leads_are_qualified_once_and_owned_by_recipient(monitor_api):
    app, client, owner = monitor_api
    await enable(monitor_api)
    app.state.bitrix.list_lead_ids.return_value = [11, 12]
    await tick(app)
    # A new worker/application instance can use the same database cursor/queue.
    await tick(app)
    rows = await jobs(app)
    assert [job.status for job in rows] == ["done", "done"]
    assert all(job.owner_id == owner and job.attempts == 1 for job in rows)
    history = (await client.get("/api/v1/qualifications")).json()
    assert len(history) == 2
    assert {item["lead_id"] for item in history} == {11, 12}
    assert all(
        item["created_by_user_id"] == owner and item["trigger"] == "automatic" for item in history
    )
    await client.post("/api/v1/bitrix/monitor/check")
    app.state.bitrix.list_lead_ids.return_value = []
    await tick(app)
    assert len(await jobs(app)) == 2
    assert app.state.bitrix.get_lead.await_count == 2


async def test_scan_drains_pages_with_id_cursor(monitor_api):
    app, client, _ = monitor_api
    await enable(monitor_api)

    async def listing(*, after):
        return [i for i in range(11, 113) if i > after][:50]

    app.state.bitrix.list_lead_ids.side_effect = listing
    for _ in range(3):
        await tick(app)
    assert len(await jobs(app)) == 102
    assert (await client.get("/api/v1/bitrix/monitor")).json()["cursor"] == 112


async def test_empty_text_is_retried_after_it_is_filled(monitor_api):
    app, _, _ = monitor_api
    await enable(monitor_api)
    app.state.bitrix.list_lead_ids.return_value = [11]
    app.state.bitrix.get_lead.return_value = {"comments": ""}
    await tick(app)
    row = (await jobs(app))[0]
    assert row.status == "retry" and row.last_error == "LEAD_TEXT_UNAVAILABLE"
    await due(app)
    app.state.bitrix.get_lead.return_value = {"comments": "Нужна CRM на 15 человек"}
    await tick(app)
    row = (await jobs(app))[0]
    assert row.status == "done" and row.attempts == 2


async def test_automatic_qualification_also_strips_crm_markup(monitor_api):
    app, client, _ = monitor_api
    await enable(monitor_api)
    app.state.bitrix.list_lead_ids.return_value = [11]
    app.state.bitrix.get_lead.return_value = {
        "comments": "[p]\nНужна [b]CRM[/b] на 15 человек\n[/p]"
    }
    await tick(app)
    row = (await jobs(app))[0]
    assert row.status == "done"
    result = (await client.get(f"/api/v1/qualifications/{row.run_id}")).json()
    assert result["source_text"] == "Нужна CRM на 15 человек"
    assert result["result"]["facts"]["users"]["evidence"] in result["source_text"]


async def test_errors_are_bounded_and_admin_can_retry(monitor_api):
    app, client, _ = monitor_api
    await enable(monitor_api)
    app.state.bitrix.list_lead_ids.return_value = [11]
    app.state.bitrix.get_lead.side_effect = BitrixError("ACCESS_DENIED")
    for _ in range(5):
        await due(app)
        await tick(app)
    row = (await jobs(app))[0]
    assert row.status == "failed" and row.attempts == 5
    assert row.last_error == "ACCESS_DENIED"
    response = await client.post(f"/api/v1/bitrix/monitor/jobs/{row.id}/retry")
    assert response.status_code == 200
    app.state.bitrix.get_lead.side_effect = None
    await tick(app)
    assert (await jobs(app))[0].status == "done"


async def test_poll_failure_does_not_advance_cursor_or_stop_existing_queue(monitor_api):
    app, client, _ = monitor_api
    await enable(monitor_api)
    app.state.bitrix.list_lead_ids.return_value = [11, 12]
    await tick(app)
    await client.post("/api/v1/bitrix/monitor/check")
    app.state.bitrix.list_lead_ids.side_effect = BitrixError("QUERY_LIMIT_EXCEEDED")
    await tick(app)
    state = (await client.get("/api/v1/bitrix/monitor")).json()
    assert state["cursor"] == 12 and state["last_error"] == "QUERY_LIMIT_EXCEEDED"
    assert state["counts"]["done"] == 2


async def test_pause_preserves_cursor_and_queue(monitor_api):
    app, client, owner = monitor_api
    await enable(monitor_api)
    app.state.bitrix.list_lead_ids.return_value = [11, 12]
    await tick(app)
    paused = await client.put("/api/v1/bitrix/monitor", json={"enabled": False, "owner_id": owner})
    assert paused.status_code == 200
    await tick(app)
    assert [row.status for row in await jobs(app)] == ["done", "queued"]
    await enable(monitor_api)
    app.state.bitrix.list_lead_ids.return_value = [13]
    await tick(app)
    assert [row.status for row in await jobs(app)] == ["done", "done", "queued"]
    assert (
        sum(
            call.kwargs.get("newest_first", False)
            for call in app.state.bitrix.list_lead_ids.await_args_list
        )
        == 1
    )


async def test_lease_excludes_parallel_workers_and_cancelled_job_is_recovered(monitor_api):
    app, _, _ = monitor_api
    await enable(monitor_api)
    app.state.bitrix.list_lead_ids.return_value = [11]
    entered = asyncio.Event()
    hold = asyncio.Event()

    async def get_lead(_):
        entered.set()
        await hold.wait()
        return {"comments": "Нужна CRM на 15 человек"}

    app.state.bitrix.get_lead.side_effect = get_lead
    first = asyncio.create_task(tick(app))
    await asyncio.wait_for(entered.wait(), timeout=5)
    try:
        await tick(app)
        assert app.state.bitrix.get_lead.await_count == 1
    finally:
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
    app.state.bitrix.get_lead.side_effect = None
    await tick(app)
    assert (await jobs(app))[0].status == "done"
    async with app.state.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(QualificationRun)) == 1


async def test_disabled_owner_fails_without_sending_source_to_model(monitor_api):
    app, _, owner = monitor_api
    await enable(monitor_api)
    app.state.bitrix.list_lead_ids.return_value = [11]
    async with app.state.session_factory() as session:
        await session.execute(update(User).where(User.id == owner).values(is_active=False))
        await session.commit()
    await tick(app)
    row = (await jobs(app))[0]
    assert row.status == "failed" and row.last_error == "OWNER_UNAVAILABLE"
    app.state.bitrix.get_lead.assert_not_awaited()


async def test_portal_change_pauses_processing(monitor_api):
    app, client, _ = monitor_api
    await enable(monitor_api)
    app.state.settings.bitrix_webhook_url = SecretStr("https://different.test/rest/1/secret/")
    await tick(app)
    state = (await client.get("/api/v1/bitrix/monitor")).json()
    assert not state["enabled"] and state["last_error"] == "PORTAL_CHANGED"
    app.state.bitrix.get_lead.assert_not_awaited()


async def test_expired_lease_can_be_reclaimed(monitor_api):
    app, _, _ = monitor_api
    await enable(monitor_api)
    token = await acquire(app.state.session_factory)
    assert token and await acquire(app.state.session_factory) is None
    async with app.state.session_factory() as session:
        await session.execute(update(LeadMonitor).values(lease_until=now() - timedelta(seconds=1)))
        await session.commit()
    assert await acquire(app.state.session_factory) not in (None, token)


async def test_pause_fences_a_listing_that_was_already_in_flight(monitor_api):
    app, client, owner = monitor_api
    await enable(monitor_api)
    entered, release = asyncio.Event(), asyncio.Event()

    async def listing(**_):
        entered.set()
        await release.wait()
        return [11]

    app.state.bitrix.list_lead_ids.side_effect = listing
    worker = asyncio.create_task(tick(app))
    await asyncio.wait_for(entered.wait(), timeout=5)
    try:
        paused = await client.put(
            "/api/v1/bitrix/monitor", json={"enabled": False, "owner_id": owner}
        )
        assert paused.status_code == 200
    finally:
        release.set()
        await worker
    state = (await client.get("/api/v1/bitrix/monitor")).json()
    assert state["cursor"] == 10 and state["jobs"] == []


async def test_cursor_rolls_back_if_queue_insertion_fails(monitor_api):
    app, client, _ = monitor_api
    await enable(monitor_api)
    app.state.bitrix.list_lead_ids.return_value = [11]

    def fail_insert(*_):
        raise RuntimeError("simulated interrupted insert")

    event.listen(LeadJob, "before_insert", fail_insert)
    try:
        with pytest.raises(RuntimeError, match="interrupted insert"):
            await tick(app)
    finally:
        event.remove(LeadJob, "before_insert", fail_insert)
    state = (await client.get("/api/v1/bitrix/monitor")).json()
    assert state["cursor"] == 10 and state["jobs"] == []


async def test_saved_result_is_reused_after_lost_job_acknowledgement(monitor_api):
    app, _, _ = monitor_api
    await enable(monitor_api)
    app.state.bitrix.list_lead_ids.return_value = [11]
    await tick(app)
    original = (await jobs(app))[0].run_id
    async with app.state.session_factory() as session:
        await session.execute(
            update(LeadJob).values(status="processing", run_id=None, claim_token="crashed")
        )
        await session.commit()
    app.state.pipeline.qualify = AsyncMock()
    await tick(app)
    row = (await jobs(app))[0]
    assert row.status == "done" and row.run_id == original
    app.state.pipeline.qualify.assert_not_awaited()
