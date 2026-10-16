import asyncio
import re
from typing import Any

import httpx


class BitrixError(Exception):
    def __init__(self, code: str, *, outcome_unknown: bool = False):
        self.code = code if re.fullmatch(r"[A-Za-z0-9_]{1,80}", code) else "BITRIX_ERROR"
        self.outcome_unknown = outcome_unknown
        super().__init__(self.code)


class BitrixClient:
    """Single configured portal. Never log URLs: they contain the webhook secret."""

    def __init__(self, client: httpx.AsyncClient, webhook_url: str, timeout: float = 15):
        self.client = client
        self.base_url = webhook_url.rstrip("/") + "/" if webhook_url else ""
        self.timeout = timeout

    async def _call(self, method: str, payload: dict, *, read_only: bool) -> dict:
        if not self.base_url:
            raise BitrixError("NOT_CONFIGURED")
        attempts = 3 if read_only else 1
        for attempt in range(attempts):
            try:
                response = await self.client.post(
                    self.base_url + method + ".json", json=payload, timeout=self.timeout
                )
            except httpx.TransportError:
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.25 * 2**attempt)
                    continue
                raise BitrixError("TRANSPORT_ERROR", outcome_unknown=not read_only) from None

            try:
                data = response.json()
            except ValueError:
                data = {}
            if not isinstance(data, dict):
                data = {}
            code = str(data.get("error", ""))
            retryable = response.status_code in {429, 500, 502, 503, 504} or code in {
                "QUERY_LIMIT_EXCEEDED",
                "INTERNAL_SERVER_ERROR",
            }
            if retryable and attempt + 1 < attempts:
                await asyncio.sleep(0.25 * 2**attempt)
                continue
            if response.is_error or code:
                # Writes are not retried: a lost response can hide a successful update.
                raise BitrixError(
                    code or f"HTTP_{response.status_code}",
                    outcome_unknown=not read_only and response.status_code >= 500,
                )
            if "result" not in data:
                raise BitrixError("INVALID_RESPONSE", outcome_unknown=not read_only)
            return data
        raise AssertionError("Unreachable")

    async def get_lead(self, lead_id: int) -> dict[str, Any]:
        data = await self._call(
            "crm.item.get",
            {"entityTypeId": 1, "id": lead_id, "useOriginalUfNames": "Y"},
            read_only=True,
        )
        result = data["result"]
        item = result.get("item") if isinstance(result, dict) else None
        if not isinstance(item, dict):
            raise BitrixError("INVALID_RESPONSE")
        return item

    async def get_lead_fields(self) -> dict:
        data = await self._call(
            "crm.item.fields", {"entityTypeId": 1, "useOriginalUfNames": "Y"}, read_only=True
        )
        return data["result"]

    async def list_lead_ids(self, *, after: int = 0, newest_first: bool = False) -> list[int]:
        # Only standard IDs are selected. Some portals return empty rows when
        # useOriginalUfNames=Y is combined with select=["id"]. It is only needed
        # for custom fields and deliberately omitted from this listing.
        data = await self._call(
            "crm.item.list",
            {
                "entityTypeId": 1,
                "select": ["id"],
                "filter": {">id": after},
                "order": {"id": "DESC" if newest_first else "ASC"},
            },
            read_only=True,
        )
        result = data["result"]
        items = result.get("items") if isinstance(result, dict) else None
        if not isinstance(items, list) or len(items) > 50:
            raise BitrixError("INVALID_RESPONSE")
        ids = []
        for item in items:
            value = item.get("id") if isinstance(item, dict) else None
            if isinstance(value, bool) or not str(value).isascii() or not str(value).isdigit():
                raise BitrixError("INVALID_RESPONSE")
            identity = int(value)
            if identity <= after:
                raise BitrixError("INVALID_RESPONSE")
            ids.append(identity)
        if ids != sorted(set(ids), reverse=newest_first):
            raise BitrixError("INVALID_RESPONSE")
        return ids

    async def update_lead(self, lead_id: int, fields: dict) -> dict:
        data = await self._call(
            "crm.item.update",
            {"entityTypeId": 1, "id": lead_id, "fields": fields, "useOriginalUfNames": "Y"},
            read_only=False,
        )
        return data["result"]
