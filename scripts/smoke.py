"""Exercise migrated database + API without an external portal or model."""

import asyncio
import json
import secrets
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import delete

from app.auth import hash_password
from app.config import Settings
from app.main import create_app
from app.models import PasswordInvitation, QualificationRun, User, UserSession


async def main():
    settings = Settings(
        extractor="rules",
        auth_cookie_secure=False,
        auth_public_origin="http://smoke",
        monitor_worker_enabled=False,
    )
    app = create_app(settings)
    payload = json.loads(
        (Path(__file__).resolve().parents[1] / "tests/data/request.json").read_text(
            encoding="utf-8"
        )
    )
    payload["text"] += f"\nSmoke test: {uuid4()}"
    run_id = None
    user_id = str(uuid4())
    user_ids = [user_id]
    username = "smoke-" + secrets.token_hex(8)
    password = secrets.token_urlsafe(24)
    async with app.router.lifespan_context(app):
        try:
            async with app.state.session_factory() as session:
                session.add(
                    User(
                        id=user_id,
                        username=username,
                        password_hash=hash_password(password),
                        role="admin",
                    )
                )
                await session.commit()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://smoke",
                headers={"X-Requested-With": "leadflow"},
            ) as client:
                ready = await client.get("/health/ready")
                ready.raise_for_status()
                login = await client.post(
                    "/api/v1/auth/login", json={"username": username, "password": password}
                )
                login.raise_for_status()
                client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
                monitoring = await client.get("/api/v1/bitrix/monitor")
                monitoring.raise_for_status()
                assert monitoring.json()["worker_available"] is False
                response = await client.post("/api/v1/qualifications", json=payload)
                response.raise_for_status()
                run = response.json()
                run_id = run["id"]
                assert run["created_by_user_id"] == user_id
                assert run["result"]["facts"]["users"]["value"] == 15
                saved = await client.get(f"/api/v1/qualifications/{run_id}")
                saved.raise_for_status()
                assert saved.json() == run
                created = await client.post(
                    "/api/v1/users", json={"username": username + "-invited", "role": "manager"}
                )
                created.raise_for_status()
                invited = created.json()
                user_ids.append(invited["user"]["id"])
                token = invited["invitation_url"].split("#set-password=")[1]
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://smoke",
                    headers={"X-Requested-With": "leadflow"},
                ) as peer:
                    setup = await peer.post(
                        "/api/v1/auth/invitations/accept",
                        json={"token": token, "password": password},
                    )
                    setup.raise_for_status()
                    assert (
                        await peer.post(
                            "/api/v1/auth/invitations/accept",
                            json={"token": token, "password": password},
                        )
                    ).status_code == 400
                    signed = await peer.post(
                        "/api/v1/auth/login",
                        json={"username": username + "-invited", "password": password},
                    )
                    signed.raise_for_status()
                    assert (await peer.get("/api/v1/users")).status_code == 403
                    assert (await peer.get("/api/v1/bitrix/monitor")).status_code == 403
                    assert (await peer.get(f"/api/v1/qualifications/{run_id}")).status_code == 404
                    assert (await peer.get("/api/v1/qualifications")).json() == []
                    disabled = await client.patch(
                        f"/api/v1/users/{user_ids[-1]}", json={"is_active": False}
                    )
                    disabled.raise_for_status()
                    assert (await peer.get("/api/v1/auth/me")).status_code == 401
                logout = await client.post("/api/v1/auth/logout")
                logout.raise_for_status()
                assert (await client.get("/api/v1/qualifications")).status_code == 401
                print("Smoke passed: login, qualification, invitation, roles, disable and logout.")
        finally:
            async with app.state.session_factory() as session:
                if run_id:
                    await session.execute(
                        delete(QualificationRun).where(QualificationRun.id == run_id)
                    )
                await session.execute(
                    delete(PasswordInvitation).where(PasswordInvitation.user_id.in_(user_ids))
                )
                await session.execute(delete(UserSession).where(UserSession.user_id.in_(user_ids)))
                await session.execute(delete(User).where(User.id.in_(user_ids)))
                await session.commit()


if __name__ == "__main__":
    asyncio.run(main())
