"""Collection orchestration.

Every run is bounded by a CollectionPlan — which leagues, which weeks, which tables. A live
run and a backfill differ only in the plan they are given (docs/DECISIONS.md D-18).

PRP-01 collects ``leagues`` and ``teams``: discover the season's leagues, then for each one fetch,
archive, parse and commit before moving to the next. PRP-02 adds each league's draft — ``players``
and ``draft_picks`` — once per season, in a transaction of its own after the league's teams. A league that fails is recorded and skipped;
the leagues before it stay committed. Every run that reaches the database writes a
``collector_runs`` row — success, partial or failed (D-22).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import psycopg

from .parse import (
    LeagueMeta,
    LeagueSettings,
    parse_discovery,
    parse_league_meta,
    parse_settings,
    parse_teams,
)
from .parse_draft import parse_draft
from .ratelimit import RateLimitExhausted, RequestTimedOut
from .store import (
    leagues_needing_draft,
    leagues_needing_settings,
    store_league_settings,
    upsert_draft_picks,
    upsert_league_meta,
    upsert_players,
    upsert_teams,
)
from .transport import AuthenticationFailed
from .yahoo import YahooClient


class UnknownLeague(ValueError):
    """A --league key that is not one of the season's leagues. A usage error, not a run."""


@dataclass(frozen=True)
class CollectionPlan:
    """What a single collection run should cover."""

    season: int
    league_keys: list[str] | None = None   # None means every league in the season
    weeks: list[int] | None = None         # None means each league's own current_week (D-24a)
    draft_only: bool = False

    def describe(self) -> str:
        leagues = "all leagues" if self.league_keys is None else f"{len(self.league_keys)} league(s)"
        if self.draft_only:
            return f"season {self.season}, {leagues}, draft results only"
        if self.weeks is None:
            return f"season {self.season}, {leagues}, current week"
        return f"season {self.season}, {leagues}, weeks {self.weeks[0]}-{self.weeks[-1]}"


def resolve_weeks(plan: CollectionPlan, league: dict) -> list[int]:
    """The weeks to collect for one league.

    An explicit range from ``--weeks`` is used as given. Otherwise the week comes from
    ``current_week`` on that league's own resource — never computed from a date (D-24a).

    Yahoo's week boundary rolls over on Tuesday, in a timezone that is not necessarily the
    host's, and drifts around Thanksgiving and the international games. Deriving the week from
    the calendar produces correct-looking rows filed under the wrong week, which idempotent
    upserts then write cleanly (D-19) — silent corruption rather than visible failure.

    Resolved *per league*: fifteen leagues can sit on different weeks.
    """
    if plan.weeks is not None:
        return plan.weeks

    current = league.get("current_week")
    if current is None:
        raise ValueError(
            f"league {league.get('league_key', '?')} has no current_week; "
            "refusing to guess the week from the calendar (D-24a)"
        )
    return [int(current)]


@dataclass
class RunResult:
    """Outcome of a run, mirrored into the collector_runs table."""

    started_at: datetime
    finished_at: datetime | None = None
    status: str = "failed"                 # 'success' | 'partial' | 'failed'
    leagues_synced: int = 0
    rows_written: int = 0
    error: str | None = None
    leagues_planned: int = 0
    failed_leagues: list[tuple[str, str]] = field(default_factory=list)


def record_run(conn: psycopg.Connection, result: RunResult) -> int:
    """Write a run to collector_runs. Called on success and on failure alike (D-22)."""
    row = conn.execute(
        """
        INSERT INTO collector_runs
            (started_at, finished_at, status, leagues_synced, rows_written, error)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (result.started_at, result.finished_at, result.status, result.leagues_synced,
         result.rows_written, result.error),
    ).fetchone()
    return row[0]


def select_leagues(discovered: list[str], requested: list[str] | None) -> list[str]:
    """Every discovered league, or the requested ones — each of which must have been discovered."""
    if requested is None:
        return list(discovered)
    unknown = [key for key in requested if key not in discovered]
    if unknown:
        raise UnknownLeague(f"not a league of this season on this account: {', '.join(unknown)}")
    return list(dict.fromkeys(requested))


def require_buildable(plan: CollectionPlan) -> None:
    """Refuse scopes whose tables are not collected yet — before any request, dry run included."""
    if plan.weeks is not None or plan.draft_only:
        raise NotImplementedError(
            "--weeks and --draft-only collect week-scoped and draft tables, which do not exist "
            "yet: draft picks are init-02, backfill is phase 5. See docs/TASK.md.")


# Failures that are not one league's: they abort the run after it is recorded (D-56, D-58).
ABORTS_RUN = (AuthenticationFailed, RateLimitExhausted, RequestTimedOut, psycopg.OperationalError)


def collect_league(conn: psycopg.Connection, client: YahooClient, league_key: str,
                   season: int, fetch_settings: bool) -> tuple[int, LeagueMeta]:
    """Fetch, archive and parse one league and its teams, then write them.

    Returns rows written and the league's metadata, which the draft phase needs.

    Everything is fetched and parsed before anything is written, so a parse failure leaves the
    league's rows exactly as they were.
    """
    payload = client.teams(league_key)
    meta = parse_league_meta(payload, league_key)
    if meta.season != season:
        raise ValueError(f"league {league_key} reports season {meta.season}, run is {season}")
    teams = parse_teams(payload, meta)
    settings: LeagueSettings | None = None
    if fetch_settings:
        settings = parse_settings(client.settings(league_key), league_key)

    rows = upsert_league_meta(conn, meta)
    if settings is not None:
        rows += store_league_settings(conn, league_key, settings)
    return rows + upsert_teams(conn, league_key, teams), meta


def collect_draft(conn: psycopg.Connection, client: YahooClient, meta: LeagueMeta,
                  game_key: str) -> int:
    """Fetch, archive and parse a league's draft, then write players before picks."""
    players, picks = parse_draft(client.draft(meta.league_key), meta, game_key)
    return upsert_players(conn, players) + upsert_draft_picks(conn, meta.league_key, picks)


def decide_status(result: RunResult) -> str:
    if result.leagues_synced == 0 or result.rows_written == 0:
        # A run that "succeeds" and writes nothing is a failure: the leagues are known to exist.
        return "failed"
    if result.failed_leagues or result.leagues_synced < result.leagues_planned:
        return "partial"
    return "success"


def run(plan: CollectionPlan, client: YahooClient, conn: psycopg.Connection,
        started_at: datetime | None = None) -> RunResult:
    """Execute a collection plan, recording the outcome whatever happens.

    ``conn`` should be in autocommit mode, so each league's ``transaction()`` commits on its own.

    Raises — after recording the run — on failures that are not one league's: authentication
    (D-29), exhausted backoff (stopping is the point of the limit), requests that keep timing out
    (a stalled network is everyone's problem), a lost database connection, and anything before
    the league loop. An unknown ``--league`` raises without recording: it is
    a usage error, not a run.
    """
    require_buildable(plan)
    result = RunResult(started_at=started_at or datetime.now(UTC))
    try:
        discovery = parse_discovery(client.discover(plan.season), plan.season)
        keys = select_leagues(discovery.league_keys, plan.league_keys)
    except UnknownLeague:
        raise
    except Exception as exc:
        _finish(conn, result, exc)
        raise

    result.leagues_planned = len(keys)
    try:
        need_settings = leagues_needing_settings(conn, keys)
        need_draft = leagues_needing_draft(conn, keys)
        for key in keys:
            try:
                with conn.transaction():
                    rows, meta = collect_league(conn, client, key, plan.season,
                                                key in need_settings)
            except ABORTS_RUN:
                raise
            except Exception as exc:  # noqa: BLE001 - one league's failure is recorded, not fatal
                result.failed_leagues.append((key, f"{type(exc).__name__}: {exc}"))
                continue
            result.leagues_synced += 1
            result.rows_written += rows

            if key not in need_draft:
                continue
            # Its own transaction: the draft is one-shot and teams are daily, so a draft that will
            # not parse must not also stop the league's daily refresh (PRP-02 Q3). The run still
            # reads partial, with the failure marked as the draft's.
            try:
                with conn.transaction():
                    result.rows_written += collect_draft(conn, client, meta,
                                                         discovery.game_key)
            except ABORTS_RUN:
                raise
            except Exception as exc:  # noqa: BLE001 - recorded against the league, not fatal
                result.failed_leagues.append((key, f"draft: {type(exc).__name__}: {exc}"))
    except Exception as exc:
        _finish(conn, result, exc)
        raise

    _finish(conn, result, None)
    return result


def _finish(conn: psycopg.Connection, result: RunResult, exc: BaseException | None) -> None:
    result.finished_at = datetime.now(UTC)
    result.status = "failed" if exc is not None else decide_status(result)
    errors = [f"{key}: {message}" for key, message in result.failed_leagues]
    if exc is not None:
        errors.insert(0, f"{type(exc).__name__}: {exc}")
    result.error = "; ".join(errors) or None
    record_run(conn, result)
