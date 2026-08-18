"""Collection orchestration.

Every run is bounded by a CollectionPlan — which leagues, which weeks, which tables. A live
run and a backfill differ only in the plan they are given (DECISIONS.md D-18).

The collectors themselves are not implemented yet; they need real API payloads to be written
against (D-33). What exists here is the plan, the run record, and the shape the collectors
will fill in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from .config import Settings
from .db import connect
from .yahoo import YahooClient


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

    Resolved *per league*: eight leagues can sit on different weeks.
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
    warnings: list[str] = field(default_factory=list)


def record_run(settings: Settings, result: RunResult) -> int:
    """Write a run to collector_runs. Called on success and on failure alike (D-22)."""
    with connect(settings) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO collector_runs
                (started_at, finished_at, status, leagues_synced, rows_written, error)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                result.started_at,
                result.finished_at,
                result.status,
                result.leagues_synced,
                result.rows_written,
                result.error,
            ),
        )
        return cur.fetchone()[0]


def run(settings: Settings, plan: CollectionPlan) -> RunResult:
    """Execute a collection plan, recording the outcome whatever happens."""
    result = RunResult(started_at=datetime.now(UTC))
    client = YahooClient(settings)

    try:
        # Collectors run in dependency order: leagues -> teams -> players ->
        # draft_picks -> rosters -> matchups/standings/stats.
        client.league_keys(plan.season)
        raise NotImplementedError("collectors are not implemented yet")
    except Exception as exc:
        result.status = "failed"
        result.error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        result.finished_at = datetime.now(UTC)

    return result
