from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from app.integrations.bitrix import BitrixError


async def test_authentication_required(anonymous_api):
    _, client = anonymous_api
    response = await client.get("/api/v1/qualifications", headers={"X-API-Key": "wrong"})
    assert response.status_code == 401


async def test_frontend_config_contains_no_secrets(api):
    _, client = api
    response = await client.get("/api/v1/config")
    assert response.status_code == 200
    assert response.json() == {
        "extractor": "rules",
        "bitrix_configured": False,
        "writable_attributes": ["users", "category"],
        "bitrix_text_field": "comments",
    }


async def test_frontend_write_options_include_only_available_mapped_suggestions(api):
    run, _ = await prepare_lead(api, existing_users=10)
    assert run["write_options"] == [
        {"attribute": "users", "suggested_value": 15, "current_value": 10},
        {"attribute": "category", "suggested_value": "crm_implementation", "current_value": None},
    ]


async def test_text_results_do_not_offer_crm_write_options(api):
    _, client = api
    result = await client.post("/api/v1/qualifications", json={"text": "Нужна CRM на 15 человек"})
    assert result.json()["write_options"] == []


async def test_store_read_list_and_deduplicate(api):
    _, client = api
    payload = {"text": "Нужна CRM на 15 человек. Бюджет до 250 тысяч рублей."}
    first = await client.post("/api/v1/qualifications", json=payload)
    assert first.status_code == 200, first.text
    run = first.json()
    assert run["result"]["facts"]["users"]["value"] == 15
    assert run["status"] == "pending_review"
    again = await client.post("/api/v1/qualifications", json=payload)
    assert again.json()["id"] == run["id"]
    assert (await client.get(f"/api/v1/qualifications/{run['id']}")).json() == run
    assert len((await client.get("/api/v1/qualifications")).json()) == 1


async def test_crm_markup_is_removed_before_analysis_and_citations(api):
    app, client = api
    app.state.bitrix = AsyncMock()
    app.state.bitrix.get_lead.return_value = {
        "id": 42,
        "comments": "[p]Нужна [b]CRM[/b] на 15 человек.[/p]",
    }
    response = await client.post("/api/v1/bitrix/leads/42/qualify")
    assert response.status_code == 200
    run = response.json()
    assert run["source_text"] == "Нужна CRM на 15 человек."
    assert run["result"]["facts"]["users"]["evidence"] in run["source_text"]
    assert run["result"]["facts"]["category_evidence"] in run["source_text"]
    # Editor-only changes do not invalidate the same visible application.
    app.state.bitrix.get_lead.return_value["comments"] = "<p>Нужна <b>CRM</b> на 15 человек.</p>"
    applied = await client.post(
        f"/api/v1/qualifications/{run['id']}/apply", json={"attributes": ["users"]}
    )
    assert applied.status_code == 200, applied.text


async def test_old_crm_results_are_cleaned_together_with_citations(api):
    from app.models import QualificationRun

    app, client = api
    run, _ = await prepare_lead(api)
    async with app.state.session_factory() as session:
        stored = await session.get(QualificationRun, run["id"])
        stored.source_text = "[p]Нужна CRM на 15 человек.[/p]"
        data = deepcopy(stored.result)
        data["facts"]["users"]["evidence"] = "[p]Нужна CRM на 15 человек.[/p]"
        stored.result = data
        stored.source_snapshot = {
            key: value for key, value in stored.source_snapshot.items() if key != "text_format"
        }
        await session.commit()
    loaded = (await client.get(f"/api/v1/qualifications/{run['id']}")).json()
    assert loaded["source_text"] == "Нужна CRM на 15 человек."
    assert loaded["result"]["facts"]["users"]["evidence"] == loaded["source_text"]


async def test_escaped_literal_markup_is_decoded_once_not_removed_on_read(api):
    app, client = api
    app.state.bitrix = AsyncMock()
    app.state.bitrix.get_lead.return_value = {
        "comments": "<p>Пример кода: &lt;b&gt;тест&lt;/b&gt;. Нужна CRM на 15 человек.</p>"
    }
    response = await client.post("/api/v1/bitrix/leads/42/qualify")
    assert response.status_code == 200
    run = response.json()
    assert run["source_text"] == "Пример кода: <b>тест</b>. Нужна CRM на 15 человек."
    assert (await client.get(f"/api/v1/qualifications/{run['id']}")).json()["source_text"] == run[
        "source_text"
    ]
    applied = await client.post(
        f"/api/v1/qualifications/{run['id']}/apply", json={"attributes": ["users"]}
    )
    assert applied.status_code == 200


async def test_manual_text_is_preserved_verbatim(api):
    _, client = api
    text = "Нужна CRM. Пример кода: [p]тест[/p]"
    run = (await client.post("/api/v1/qualifications", json={"text": text})).json()
    assert run["source_text"] == text


async def test_legacy_deadline_writes_the_same_clean_value_that_is_displayed(api):
    from app.models import QualificationRun

    app, client = api
    app.state.settings.bitrix_field_map = {"deadline_text": "UF_CRM_DEADLINE"}
    app.state.bitrix = AsyncMock()
    source = "[p]Нужна CRM на 15 человек. Запуск до конца месяца.[/p]"
    app.state.bitrix.get_lead.return_value = {"comments": source}
    response = await client.post("/api/v1/bitrix/leads/42/qualify")
    assert response.status_code == 200
    run_id = response.json()["id"]
    async with app.state.session_factory() as session:
        stored = await session.get(QualificationRun, run_id)
        stored.source_text = source
        stored.source_snapshot = {
            key: value for key, value in stored.source_snapshot.items() if key != "text_format"
        }
        data = deepcopy(stored.result)
        data["facts"]["deadline"] = {
            "value": "до конца месяца.[/p]",
            "evidence": "до конца месяца.[/p]",
        }
        stored.result = data
        await session.commit()
    loaded = (await client.get(f"/api/v1/qualifications/{run_id}")).json()
    assert loaded["write_options"][0]["suggested_value"] == "до конца месяца."
    applied = await client.post(
        f"/api/v1/qualifications/{run_id}/apply", json={"attributes": ["deadline_text"]}
    )
    assert applied.status_code == 200
    app.state.bitrix.update_lead.assert_awaited_once_with(
        42, {"UF_CRM_DEADLINE": "до конца месяца."}
    )


@pytest.mark.parametrize("payload", [{"text": " "}, {"text": ""}, {"text": "a" * 20001}])
async def test_invalid_input_rejected(api, payload):
    _, client = api
    assert (await client.post("/api/v1/qualifications", json=payload)).status_code == 422


async def test_health(api):
    _, client = api
    assert (await client.get("/health/live")).status_code == 200
    assert (await client.get("/health/ready")).status_code == 200


async def test_bitrix_unconfigured(api):
    _, client = api
    response = await client.post("/api/v1/bitrix/leads/42/qualify")
    assert response.status_code == 503
    assert response.json()["code"] == "NOT_CONFIGURED"


async def prepare_lead(api, existing_users=None):
    app, client = api
    lead = {"id": 42, "comments": "Нужна CRM на 15 человек.", "UF_CRM_AI_USERS": existing_users}
    app.state.bitrix = AsyncMock()
    app.state.bitrix.get_lead.return_value = lead
    response = await client.post("/api/v1/bitrix/leads/42/qualify")
    assert response.status_code == 200, response.text
    app.state.bitrix.update_lead.assert_not_awaited()
    return response.json(), lead


async def test_only_selected_fields_are_written_and_repeat_is_idempotent(api):
    app, client = api
    run, _ = await prepare_lead(api)
    url = f"/api/v1/qualifications/{run['id']}/apply"
    approval = {"attributes": ["users"]}
    result = await client.post(url, json=approval)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "applied"
    assert result.json()["applied_attributes"] == ["users"]
    app.state.bitrix.update_lead.assert_awaited_once_with(42, {"UF_CRM_AI_USERS": 15})
    assert (await client.post(url, json=approval)).status_code == 200
    assert app.state.bitrix.update_lead.await_count == 1


async def test_stale_source_blocks_writes(api):
    app, client = api
    run, lead = await prepare_lead(api)
    lead["comments"] = "Теперь нужны 30 пользователей"
    result = await client.post(
        f"/api/v1/qualifications/{run['id']}/apply",
        json={"attributes": ["users"]},
    )
    assert result.status_code == 409
    app.state.bitrix.update_lead.assert_not_awaited()


async def test_changed_target_field_blocks_writes(api):
    app, client = api
    run, lead = await prepare_lead(api)
    lead["UF_CRM_AI_USERS"] = 20
    result = await client.post(
        f"/api/v1/qualifications/{run['id']}/apply",
        json={"attributes": ["users"], "allow_overwrite": True},
    )
    assert result.status_code == 409
    app.state.bitrix.update_lead.assert_not_awaited()


async def test_existing_value_requires_explicit_overwrite(api):
    app, client = api
    run, _ = await prepare_lead(api, existing_users=10)
    url = f"/api/v1/qualifications/{run['id']}/apply"
    assert (await client.post(url, json={"attributes": ["users"]})).status_code == 409
    app.state.bitrix.update_lead.assert_not_awaited()
    result = await client.post(url, json={"attributes": ["users"], "allow_overwrite": True})
    assert result.status_code == 200, result.text
    app.state.bitrix.update_lead.assert_awaited_once()


async def test_unknown_write_outcome_blocks_retries(api):
    app, client = api
    run, _ = await prepare_lead(api)
    app.state.bitrix.update_lead.side_effect = BitrixError("TRANSPORT_ERROR", outcome_unknown=True)
    url = f"/api/v1/qualifications/{run['id']}/apply"
    first = await client.post(url, json={"attributes": ["users"]})
    assert first.status_code == 502
    saved = (await client.get(f"/api/v1/qualifications/{run['id']}")).json()
    assert saved["status"] == "apply_unknown"
    assert (await client.post(url, json={"attributes": ["users"]})).status_code == 409
    assert app.state.bitrix.update_lead.await_count == 1


async def test_unknown_fields_and_missing_suggestions_cannot_be_applied(api):
    app, client = api
    run, _ = await prepare_lead(api)
    url = f"/api/v1/qualifications/{run['id']}/apply"
    for attribute in ["stageId", "budget_max", "deadline_text"]:
        assert (await client.post(url, json={"attributes": [attribute]})).status_code == 422
    app.state.bitrix.update_lead.assert_not_awaited()


async def test_text_only_result_cannot_modify_crm(api):
    _, client = api
    run = (await client.post("/api/v1/qualifications", json={"text": "Нужна CRM"})).json()
    response = await client.post(
        f"/api/v1/qualifications/{run['id']}/apply",
        json={"attributes": ["category"]},
    )
    assert response.status_code == 409


async def test_changed_field_mapping_requires_new_qualification(api):
    app, client = api
    run, _ = await prepare_lead(api)
    app.state.settings.bitrix_field_map = {"users": "UF_CRM_OTHER"}
    response = await client.post(
        f"/api/v1/qualifications/{run['id']}/apply",
        json={"attributes": ["users"]},
    )
    assert response.status_code == 409
    app.state.bitrix.update_lead.assert_not_awaited()


async def test_result_is_bound_to_original_portal(api):
    from pydantic import SecretStr

    app, client = api
    run, _ = await prepare_lead(api)
    app.state.settings.bitrix_webhook_url = SecretStr("https://another.test/rest/1/secret/")
    response = await client.post(
        f"/api/v1/qualifications/{run['id']}/apply",
        json={"attributes": ["users"]},
    )
    assert response.status_code == 409
    app.state.bitrix.update_lead.assert_not_awaited()


@pytest.mark.parametrize(
    "text",
    [
        "Хотим подключить сайт " + "текст " * 1000,
        "Бюджет " + "0" * 3000 + " рублей",
    ],
)
async def test_long_valid_requests_do_not_fail_internal_fact_limits(api, text):
    _, client = api
    response = await client.post("/api/v1/qualifications", json={"text": text})
    assert response.status_code == 200, response.text
