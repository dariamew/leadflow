import pytest
from pydantic import ValidationError

from app.config import Settings


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")


def test_database_connection_must_be_configured(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    "mapping",
    [
        {"users": "stageId"},
        {"arbitrary": "UF_CRM_TEST"},
        {"users": "UF_CRM_SAME", "category": "UF_CRM_SAME"},
    ],
)
def test_unsafe_mapping_rejected(mapping):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, bitrix_field_map=mapping)


def test_llm_needs_explicit_credentials_and_model():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, extractor="llm", llm_model="", llm_api_key="")


def test_production_cookie_default_and_origin_validation():
    assert Settings(_env_file=None).auth_cookie_secure
    assert (
        Settings(_env_file=None, auth_public_origin="https://service.test/").auth_public_origin
        == "https://service.test"
    )
    for origin in [
        "https://service.test/path",
        "https://user:password@service.test",
        "null",
        "https://service.test?q=1",
    ]:
        with pytest.raises(ValidationError):
            Settings(_env_file=None, auth_public_origin=origin)
