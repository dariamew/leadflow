import pytest
from sqlalchemy import select

from app.auth import hash_password, normalize_username, verify_password
from app.models import User, UserSession
from app.users import save_user


async def test_password_reset_revokes_sessions(api, monkeypatch):
    app, client = api
    monkeypatch.setattr("app.users.Settings", lambda: app.state.settings)
    password = "a-new-password-for-manager"
    await save_user("MANAGER", password, reset=True)
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    async with app.state.session_factory() as session:
        assert await session.scalar(select(UserSession)) is None
        user = await session.scalar(select(User).where(User.username == "manager"))
        assert verify_password(password, user.password_hash)


async def test_create_and_duplicate_account(anonymous_api, monkeypatch):
    app, _ = anonymous_api
    monkeypatch.setattr("app.users.Settings", lambda: app.state.settings)
    await save_user("second.user", "a-password-for-second-user")
    with pytest.raises(ValueError, match="Логин уже занят"):
        await save_user("SECOND.USER", "a-password-for-second-user")


@pytest.mark.parametrize("username", ["ab", "a b c", "русский", "a" * 65, "-admin"])
def test_invalid_usernames(username):
    with pytest.raises(ValueError):
        normalize_username(username)


def test_oversized_password_rejected():
    with pytest.raises(ValueError):
        hash_password("a" * 129)
