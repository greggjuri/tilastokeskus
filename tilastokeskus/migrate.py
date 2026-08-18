"""Schema migrations.

Migrations are ``.sql`` files in ``tilastokeskus/migrations/``, applied in filename order.
Each is applied once and recorded in ``schema_migrations``. A migration runs inside a
transaction, so a failing one leaves no partial schema behind.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import psycopg

from .config import Settings
from .db import connect

TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


@dataclass(frozen=True)
class Migration:
    version: str
    sql: str


def discover() -> list[Migration]:
    """All migrations, ordered by filename."""
    files = resources.files("tilastokeskus") / "migrations"
    found = sorted(
        (p for p in files.iterdir() if p.name.endswith(".sql")),
        key=lambda p: p.name,
    )
    return [Migration(version=Path(p.name).stem, sql=p.read_text()) for p in found]


def applied_versions(conn: psycopg.Connection) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(TRACKING_TABLE)
        cur.execute("SELECT version FROM schema_migrations")
        return {row[0] for row in cur.fetchall()}


def run(settings: Settings, dry_run: bool = False) -> list[str]:
    """Apply any unapplied migrations. Returns the versions applied."""
    with connect(settings) as conn:
        already = applied_versions(conn)
        pending = [m for m in discover() if m.version not in already]

        if dry_run:
            return [m.version for m in pending]

        for migration in pending:
            with conn.cursor() as cur:
                cur.execute(migration.sql)
                cur.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)",
                    (migration.version,),
                )
        return [m.version for m in pending]
