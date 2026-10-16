from contextlib import AsyncExitStack
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from conftest import TEST_PASSWORD
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.models import QualificationRun, User


@pytest_asyncio.fixture
async def actors(api, password_hash):
    app, manager = api
    async with app.state.session_factory() as session:
        session.add_all(
            [
                User(username="other-manager", password_hash=password_hash),
                User(username="administrator", role="admin", password_hash=password_hash),
            ]
        )
        await session.commit()
    async with AsyncExitStack() as stack:
        clients = {}
        for username in ("other-manager", "administrator"):
            client = await stack.enter_async_context(
                AsyncClient(
                    transport=ASGITransport(app=app),
                    base_url="http://test",
                    headers={"X-Requested-With": "leadflow"},
                )
            )
            login = await client.post(
                "/api/v1/auth/login", json={"username": username, "password": TEST_PASSWORD}
            )
            assert login.status_code == 200
            client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
            clients[username] = client
        yield app, manager, clients["other-manager"], clients["administrator"]


async def create(client, text="Нужна CRM на 15 человек"):
    response = await client.post("/api/v1/qualifications", json={"text": text})
    assert response.status_code == 200, response.text
    return response.json()


async def test_history_is_personal_and_deduplication_never_shares_records(actors):
    _, manager, other, admin = actors
    first = await create(manager)
    second = await create(other)
    third = await create(admin)
    assert len({first["id"], second["id"], third["id"]}) == 3
    assert first["created_by_username"] == "manager"
    assert (
        first["created_by_user_id"] == (await manager.get("/api/v1/auth/me")).json()["user"]["id"]
    )
    assert (await create(manager))["id"] == first["id"]
    assert (await manager.get("/api/v1/qualifications")).json() == [first]
    assert (await other.get("/api/v1/qualifications")).json() == [second]
    assert {r["id"] for r in (await admin.get("/api/v1/qualifications")).json()} == {
        first["id"],
        second["id"],
        third["id"],
    }
    assert (await admin.get(f"/api/v1/qualifications/{first['id']}")).json() == first
    assert (await manager.get(f"/api/v1/qualifications/{second['id']}")).status_code == 404
    assert (await other.get(f"/api/v1/qualifications/{first['id']}")).status_code == 404


async def test_access_filter_precedes_pagination(actors):
    _, manager, other, _ = actors
    oldest = await create(manager, "Нужна CRM на 10 человек")
    newest = await create(manager, "Нужна CRM на 20 человек")
    await create(other)
    assert (await manager.get("/api/v1/qualifications?limit=1")).json() == [newest]
    assert (await manager.get("/api/v1/qualifications?limit=1&offset=1")).json() == [oldest]
    assert (await manager.get("/api/v1/qualifications?offset=2")).json() == []


async def test_legacy_history_remains_admin_only(actors):
    app, manager, other, admin = actors
    legacy = await create(manager)
    async with app.state.session_factory() as session:
        run = await session.get(QualificationRun, legacy["id"])
        run.created_by_user_id = None
        run.created_by_username = None
        await session.commit()
    for client in (manager, other):
        assert (await client.get("/api/v1/qualifications")).json() == []
        assert (await client.get(f"/api/v1/qualifications/{legacy['id']}")).status_code == 404
    result = await admin.get(f"/api/v1/qualifications/{legacy['id']}")
    assert result.status_code == 200
    assert result.json()["created_by_user_id"] is None
    assert len((await admin.get("/api/v1/qualifications")).json()) == 1


async def test_foreign_crm_run_cannot_be_applied_but_admin_can_review_it(actors):
    app, manager, other, admin = actors
    app.state.bitrix = AsyncMock()
    app.state.bitrix.get_lead.return_value = {"id": 42, "comments": "Нужна CRM на 15 человек"}
    first = await manager.post("/api/v1/bitrix/leads/42/qualify")
    second = await other.post("/api/v1/bitrix/leads/42/qualify")
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] != second.json()["id"]
    app.state.bitrix.reset_mock()
    url = f"/api/v1/qualifications/{first.json()['id']}/apply"
    assert (await other.post(url, json={"attributes": ["users"]})).status_code == 404
    app.state.bitrix.get_lead.assert_not_awaited()
    app.state.bitrix.update_lead.assert_not_awaited()
    assert (await admin.post(url, json={"attributes": ["users"]})).status_code == 200
    app.state.bitrix.update_lead.assert_awaited_once_with(42, {"UF_CRM_AI_USERS": 15})


async def test_client_cannot_choose_qualification_owner(actors):
    app, manager, _, _ = actors
    async with app.state.session_factory() as session:
        other_id = await session.scalar(select(User.id).where(User.username == "other-manager"))
    response = await manager.post(
        "/api/v1/qualifications",
        json={"text": "Нужна CRM", "created_by_user_id": other_id},
    )
    assert response.status_code == 422


async def test_history_filters_combine_before_pagination_and_respect_ownership(actors):
    app, manager, other, admin = actors
    matching = await create(manager, "Нужна CRM на 10 человек")
    later_match = await create(manager, "Нужна CRM на 20 человек")
    wrong_priority = await create(manager, "Нужна CRM на 30 человек")
    wrong_status = await create(manager, "Нужна CRM на 40 человек")
    wrong_id = await create(manager, "Нужна CRM на 50 человек")
    foreign = await create(other)
    async with app.state.session_factory() as session:
        for record in (matching, later_match, wrong_priority, wrong_status, wrong_id, foreign):
            run = await session.get(QualificationRun, record["id"])
            run.lead_id = 43 if record == wrong_id else 42
            run.status = "applied" if record == wrong_status else "pending_review"
            run.result = {
                **run.result,
                "priority": "normal" if record == wrong_priority else "elevated",
            }
        await session.commit()

    url = "/api/v1/qualifications?lead_id=42&status=pending_review&priority=elevated"
    response = await manager.get(url)
    assert response.status_code == 200
    assert [r["id"] for r in response.json()] == [later_match["id"], matching["id"]]
    assert [r["id"] for r in (await manager.get(url + "&limit=1&offset=1")).json()] == [
        matching["id"]
    ]
    assert {r["id"] for r in (await admin.get(url)).json()} == {
        matching["id"],
        later_match["id"],
        foreign["id"],
    }
    assert [r["id"] for r in (await other.get(url)).json()] == [foreign["id"]]
    assert (await manager.get(url.replace("42", "999"))).json() == []
    assert [
        r["id"] for r in (await manager.get("/api/v1/qualifications?priority=normal")).json()
    ] == [wrong_priority["id"]]
    assert [
        r["id"] for r in (await manager.get("/api/v1/qualifications?status=applied")).json()
    ] == [wrong_status["id"]]


@pytest.mark.parametrize(
    "query",
    [
        "lead_id=0",
        "lead_id=-1",
        "lead_id=abc",
        "lead_id=9007199254740992",
        "status=invalid",
        "priority=high",
    ],
)
async def test_invalid_history_filters_are_rejected(api, query):
    _, client = api
    assert (await client.get(f"/api/v1/qualifications?{query}")).status_code == 422
