"""Configuration loading.

Values are read from, in ascending order of precedence:

  1. ``~/.config/tilastokeskus/env``
  2. ``.env`` in the working directory
  3. the process environment
  4. explicit command-line arguments

The season is deliberately a parameter with a computed default rather than a constant
anywhere in this package. See DECISIONS.md D-03.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from dotenv import load_dotenv

CONFIG_DIR = Path.home() / ".config" / "tilastokeskus"

# An NFL season labelled Y runs from September Y into February Y+1, and Yahoo publishes the
# game key for season Y during the preceding spring. Treat March as the changeover: before it,
# the current season is still the previous calendar year.
SEASON_ROLLOVER_MONTH = 3


def default_season(today: date | None = None) -> int:
    """The season to use when none is given explicitly."""
    today = today or datetime.now(UTC).date()
    return today.year if today.month >= SEASON_ROLLOVER_MONTH else today.year - 1


def _load_dotenv_files() -> None:
    """Load .env files. Later calls do not override values already set."""
    load_dotenv(CONFIG_DIR / "env")
    load_dotenv(Path(".env"))


@dataclass(frozen=True)
class Settings:
    pg_host: str
    pg_port: int
    pg_database: str
    pg_user: str
    pg_password: str
    season: int
    raw_dir: Path
    yahoo_client_id: str
    yahoo_client_secret: str
    yahoo_redirect_uri: str
    yahoo_refresh_token: str

    @property
    def conninfo(self) -> str:
        """libpq connection string. Password is included, so never log this."""
        return (
            f"host={self.pg_host} port={self.pg_port} dbname={self.pg_database} "
            f"user={self.pg_user} password={self.pg_password}"
        )

    @property
    def safe_conninfo(self) -> str:
        """Connection description without the password, for logs and error messages."""
        return f"{self.pg_user}@{self.pg_host}:{self.pg_port}/{self.pg_database}"


def load_settings(season: int | None = None) -> Settings:
    """Build Settings from the environment, with an optional season override."""
    _load_dotenv_files()

    env_season = os.getenv("TILASTO_SEASON")
    resolved_season = season or (int(env_season) if env_season else default_season())

    return Settings(
        pg_host=os.getenv("PGHOST", "localhost"),
        pg_port=int(os.getenv("PGPORT", "5432")),
        pg_database=os.getenv("PGDATABASE", "tilastokeskus"),
        pg_user=os.getenv("PGUSER", "tilasto_app"),
        pg_password=os.getenv("PGPASSWORD", ""),
        season=resolved_season,
        raw_dir=Path(os.getenv("TILASTO_RAW_DIR", "raw")),
        yahoo_client_id=os.getenv("YAHOO_CLIENT_ID", ""),
        yahoo_client_secret=os.getenv("YAHOO_CLIENT_SECRET", ""),
        yahoo_redirect_uri=os.getenv("YAHOO_REDIRECT_URI", "https://localhost:8000"),
        yahoo_refresh_token=os.getenv("YAHOO_REFRESH_TOKEN", ""),
    )
