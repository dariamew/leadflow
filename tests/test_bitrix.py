import json

import httpx
import pytest

from app.integrations.bitrix import BitrixClient, BitrixError


async def test_get_uses_universal_api_and_original_custom_field_names():
    def handler(request):
        assert request.url.path.endswith("/crm.item.get.json")
        assert json.loads(request.content) == {
            "entityTypeId": 1,
            "id": 42,
            "useOriginalUfNames": "Y",
        }
        return httpx.Response(200, json={"result": {"item": {"id": 42}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await BitrixClient(http, "https://portal.test/rest/1/secret/").get_lead(42) == {
            "id": 42
        }


async def test_fields_catalogue_uses_original_custom_field_names():
    def handler(request):
        assert request.url.path.endswith("/crm.item.fields.json")
        assert json.loads(request.content) == {"entityTypeId": 1, "useOriginalUfNames": "Y"}
        return httpx.Response(200, json={"result": {"fields": {"UF_CRM_TEXT": {"type": "string"}}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await BitrixClient(http, "https://portal.test/rest/1/secret/").get_lead_fields()
        assert "UF_CRM_TEXT" in result["fields"]


async def test_read_retries_limit_error(monkeypatch):
    calls = []

    async def no_sleep(_):
        pass

    monkeypatch.setattr("app.integrations.bitrix.asyncio.sleep", no_sleep)

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, json={"error": "QUERY_LIMIT_EXCEEDED"})
        return httpx.Response(200, json={"result": {"item": {"id": 42}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await BitrixClient(http, "https://portal.test/rest/1/secret").get_lead(42)
    assert len(calls) == 2


async def test_write_timeout_is_never_retried_and_outcome_is_unknown():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("secret URL must not escape", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(BitrixError) as error:
            await BitrixClient(http, "https://portal.test/rest/1/secret").update_lead(42, {})
    assert len(calls) == 1
    assert error.value.outcome_unknown
    assert "secret" not in str(error.value)


async def test_api_error_is_sanitized():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "error": "ACCESS_DENIED",
                "error_description": "sensitive customer data",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(BitrixError, match="ACCESS_DENIED") as error:
            await BitrixClient(http, "https://portal.test/rest/1/secret").get_lead(42)
    assert "sensitive" not in str(error.value)


async def test_unconfigured_portal_does_not_send_requests():
    async with httpx.AsyncClient() as http:
        with pytest.raises(BitrixError, match="NOT_CONFIGURED"):
            await BitrixClient(http, "").get_lead(42)


@pytest.mark.parametrize("newest", [True, False])
async def test_list_ids_uses_stable_cursor_and_validates_order(newest):
    def handler(request):
        assert request.url.path.endswith("/crm.item.list.json")
        assert json.loads(request.content) == {
            "entityTypeId": 1,
            "select": ["id"],
            "filter": {">id": 10},
            "order": {"id": "DESC" if newest else "ASC"},
        }
        return httpx.Response(
            200, json={"result": {"items": [{"id": i} for i in ([12, 11] if newest else [11, 12])]}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        ids = await BitrixClient(http, "https://portal.test/rest/1/secret/").list_lead_ids(
            after=10, newest_first=newest
        )
        assert ids == ([12, 11] if newest else [11, 12])


@pytest.mark.parametrize(
    "items",
    [
        [{"id": True}],
        [{"id": 0}],
        [{"id": "bad"}],
        [{"id": 12}, {"id": 11}],
        [{"id": 11}, {"id": 11}],
        [None],
    ],
)
async def test_invalid_listing_never_advances_cursor(items):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"result": {"items": items}})
        )
    ) as http:
        with pytest.raises(BitrixError, match="INVALID_RESPONSE"):
            await BitrixClient(http, "https://portal.test/rest/1/secret/").list_lead_ids(after=10)
