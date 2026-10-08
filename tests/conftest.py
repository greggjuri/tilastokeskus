"""Shared fixtures.

``db`` gives a test a connection to the real database with every migration applied inside a
throwaway schema, in a transaction that is rolled back afterwards. No real table is read or
written, and nothing survives the test.

It fails rather than skips when Postgres is unreachable. A skipped idempotency test is silence:
the run reports green and nothing was checked.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest

from tilastokeskus import migrate
from tilastokeskus.config import load_settings

FIXTURES = Path(__file__).parent / "fixtures"


@contextmanager
def throwaway_schema() -> Iterator[psycopg.Connection]:
    settings = load_settings()
    try:
        conn = psycopg.connect(settings.conninfo)
    except psycopg.OperationalError as exc:
        pytest.fail(
            f"database-backed test needs Postgres at {settings.safe_conninfo} and could not "
            f"connect. These tests fail rather than skip; start the database and re-run. ({exc})"
        )

    schema = f"test_{uuid.uuid4().hex[:12]}"
    try:
        conn.execute(f"CREATE SCHEMA {schema}")
        conn.execute(f"SET LOCAL search_path TO {schema}")
        for migration in migrate.discover():
            conn.execute(migration.sql)
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def db() -> Iterator[psycopg.Connection]:
    with throwaway_schema() as conn:
        yield conn


def load_fixture(name: str) -> object:
    """A redacted payload generated from the raw archive by scripts/redact_payload.py."""
    return json.loads((FIXTURES / f"{name}.json").read_text())
