"""Database connection helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

from .config import Settings


class DatabaseUnavailable(RuntimeError):
    """Raised when the database cannot be reached, with the target described."""


@contextmanager
def connect(settings: Settings) -> Iterator[psycopg.Connection]:
    """Open a connection, committing on success and rolling back on error.

    The password never appears in the error message — only ``safe_conninfo``.
    """
    try:
        conn = psycopg.connect(settings.conninfo)
    except psycopg.OperationalError as exc:
        raise DatabaseUnavailable(
            f"cannot connect to {settings.safe_conninfo}: {exc.args[0].strip() if exc.args else exc}"
        ) from exc

    try:
        with conn:
            yield conn
    finally:
        conn.close()
