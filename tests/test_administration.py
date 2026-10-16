import asyncio
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update

from app.auth import token_hash
from app.models import LoginLimit, PasswordInvitation, User
from tests.conftest import TEST_PASSWORD


@pytest_asyncio.fixture
async def admin_api(api):
    app, client = api
    app.state.settings.auth_public_origin = "http://test"
    async with app.state.session_factory() as session:
        await session.execute(update(User).where(User.username == "manager").values(role="admin"))
        await session.commit()
    return app, client


async def create(client, username="new.manager", role="manager"):
    response = await client.post("/api/v1/users", json={"username": username, "role": role})
    assert response.status_code == 201, response.text
    result = response.json()
    return result, result["invitation_url"].split("#set-password=")[1]


async def activate(client, token):
    return await client.post(
        "/api/v1/auth/invitations/accept", json={"token": token, "password": TEST_PASSWORD}
    )


async def test_manager_cannot_use_administration(api):
    _, client = api
    requests = [
        ("GET", "/api/v1/users", None),
        ("POST", "/api/v1/users", {"username": "other", "role": "admin"}),
        ("PATCH", "/api/v1/users/any", {"role": "admin"}),
        ("POST", "/api/v1/users/any/invitation", None),
        ("POST", "/api/v1/users/any/password-reset", None),
    ]
    for method, path, body in requests:
        assert (await client.request(method, path, json=body)).status_code == 403


async def test_create_single_use_link_and_login(admin_api):
    app, client = admin_api
    result, token = await create(client, username="NEW.MANAGER")
    assert result["user"]["username"] == "new.manager"
    assert result["user"]["role"] == "manager"
    assert not result["user"]["password_set"]
    assert result["invitation_url"].startswith("http://test/#set-password=")
    async with app.state.session_factory() as session:
        invite = await session.scalar(select(PasswordInvitation))
        assert invite.token_hash == token_hash(token) and invite.token_hash != token
    listing = (await client.get("/api/v1/users")).json()
    assert listing["total"] == 2
    assert token not in str(listing) and "password_hash" not in str(listing)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Requested-With": "leadflow"},
    ) as guest:
        assert (
            await guest.post(
                "/api/v1/auth/login", json={"username": "new.manager", "password": TEST_PASSWORD}
            )
        ).status_code == 401
        # Previewing a link never consumes it.
        for _ in range(2):
            preview = await guest.post("/api/v1/auth/invitations/check", json={"token": token})
            assert preview.status_code == 200 and preview.json()["username"] == "new.manager"
        assert (await activate(guest, token)).status_code == 204
        assert (await activate(guest, token)).status_code == 400
        assert (
            await guest.post("/api/v1/auth/invitations/check", json={"token": token})
        ).status_code == 400
        login = await guest.post(
            "/api/v1/auth/login", json={"username": "new.manager", "password": TEST_PASSWORD}
        )
        assert login.status_code == 200 and login.json()["user"]["role"] == "manager"
        assert (await guest.get("/api/v1/users")).status_code == 403
    assert (await client.get("/api/v1/users")).json()["items"][1]["password_set"]


async def test_admin_invitation_role_is_assigned_by_admin(admin_api):
    app, client = admin_api
    created, token = await create(client, role="admin")
    assert created["user"]["role"] == "admin"
    escalation = await client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": token, "password": TEST_PASSWORD, "role": "admin"},
    )
    assert escalation.status_code == 422
    assert (await activate(client, token)).status_code == 204
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Requested-With": "leadflow"},
    ) as other:
        signed = await other.post(
            "/api/v1/auth/login", json={"username": "new.manager", "password": TEST_PASSWORD}
        )
        assert signed.json()["user"]["role"] == "admin"
        assert (await other.get("/api/v1/users")).status_code == 200


@pytest.mark.parametrize("role", ["root", "owner", "", None])
async def test_invalid_role_rejected(admin_api, role):
    _, client = admin_api
    assert (
        await client.post("/api/v1/users", json={"username": "someone", "role": role})
    ).status_code == 422


async def test_duplicate_and_invalid_login(admin_api):
    _, client = admin_api
    await create(client)
    assert (await client.post("/api/v1/users", json={"username": "NEW.MANAGER"})).status_code == 409
    assert (await client.post("/api/v1/users", json={"username": "bad name"})).status_code == 422


async def test_reissue_invalidates_previous_and_expired_links(admin_api):
    app, client = admin_api
    result, old = await create(client)
    user_id = result["user"]["id"]
    async with app.state.session_factory() as session:
        await session.execute(
            update(PasswordInvitation).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()
    assert (await activate(client, old)).status_code == 400
    renewed = await client.post(f"/api/v1/users/{user_id}/invitation")
    assert renewed.status_code == 200
    token = renewed.json()["invitation_url"].split("#set-password=")[1]
    assert token != old
    assert (await activate(client, old)).status_code == 400
    assert (await activate(client, token)).status_code == 204
    assert (await client.post(f"/api/v1/users/{user_id}/invitation")).status_code == 409


async def test_disabling_pending_user_revokes_link_and_reenable_requires_new_link(admin_api):
    _, client = admin_api
    created, token = await create(client)
    path = f"/api/v1/users/{created['user']['id']}"
    assert (await client.patch(path, json={"is_active": False})).status_code == 200
    assert (await activate(client, token)).status_code == 400
    assert (await client.post(path + "/invitation")).status_code == 409
    assert (await client.patch(path, json={"is_active": True})).status_code == 200
    assert (await activate(client, token)).status_code == 400
    assert (await client.post(path + "/invitation")).status_code == 200


async def test_disable_and_role_changes_revoke_sessions(admin_api):
    app, client = admin_api
    created, token = await create(client)
    assert (await activate(client, token)).status_code == 204
    path = f"/api/v1/users/{created['user']['id']}"
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Requested-With": "leadflow"},
    ) as other:
        for changes, expected_role in [
            ({"role": "admin"}, "manager"),
            ({"is_active": False}, "admin"),
        ]:
            signed = await other.post(
                "/api/v1/auth/login", json={"username": "new.manager", "password": TEST_PASSWORD}
            )
            assert signed.json()["user"]["role"] == expected_role
            assert (await other.get("/api/v1/config")).status_code == 200
            assert (await client.patch(path, json=changes)).status_code == 200
            assert (await other.get("/api/v1/auth/me")).status_code == 401
        signed = await other.post(
            "/api/v1/auth/login", json={"username": "new.manager", "password": TEST_PASSWORD}
        )
        assert signed.status_code == 401


async def test_cannot_disable_or_demote_self(admin_api):
    _, client = admin_api
    user_id = (await client.get("/api/v1/auth/me")).json()["user"]["id"]
    for changes in [{"is_active": False}, {"role": "manager"}]:
        assert (await client.patch(f"/api/v1/users/{user_id}", json=changes)).status_code == 409
    assert (await client.get("/api/v1/users")).status_code == 200


async def test_csrf_and_public_request_protection(admin_api):
    _, client = admin_api
    response = await client.post(
        "/api/v1/users", json={"username": "someone"}, headers={"X-CSRF-Token": ""}
    )
    assert response.status_code == 403
    created, token = await create(client)
    response = await client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": token, "password": TEST_PASSWORD},
        headers={"Origin": "https://foreign.test"},
    )
    assert response.status_code == 403
    response = await client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": token, "password": TEST_PASSWORD},
        headers={"X-Requested-With": ""},
    )
    assert response.status_code == 403
    private = "secret-password-" * 20
    response = await client.post(
        "/api/v1/auth/invitations/accept", json={"token": token, "password": private}
    )
    assert (
        response.status_code == 422 and private not in response.text and token not in response.text
    )


async def test_no_untrusted_host_in_invitation_url(admin_api):
    app, client = admin_api
    app.state.settings.auth_public_origin = ""
    response = await client.post(
        "/api/v1/users", json={"username": "someone"}, headers={"Host": "evil.test"}
    )
    assert response.status_code == 503
    assert (await client.get("/api/v1/users")).json()["total"] == 1


async def test_revoked_admin_cannot_demote_remaining_admin(admin_api):
    app, client = admin_api
    created, token = await create(client, role="admin")
    assert (await activate(client, token)).status_code == 204
    original_id = (await client.get("/api/v1/auth/me")).json()["user"]["id"]
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Requested-With": "leadflow"},
    ) as other:
        signed = await other.post(
            "/api/v1/auth/login", json={"username": "new.manager", "password": TEST_PASSWORD}
        )
        other.headers["X-CSRF-Token"] = signed.json()["csrf_token"]
        assert (
            await client.patch(f"/api/v1/users/{created['user']['id']}", json={"role": "manager"})
        ).status_code == 200
        assert (
            await other.patch(f"/api/v1/users/{original_id}", json={"role": "manager"})
        ).status_code == 401
    assert (await client.get("/api/v1/auth/me")).json()["user"]["role"] == "admin"


async def test_concurrent_invitation_redemption_happens_once(admin_api):
    app, client = admin_api
    _, token = await create(client)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Requested-With": "leadflow"},
    ) as other:
        results = await asyncio.gather(activate(client, token), activate(other, token))
    statuses = [result.status_code for result in results]
    assert statuses.count(204) == 1
    assert all(status in {204, 400, 503} for status in statuses)


async def test_recovery_changes_password_once_and_revokes_sessions(admin_api):
    app, client = admin_api
    created, setup = await create(client)
    assert (await activate(client, setup)).status_code == 204
    path = f"/api/v1/users/{created['user']['id']}/password-reset"
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Requested-With": "leadflow"},
    ) as peer:
        signed = await peer.post(
            "/api/v1/auth/login", json={"username": "new.manager", "password": TEST_PASSWORD}
        )
        assert signed.status_code == 200
        reset = await client.post(path)
        assert reset.status_code == 200 and reset.json()["user"]["password_set"]
        token = reset.json()["invitation_url"].split("#set-password=")[1]
        assert (await peer.get("/api/v1/auth/me")).status_code == 200
        # Old credentials continue working until a replacement is actually set.
        assert (
            await peer.post(
                "/api/v1/auth/login", json={"username": "new.manager", "password": TEST_PASSWORD}
            )
        ).status_code == 200
        async with app.state.session_factory() as session:
            await session.execute(
                update(LoginLimit)
                .where(LoginLimit.key == token_hash("user:new.manager"))
                .values(attempts=10)
            )
            await session.commit()
        password = "the-replacement-password"
        response = await client.post(
            "/api/v1/auth/invitations/accept", json={"token": token, "password": password}
        )
        assert response.status_code == 204
        assert (await peer.get("/api/v1/auth/me")).status_code == 401
        assert (await activate(client, token)).status_code == 400
        assert (
            await peer.post(
                "/api/v1/auth/login", json={"username": "new.manager", "password": TEST_PASSWORD}
            )
        ).status_code == 401
        assert (
            await peer.post(
                "/api/v1/auth/login", json={"username": "new.manager", "password": password}
            )
        ).status_code == 200


async def test_recovery_reissue_and_disabling_revoke_links(admin_api):
    _, client = admin_api
    created, setup = await create(client)
    assert (await activate(client, setup)).status_code == 204
    path = f"/api/v1/users/{created['user']['id']}"
    first = (
        (await client.post(path + "/password-reset"))
        .json()["invitation_url"]
        .split("#set-password=")[1]
    )
    second = (
        (await client.post(path + "/password-reset"))
        .json()["invitation_url"]
        .split("#set-password=")[1]
    )
    assert (await activate(client, first)).status_code == 400
    assert (await client.patch(path, json={"is_active": False})).status_code == 200
    assert (await activate(client, second)).status_code == 400
    assert (await client.post(path + "/password-reset")).status_code == 409


async def test_recovery_rejects_self_and_pending_users(admin_api):
    _, client = admin_api
    self_id = (await client.get("/api/v1/auth/me")).json()["user"]["id"]
    assert (await client.post(f"/api/v1/users/{self_id}/password-reset")).status_code == 409
    created, _ = await create(client)
    assert (
        await client.post(f"/api/v1/users/{created['user']['id']}/password-reset")
    ).status_code == 409
