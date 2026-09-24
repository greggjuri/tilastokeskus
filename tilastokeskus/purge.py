"""Deletion of Yahoo Materials and Fantasy Information (DECISIONS.md D-50).

Two operations that are deliberately separate and never implied by one another:

  * **data** — the collected rows and the raw response archive
  * **credentials** — the token file and the Yahoo keys in the environment files

Keeping them apart is the whole design. A routine "clear the data" must not silently cost the
Postgres password, and revoking access must not require throwing away the collection. Callers
choose explicitly; there is no default that does both.

Nothing here reports success from the fact that a statement ran. Every operation is followed by
an independent re-read of the thing it claimed to change, and a purge whose verification fails
raises rather than returning — the same argument as the D-41 default-privilege probe, applied to
the one command where "it said it worked" is least acceptable.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .config import CONFIG_DIR, Settings
from .db import connect

# Tables holding Yahoo Fantasy Information. Ordering is irrelevant — they are truncated in one
# statement so foreign keys resolve together rather than requiring a dependency walk.
FANTASY_TABLES = (
    "draft_picks",
    "leagues",
    "matchups",
    "player_weekly_stats",
    "players",
    "rosters",
    "standings",
    "teams",
    "transactions",
)

# collector_runs is health data by construction (D-07, D-08) and holds no Fantasy Information in
# any column meant for it. It is purged with the data anyway, because `error` stores raw exception
# text: a collector that fails mid-parse can put a league key, a team key, or a fragment of a
# payload into that column, and none of it is distinguishable from an ordinary message afterwards.
# Purging on a maybe costs a health history that is not Yahoo's; keeping it risks retaining what
# the purge exists to remove.
RUN_LOG_TABLE = "collector_runs"

# schema_migrations is deliberately NOT purged. It records which migrations have been applied and
# contains nothing from Yahoo. Truncating it would leave a migrated database that believes it is
# empty, so the next `tilasto migrate` would re-run 001 against existing tables and fail.
PURGED_TABLES = (*FANTASY_TABLES, RUN_LOG_TABLE)

# Values issued by or derived from Yahoo. Blanked rather than deleted, so the file keeps its shape
# and still documents which keys belong there. YAHOO_REDIRECT_URI is not in this list: it is our
# own configuration, is not secret, and was never issued by Yahoo.
YAHOO_CREDENTIAL_KEYS = (
    "YAHOO_CLIENT_ID",
    "YAHOO_CLIENT_SECRET",
    "YAHOO_REFRESH_TOKEN",
)

TOKEN_FILENAME = ".yahoofantasy"


class PurgeVerificationFailed(RuntimeError):
    """A purge ran but the follow-up check found the thing it deleted still present.

    Raised rather than returned. A purge that cannot prove it worked has not worked, and the one
    obligation this command exists to satisfy is not one to report optimistically.
    """


@dataclass
class DataPurgeReport:
    rows_deleted: dict[str, int] = field(default_factory=dict)
    archive_path: Path | None = None
    archive_files_deleted: int = 0
    verified: bool = False
    dry_run: bool = False

    @property
    def total_rows(self) -> int:
        return sum(self.rows_deleted.values())


@dataclass
class CredentialPurgeReport:
    token_files_deleted: list[Path] = field(default_factory=list)
    env_files_cleared: dict[Path, list[str]] = field(default_factory=dict)
    preserved_keys: dict[Path, list[str]] = field(default_factory=dict)
    verified: bool = False
    dry_run: bool = False


def _count_rows(settings: Settings) -> dict[str, int]:
    """Row counts for every table this module would purge, read fresh from the database."""
    counts: dict[str, int] = {}
    with connect(settings) as conn, conn.cursor() as cur:
        for table in PURGED_TABLES:
            cur.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = %s",
                (table,),
            )
            if not cur.fetchone()[0]:
                continue  # table absent, e.g. before the first migration
            # Identifier comes from PURGED_TABLES, never from input.
            cur.execute(f"SELECT count(*) FROM {table}")
            counts[table] = cur.fetchone()[0]
    return counts


def purge_data(settings: Settings, *, dry_run: bool = True) -> DataPurgeReport:
    """Delete collected rows and the raw response archive, then prove both are gone."""
    report = DataPurgeReport(dry_run=dry_run, archive_path=Path(settings.raw_dir))
    report.rows_deleted = _count_rows(settings)

    archive = Path(settings.raw_dir)
    if archive.is_dir():
        report.archive_files_deleted = sum(1 for p in archive.rglob("*") if p.is_file())

    if dry_run:
        return report

    present = [t for t in PURGED_TABLES if t in report.rows_deleted]
    if present:
        with connect(settings) as conn, conn.cursor() as cur:
            # One statement: CASCADE resolves the foreign keys between them without an ordering
            # here that would silently rot as the schema changes.
            cur.execute(f"TRUNCATE {', '.join(present)} RESTART IDENTITY CASCADE")

    if archive.is_dir():
        shutil.rmtree(archive)

    report.verified = verify_data_purged(settings)
    if not report.verified:
        raise PurgeVerificationFailed(
            "purge ran but verification failed: rows or archive files are still present. "
            "The database and archive must be inspected by hand before this is treated as done."
        )
    return report


def verify_data_purged(settings: Settings) -> bool:
    """Re-read the database and the filesystem. True only if both are genuinely empty.

    Deliberately independent of the purge: it re-counts rather than trusting a return value, so a
    TRUNCATE that silently affected nothing cannot pass.
    """
    if any(count > 0 for count in _count_rows(settings).values()):
        return False
    archive = Path(settings.raw_dir)
    return not (archive.is_dir() and any(p.is_file() for p in archive.rglob("*")))


def _clear_env_keys(path: Path, keys: tuple[str, ...], *, dry_run: bool) -> tuple[list[str], list[str]]:
    """Blank the named keys in a dotenv file, leaving every other line untouched.

    Returns (cleared, preserved). The file is rewritten line by line rather than reconstructed, so
    comments, ordering, blank lines, and unrelated values survive exactly as they were.
    """
    cleared: list[str] = []
    preserved: list[str] = []
    out: list[str] = []

    for line in path.read_text().splitlines():
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in keys:
            if stripped.split("=", 1)[1]:  # only report keys that actually held something
                cleared.append(key)
            out.append(f"{key}=")
        else:
            preserved.append(key)
            out.append(line)

    if not dry_run and cleared:
        path.write_text("\n".join(out) + "\n")
    return cleared, preserved


def env_file_paths() -> list[Path]:
    """Dotenv files that may hold Yahoo credentials, in the order config.py loads them."""
    return [p for p in (CONFIG_DIR / "env", Path(".env")) if p.is_file()]


def token_file_paths() -> list[Path]:
    """Token files written by ``yahoofantasy login``.

    The working directory is where the library writes and where the transport reads
    (``transport.py``); the home copy is checked because older runs may have left one there.
    """
    return [p for p in (Path(TOKEN_FILENAME), Path.home() / TOKEN_FILENAME) if p.is_file()]


def purge_credentials(*, dry_run: bool = True) -> CredentialPurgeReport:
    """Delete token files and blank the Yahoo keys in dotenv files, then prove it.

    Everything that is not a Yahoo credential is left alone — the Postgres password above all,
    which lives in the same file and has nothing to do with Yahoo.
    """
    report = CredentialPurgeReport(dry_run=dry_run)
    tokens = token_file_paths()
    report.token_files_deleted = list(tokens)

    for path in env_file_paths():
        cleared, preserved = _clear_env_keys(path, YAHOO_CREDENTIAL_KEYS, dry_run=dry_run)
        report.env_files_cleared[path] = cleared
        report.preserved_keys[path] = preserved

    if dry_run:
        return report

    for path in tokens:
        path.unlink()

    report.verified = verify_credentials_purged()
    if not report.verified:
        raise PurgeVerificationFailed(
            "credential purge ran but verification failed: a token file or a Yahoo key is still "
            "present. Treat the token as live and revoke it at Yahoo rather than assuming it is gone."
        )
    return report


def verify_credentials_purged() -> bool:
    """True only if no token file exists and no Yahoo key still holds a value."""
    if token_file_paths():
        return False
    for path in env_file_paths():
        for line in path.read_text().splitlines():
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            if key.strip() in YAHOO_CREDENTIAL_KEYS and value.strip():
                return False
    return True
