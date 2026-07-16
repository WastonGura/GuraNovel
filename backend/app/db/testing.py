"""Safety helpers shared by PostgreSQL integration tests."""

import os

from sqlalchemy.engine import make_url

TEST_DATABASE_URL_ENV = "TEST_DATABASE_URL"


def get_test_database_url() -> str:
    """Return the explicit integration database URL after validating its name."""
    database_url = os.environ.get(TEST_DATABASE_URL_ENV)
    if not database_url:
        raise RuntimeError(f"{TEST_DATABASE_URL_ENV} must be set for integration tests")

    database_name = make_url(database_url).database
    if not database_name or not database_name.endswith("_test"):
        raise ValueError("Integration database name must end with '_test'")

    return database_url
