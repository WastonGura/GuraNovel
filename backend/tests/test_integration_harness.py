import pytest

from app.db.testing import TEST_DATABASE_URL_ENV, get_test_database_url


def test_test_database_url_must_be_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TEST_DATABASE_URL_ENV, raising=False)

    with pytest.raises(RuntimeError, match=TEST_DATABASE_URL_ENV):
        get_test_database_url()


def test_test_database_url_rejects_a_non_test_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        TEST_DATABASE_URL_ENV,
        "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/guranovel",
    )

    with pytest.raises(ValueError, match="_test"):
        get_test_database_url()


def test_test_database_url_accepts_a_test_database(monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/guranovel_test"
    monkeypatch.setenv(TEST_DATABASE_URL_ENV, database_url)

    assert get_test_database_url() == database_url
