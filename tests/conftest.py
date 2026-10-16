import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth import hash_password
from app.config import Settings
from app.db import Base
from app.main import create_app
from app.models import BitrixSource, LeadMonitor, User, UserAdminLock

TEST_PASSWORD = "test-password-for-local-tests"


@pytest.fixture(scope="session")
def password_hash():
    return hash_password(TEST_PASSWORD)


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}",
        auth_cookie_secure=False,
        monitor_worker_enabled=False,
        extractor="rules",
        bitrix_webhook_url="",
        bitrix_field_map={"users": "UF_CRM_AI_USERS", "category": "UF_CRM_AI_CATEGORY"},
    )


@pytest_asyncio.fixture
async def anonymous_api(settings, password_hash):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with app.state.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with app.state.session_factory() as session:
            session.add(User(username="manager", password_hash=password_hash))
            session.add(UserAdminLock(id=1))
            session.add(LeadMonitor(id=1))
            session.add(BitrixSource(id=1))
            await session.commit()
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-Requested-With": "leadflow"},
        ) as client:
            yield app, client


@pytest_asyncio.fixture
async def api(anonymous_api):
    app, client = anonymous_api
    response = await client.post(
        "/api/v1/auth/login", json={"username": "manager", "password": TEST_PASSWORD}
    )
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    yield app, client
