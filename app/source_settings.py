import hashlib
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.administration import Administrator, lock_administration
from app.auth import Session
from app.config import Settings
from app.integrations.bitrix import BitrixError
from app.models import BitrixSource

router = APIRouter(prefix="/api/v1/bitrix/source", tags=["bitrix source"])


def portal_key(settings: Settings) -> str | None:
    host = urlsplit(settings.bitrix_webhook_url.get_secret_value()).netloc.casefold()
    return hashlib.sha256(host.encode()).hexdigest() if host else None


async def effective_settings(session: AsyncSession, settings: Settings) -> Settings:
    source = await session.get(BitrixSource, 1, populate_existing=True)
    if source and source.text_field and source.portal_key == portal_key(settings):
        return settings.model_copy(update={"bitrix_text_field": source.text_field})
    return settings


async def read_source(session: AsyncSession, settings: Settings):
    source = await session.get(BitrixSource, 1, populate_existing=True)
    if source is None:
        raise HTTPException(503, "Bitrix source settings are not initialized")
    key = portal_key(settings)
    saved = bool(source.text_field and key and source.portal_key == key)
    return {
        "text_field": source.text_field if saved else settings.bitrix_text_field,
        "saved": saved,
        "portal_changed": bool(source.text_field and source.portal_key != key),
        "bitrix_configured": key is not None,
    }


async def text_fields(client):
    result = await client.get_lead_fields()
    fields = result.get("fields") if isinstance(result, dict) else None
    if not isinstance(fields, dict):
        raise BitrixError("INVALID_RESPONSE")
    choices = []
    for code, metadata in fields.items():
        if not isinstance(metadata, dict):
            continue
        if metadata.get("type") not in {"string", "text"}:
            continue
        if metadata.get("isMultiple") in (True, "Y", "true", 1):
            continue
        title = metadata.get("title")
        choices.append({"code": code, "title": title if isinstance(title, str) and title else code})
    return sorted(
        choices,
        key=lambda field: (field["code"] != "comments", field["title"].casefold(), field["code"]),
    )


class SourceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text_field: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$")


async def validate_text_field(client, code: str):
    fields = await text_fields(client)
    if code not in {field["code"] for field in fields}:
        raise HTTPException(422, "Choose a single text field from Bitrix24")


@router.get("")
async def source_config(request: Request, session: Session, actor: Administrator):
    return await read_source(session, request.app.state.settings)


@router.get("/fields")
async def source_fields(request: Request, actor: Administrator):
    return await text_fields(request.app.state.bitrix)


@router.put("")
async def set_source(
    payload: SourceUpdate, request: Request, session: Session, actor: Administrator
):
    settings = request.app.state.settings
    if not portal_key(settings):
        raise HTTPException(409, "Bitrix webhook is not configured")
    # Check the actual portal catalogue rather than accepting arbitrary keys or
    # falling back silently to comments when a selected field is unavailable.
    await validate_text_field(request.app.state.bitrix, payload.text_field)
    await lock_administration(session, actor)
    source = await session.get(BitrixSource, 1)
    if source is None:
        raise HTTPException(503, "Bitrix source settings are not initialized")
    source.text_field = payload.text_field
    source.portal_key = portal_key(settings)
    await session.commit()
    return await read_source(session, settings)
