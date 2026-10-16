from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import select, update

from app.models import BitrixSource, QualificationRun, User
from app.monitor import tick
from app.source_settings import effective_settings

CUSTOM = "UF_CRM_REQUEST_TEXT"
CATALOGUE = {
    "fields": {
        "comments": {"type": "text", "title": "Комментарий", "isMultiple": False},
        "title": {"type": "string", "title": "Название лида"},
        CUSTOM: {"type": "string", "title": "Текст заявки", "isMultiple": False},
        "id": {"type": "integer", "title": "ID"},
        "UF_CRM_MULTI": {"type": "string", "isMultiple": True},
        "UF_CRM_LIST": {"type": "enumeration"},
    }
}


@pytest_asyncio.fixture
async def source_api(api):
    app, client = api
    async with app.state.session_factory() as session:
        await session.execute(update(User).where(User.username == "manager").values(role="admin"))
        await session.commit()
    app.state.settings.bitrix_webhook_url = SecretStr("https://portal.test/rest/1/secret/")
    app.state.bitrix = AsyncMock()
    app.state.bitrix.get_lead_fields.return_value = CATALOGUE
    app.state.bitrix.get_lead.return_value = {
        "comments": "Текст комментария",
        CUSTOM: "[p]Нужна CRM на 15 человек[/p]",
    }
    return app, client


async def save(client, field=CUSTOM):
    response = await client.put("/api/v1/bitrix/source", json={"text_field": field})
    assert response.status_code == 200, response.text
    return response.json()


async def test_source_settings_require_administrator(api):
    _, client = api
    assert (await client.get("/api/v1/bitrix/source")).status_code == 403
    assert (await client.get("/api/v1/bitrix/source/fields")).status_code == 403
    assert (
        await client.put("/api/v1/bitrix/source", json={"text_field": CUSTOM})
    ).status_code == 403


async def test_catalogue_includes_only_single_text_fields(source_api):
    _, client = source_api
    response = await client.get("/api/v1/bitrix/source/fields")
    assert response.status_code == 200
    assert {field["code"] for field in response.json()} == {"comments", "title", CUSTOM}
    assert response.json()[0]["code"] == "comments"


async def test_saved_field_is_persistent_and_used_by_manual_qualification(source_api):
    app, client = source_api
    saved = await save(client)
    assert saved["text_field"] == CUSTOM and saved["saved"]
    assert (await client.get("/api/v1/config")).json()["bitrix_text_field"] == CUSTOM
    run = await client.post("/api/v1/bitrix/leads/42/qualify")
    assert run.status_code == 200
    assert run.json()["source_text"] == "Нужна CRM на 15 человек"
    assert run.json()["result"]["facts"]["users"]["value"] == 15
    async with app.state.session_factory() as session:
        stored = await session.get(QualificationRun, run.json()["id"])
        assert stored.source_snapshot["text_field"] == CUSTOM
        restored = await effective_settings(session, app.state.settings.model_copy())
        assert restored.bitrix_text_field == CUSTOM
    assert app.state.settings.bitrix_text_field == "comments"


async def test_automatic_qualification_uses_saved_source_field(source_api):
    app, client = source_api
    await save(client)
    app.state.settings.monitor_worker_enabled = True
    app.state.bitrix.list_lead_ids.return_value = [10]
    user = (await client.get("/api/v1/auth/me")).json()["user"]
    enabled = await client.put(
        "/api/v1/bitrix/monitor", json={"enabled": True, "owner_id": user["id"]}
    )
    assert enabled.status_code == 200
    app.state.bitrix.list_lead_ids.return_value = [11]
    await tick(app)
    history = (await client.get("/api/v1/qualifications")).json()
    assert len(history) == 1 and history[0]["trigger"] == "automatic"
    assert history[0]["source_text"] == "Нужна CRM на 15 человек"
    app.state.bitrix.update_lead.assert_not_awaited()


@pytest.mark.parametrize("field", ["id", "UF_CRM_MULTI", "UF_CRM_LIST", "UF_CRM_MISSING"])
async def test_nontext_multiple_and_missing_fields_cannot_be_saved(source_api, field):
    _, client = source_api
    response = await client.put("/api/v1/bitrix/source", json={"text_field": field})
    assert response.status_code == 422
    assert (await client.get("/api/v1/bitrix/source")).json()["text_field"] == "comments"


async def test_empty_selected_field_does_not_fall_back_to_comments(source_api):
    app, client = source_api
    await save(client)
    app.state.bitrix.get_lead.return_value = {CUSTOM: "", "comments": "Нужна CRM на 15 человек"}
    response = await client.post("/api/v1/bitrix/leads/42/qualify")
    assert response.status_code == 422


async def test_field_change_blocks_applying_old_result_but_new_result_can_be_applied(source_api):
    app, client = source_api
    app.state.bitrix.get_lead.return_value["comments"] = "Нужна CRM на 10 человек"
    original = (await client.post("/api/v1/bitrix/leads/42/qualify")).json()
    await save(client)
    old = await client.post(
        f"/api/v1/qualifications/{original['id']}/apply", json={"attributes": ["users"]}
    )
    assert old.status_code == 409
    app.state.bitrix.update_lead.assert_not_awaited()
    new = (await client.post("/api/v1/bitrix/leads/42/qualify")).json()
    assert new["id"] != original["id"]
    applied = await client.post(
        f"/api/v1/qualifications/{new['id']}/apply", json={"attributes": ["users"]}
    )
    assert applied.status_code == 200
    app.state.bitrix.update_lead.assert_awaited_once_with(42, {"UF_CRM_AI_USERS": 15})


async def test_source_choice_does_not_leak_to_another_portal(source_api):
    app, client = source_api
    await save(client)
    app.state.settings.bitrix_webhook_url = SecretStr("https://other.test/rest/1/secret/")
    read = (await client.get("/api/v1/bitrix/source")).json()
    assert read["portal_changed"] and not read["saved"] and read["text_field"] == "comments"
    assert (await client.get("/api/v1/config")).json()["bitrix_text_field"] == "comments"


async def test_existing_environment_default_is_preserved_until_ui_save(source_api):
    app, client = source_api
    app.state.settings.bitrix_text_field = "title"
    read = (await client.get("/api/v1/bitrix/source")).json()
    assert read["text_field"] == "title" and not read["saved"]
    async with app.state.session_factory() as session:
        assert await session.scalar(select(BitrixSource.text_field)) is None


async def test_manager_can_choose_source_for_one_analysis_without_changing_defaults(api):
    app, client = api
    app.state.bitrix = AsyncMock()
    app.state.bitrix.get_lead_fields.return_value = CATALOGUE
    app.state.bitrix.get_lead.return_value = {
        "comments": "Комментарий",
        CUSTOM: "[p]Нужна CRM на 15 человек[/p]",
    }
    catalogue = await client.get("/api/v1/bitrix/text-fields")
    assert catalogue.status_code == 200
    assert CUSTOM in {field["code"] for field in catalogue.json()}
    response = await client.post("/api/v1/bitrix/leads/42/qualify", json={"text_field": CUSTOM})
    assert response.status_code == 200, response.text
    run = response.json()
    assert run["source_text"] == "Нужна CRM на 15 человек"
    assert run["source_text_field"] == CUSTOM and run["source_field_explicit"]
    assert (await client.get(f"/api/v1/qualifications/{run['id']}")).json() == run
    assert (await client.get("/api/v1/config")).json()["bitrix_text_field"] == "comments"
    async with app.state.session_factory() as session:
        assert await session.scalar(select(BitrixSource.text_field)) is None


async def test_explicit_source_can_be_applied_after_default_changes(source_api):
    app, client = source_api
    original = (
        await client.post("/api/v1/bitrix/leads/42/qualify", json={"text_field": CUSTOM})
    ).json()
    await save(client, "title")
    # Changes in an unrelated comment must not affect the chosen source either.
    app.state.bitrix.get_lead.return_value["comments"] = "Другой комментарий"
    applied = await client.post(
        f"/api/v1/qualifications/{original['id']}/apply", json={"attributes": ["users"]}
    )
    assert applied.status_code == 200, applied.text
    app.state.bitrix.update_lead.assert_awaited_once_with(42, {"UF_CRM_AI_USERS": 15})
    assert (await client.get("/api/v1/config")).json()["bitrix_text_field"] == "title"


async def test_changed_explicit_source_blocks_writes(source_api):
    app, client = source_api
    original = (
        await client.post("/api/v1/bitrix/leads/42/qualify", json={"text_field": CUSTOM})
    ).json()
    app.state.bitrix.get_lead.return_value[CUSTOM] = "Теперь нужны 30 пользователей"
    applied = await client.post(
        f"/api/v1/qualifications/{original['id']}/apply", json={"attributes": ["users"]}
    )
    assert applied.status_code == 409
    app.state.bitrix.update_lead.assert_not_awaited()


@pytest.mark.parametrize("field", ["id", "UF_CRM_MULTI", "UF_CRM_MISSING"])
async def test_invalid_explicit_source_is_rejected_before_loading_lead(source_api, field):
    app, client = source_api
    response = await client.post("/api/v1/bitrix/leads/42/qualify", json={"text_field": field})
    assert response.status_code == 422
    app.state.bitrix.get_lead.assert_not_awaited()


async def test_empty_explicit_source_never_reads_comments(source_api):
    app, client = source_api
    app.state.bitrix.get_lead.return_value[CUSTOM] = ""
    app.state.bitrix.get_lead.return_value["comments"] = "Нужна CRM на 15 человек"
    response = await client.post("/api/v1/bitrix/leads/42/qualify", json={"text_field": CUSTOM})
    assert response.status_code == 422


async def test_manual_default_and_explicit_source_are_deduplicated_separately(source_api):
    app, client = source_api
    app.state.bitrix.get_lead.return_value["comments"] = "Нужна CRM на 15 человек"
    first = (await client.post("/api/v1/bitrix/leads/42/qualify")).json()
    explicit = (
        await client.post("/api/v1/bitrix/leads/42/qualify", json={"text_field": "comments"})
    ).json()
    assert not first["source_field_explicit"] and explicit["source_field_explicit"]
    assert first["id"] != explicit["id"]
    again = (
        await client.post("/api/v1/bitrix/leads/42/qualify", json={"text_field": "comments"})
    ).json()
    assert again["id"] == explicit["id"]
