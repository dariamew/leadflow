from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update

from app.auth import COOKIE_NAME, hash_password, token_hash, verify_password
from app.main import create_app
from app.models import User, UserSession
from tests.conftest import TEST_PASSWORD


async def login(client, **overrides):
    return await client.post(
        "/api/v1/auth/login",
        json={"username": "manager", "password": TEST_PASSWORD, **overrides},
    )


async def test_login_me_cookie_and_hashed_session(anonymous_api):
    app, client = anonymous_api
    response = await login(client, username=" MANAGER ")
    assert response.status_code == 200
    data = response.json()
    assert data["user"]["username"] == "manager"
    assert set(data["user"]) == {"id", "username", "role"}
    assert data["user"]["role"] == "manager"
    assert response.headers["cache-control"] == "no-store"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Max-Age=43200" in cookie
    assert "Domain=" not in cookie
    assert (await client.get("/api/v1/auth/me")).json() == data
    assert (await client.get("/api/v1/qualifications")).status_code == 200
    async with app.state.session_factory() as session:
        saved = await session.scalar(select(UserSession))
        assert saved.token_hash == token_hash(client.cookies.get(COOKIE_NAME))
        assert saved.token_hash != client.cookies.get(COOKIE_NAME)
        user = await session.scalar(select(User))
        assert TEST_PASSWORD not in user.password_hash


@pytest.mark.parametrize("username,password", [("manager", "wrong"), ("unknown", TEST_PASSWORD)])
async def test_wrong_credentials_are_indistinguishable(anonymous_api, username, password):
    _, client = anonymous_api
    response = await login(client, username=username, password=password)
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid credentials"}
    assert COOKIE_NAME not in client.cookies


async def test_login_validation_does_not_echo_password(anonymous_api):
    _, client = anonymous_api
    password = "private-password-" * 20
    response = await login(client, password=password)
    assert response.status_code == 422
    assert password not in response.text


async def test_old_api_key_cannot_bypass_login(anonymous_api):
    _, client = anonymous_api
    for path in ["/config", "/qualifications", "/bitrix/fields"]:
        response = await client.get(
            "/api/v1" + path, headers={"X-API-Key": "local-dev-key-change-me"}
        )
        assert response.status_code == 401
    assert (
        await client.post("/api/v1/qualifications", json={"text": "Нужна CRM"})
    ).status_code == 401


async def test_csrf_required_and_cross_origin_denied(anonymous_api):
    _, client = anonymous_api
    authenticated = (await login(client)).json()
    path = "/api/v1/qualifications"
    payload = {"text": "Нужна CRM"}
    for csrf in ["", "wrong"]:
        response = await client.post(path, json=payload, headers={"X-CSRF-Token": csrf})
        assert response.status_code == 403
    headers = {"X-CSRF-Token": authenticated["csrf_token"], "Origin": "https://other.test"}
    assert (await client.post(path, json=payload, headers=headers)).status_code == 403
    headers["Origin"] = "http://test"
    assert (await client.post(path, json=payload, headers=headers)).status_code == 200


async def test_login_csrf_and_untrusted_origin(anonymous_api):
    _, client = anonymous_api
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "manager", "password": TEST_PASSWORD},
        headers={"X-Requested-With": ""},
    )
    assert response.status_code == 403
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "manager", "password": TEST_PASSWORD},
        headers={"Origin": "https://other.test"},
    )
    assert response.status_code == 403


async def test_logout_revokes_copied_cookie_and_relogin_rotates(anonymous_api):
    _, client = anonymous_api
    first = await login(client)
    old_token = client.cookies.get(COOKIE_NAME)
    second = await login(client)
    assert client.cookies.get(COOKIE_NAME) != old_token
    assert first.json()["csrf_token"] != second.json()["csrf_token"]
    current_token = client.cookies.get(COOKIE_NAME)
    response = await client.post(
        "/api/v1/auth/logout", headers={"X-CSRF-Token": second.json()["csrf_token"]}
    )
    assert response.status_code == 204
    for token in [old_token, current_token]:
        client.cookies.clear()
        client.cookies.set(COOKIE_NAME, token)
        assert (await client.get("/api/v1/auth/me")).status_code == 401


@pytest.mark.parametrize("expired", [True, False])
async def test_expired_sessions_and_disabled_users_denied(anonymous_api, expired):
    app, client = anonymous_api
    await login(client)
    async with app.state.session_factory() as session:
        statement = (
            update(UserSession).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            if expired
            else update(User).values(is_active=False)
        )
        await session.execute(statement)
        await session.commit()
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_session_survives_api_restart(anonymous_api, settings):
    _, client = anonymous_api
    await login(client)
    token = client.cookies.get(COOKIE_NAME)
    restarted = create_app(settings)
    async with restarted.router.lifespan_context(restarted):
        async with AsyncClient(
            transport=ASGITransport(app=restarted), base_url="http://test"
        ) as fresh:
            fresh.cookies.set(COOKIE_NAME, token)
            assert (await fresh.get("/api/v1/auth/me")).status_code == 200


async def test_login_throttling_is_persistent(anonymous_api, monkeypatch):
    app, client = anonymous_api
    monkeypatch.setattr("app.auth.verify_password", lambda *_: False)
    for _ in range(10):
        assert (await login(client, password="wrong")).status_code == 401
    response = await login(client, password="wrong")
    assert response.status_code == 429
    assert response.headers["retry-after"] == "600"
    # A new app instance uses the same database counters.
    restarted = create_app(app.state.settings)
    async with restarted.router.lifespan_context(restarted):
        async with AsyncClient(
            transport=ASGITransport(app=restarted),
            base_url="http://test",
            headers={"X-Requested-With": "leadflow"},
        ) as fresh:
            assert (await login(fresh, password="wrong")).status_code == 429


async def test_secure_cookie_in_production(anonymous_api):
    app, client = anonymous_api
    app.state.settings.auth_cookie_secure = True
    response = await login(client)
    assert "; Secure" in response.headers["set-cookie"]


def test_password_salts_and_verification():
    first, second = hash_password(TEST_PASSWORD), hash_password(TEST_PASSWORD)
    assert first != second
    assert verify_password(TEST_PASSWORD, first)
    assert not verify_password("wrong", first)
    assert not verify_password(TEST_PASSWORD, "broken")
    with pytest.raises(ValueError):
        hash_password("short")
