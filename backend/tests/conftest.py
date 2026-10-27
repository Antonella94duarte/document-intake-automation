import os

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api.deps import storage_dep
from app.db.session import get_db
from app.main import app
from app.storage import LocalStorage

# A separate database, so tests never touch the development data.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://intake:intake@localhost:5435/document_intake_test",
)

# The smallest valid-looking PDF header; enough for the upload checks.
MINIMAL_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def storage(tmp_path) -> LocalStorage:
    return LocalStorage(tmp_path / "storage")


def _create_database_if_missing(url: str) -> None:
    target = make_url(url)
    admin = create_engine(
        target.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        # Fail fast when nothing listens: without it a dropped connection can hang for minutes.
        connect_args={"connect_timeout": 5},
    )
    with admin.connect() as conn:
        exists = conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": target.database}
        )
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    admin.dispose()


@pytest.fixture(scope="session")
def _database_unreachable() -> str | None:
    """Try to reach PostgreSQL once per run, not once per test that needs it."""
    try:
        _create_database_if_missing(TEST_DATABASE_URL)
    except OperationalError as exc:
        return f"PostgreSQL not reachable at {TEST_DATABASE_URL}: {exc.__class__.__name__}"
    return None


@pytest.fixture(scope="session")
def db_engine(_database_unreachable):
    """Test database migrated with Alembic, so the migrations themselves are exercised."""
    if _database_unreachable:
        # Locally, skipping lets the rest of the suite run without Docker. In CI (GitHub Actions
        # sets CI=true) a skip would hide a broken setup behind a green build, so fail instead.
        if os.environ.get("CI"):
            pytest.fail(_database_unreachable)
        pytest.skip(_database_unreachable)

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.upgrade(config, "head")

    engine = create_engine(TEST_DATABASE_URL)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine):
    """Session inside a transaction that is rolled back after the test.

    Commits made by the code under test only release a savepoint, so nothing persists.
    """
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def session_factory(db_session):
    """Stands in for SessionLocal in code that opens its own sessions (the worker): every
    "new" session is the test's rolled-back session, and leaving the `with` block keeps it open.
    """

    class _Factory:
        def __call__(self):
            return self

        def __enter__(self):
            return db_session

        def __exit__(self, *exc):
            return False

    return _Factory()


@pytest.fixture
def db_client(client, db_session, storage):
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[storage_dep] = lambda: storage
    return client
