"""Writes for ``leagues`` and ``teams``. Every write is an idempotent upsert (D-19).

Functions take a connection rather than settings: the caller owns the transaction — one per league
in a collection run — and tests pass a connection into a throwaway schema.
"""

from __future__ import annotations

from collections.abc import Iterable

import psycopg

from .parse import LeagueMeta, LeagueSettings, Team

# The metadata upsert names only the columns the metadata carries. It never writes `tier`, which
# is set by hand and presentation only (D-54), and never the settings columns, which a later
# metadata-only run would otherwise overwrite with nothing.
UPSERT_LEAGUE_META = """
INSERT INTO leagues (league_key, season, name, num_teams, scoring_type,
                     current_week, start_week, end_week, is_finished, updated_at)
VALUES (%(league_key)s, %(season)s, %(name)s, %(num_teams)s, %(scoring_type)s,
        %(current_week)s, %(start_week)s, %(end_week)s, %(is_finished)s, now())
ON CONFLICT (league_key) DO UPDATE SET
    season       = EXCLUDED.season,
    name         = EXCLUDED.name,
    num_teams    = EXCLUDED.num_teams,
    scoring_type = EXCLUDED.scoring_type,
    current_week = EXCLUDED.current_week,
    start_week   = EXCLUDED.start_week,
    end_week     = EXCLUDED.end_week,
    is_finished  = EXCLUDED.is_finished,
    updated_at   = now()
"""

STORE_SETTINGS = """
UPDATE leagues SET
    draft_type          = %(draft_type)s,
    is_auction_draft    = %(is_auction_draft)s,
    playoff_start_week  = %(playoff_start_week)s,
    settings_fetched_at = now(),
    updated_at          = now()
WHERE league_key = %(league_key)s
"""

UPSERT_TEAM = """
INSERT INTO teams (team_key, league_key, team_id, name, manager_name, is_owned_by_me, logo_url,
                   draft_position, draft_grade, has_draft_grade, draft_recap_url, updated_at)
VALUES (%(team_key)s, %(league_key)s, %(team_id)s, %(name)s, %(manager_name)s,
        %(is_owned_by_me)s, %(logo_url)s, %(draft_position)s, %(draft_grade)s,
        %(has_draft_grade)s, %(draft_recap_url)s, now())
ON CONFLICT (team_key) DO UPDATE SET
    league_key      = EXCLUDED.league_key,
    team_id         = EXCLUDED.team_id,
    name            = EXCLUDED.name,
    manager_name    = EXCLUDED.manager_name,
    is_owned_by_me  = EXCLUDED.is_owned_by_me,
    logo_url        = EXCLUDED.logo_url,
    draft_position  = EXCLUDED.draft_position,
    draft_grade     = EXCLUDED.draft_grade,
    has_draft_grade = EXCLUDED.has_draft_grade,
    draft_recap_url = EXCLUDED.draft_recap_url,
    updated_at      = now()
"""


def upsert_league_meta(conn: psycopg.Connection, meta: LeagueMeta) -> int:
    """Insert or refresh a league's metadata. Returns rows written: always 1."""
    cur = conn.execute(UPSERT_LEAGUE_META, vars(meta))
    return cur.rowcount


def store_league_settings(conn: psycopg.Connection, league_key: str,
                          settings: LeagueSettings) -> int:
    """Record a league's /settings and when they were fetched. The league row must exist."""
    cur = conn.execute(STORE_SETTINGS, {**vars(settings), "league_key": league_key})
    if cur.rowcount != 1:
        raise LookupError(f"league {league_key} is not stored; write its metadata first")
    return cur.rowcount


def upsert_teams(conn: psycopg.Connection, league_key: str, teams: Iterable[Team]) -> int:
    """Insert or refresh a league's teams. Returns rows written.

    An empty list is refused rather than written as nothing: a league with no teams is a parse
    that went wrong, not a quiet week (D-38).
    """
    teams = list(teams)
    if not teams:
        raise ValueError(f"no teams to write for {league_key}")
    stray = [t.team_key for t in teams if t.league_key != league_key]
    if stray:
        raise ValueError(f"teams {stray} do not belong to {league_key}")
    written = 0
    for team in teams:
        written += conn.execute(UPSERT_TEAM, vars(team)).rowcount
    return written


def leagues_needing_settings(conn: psycopg.Connection, league_keys: Iterable[str]) -> set[str]:
    """Keys whose /settings have never been stored: not yet in `leagues`, or never fetched."""
    keys = list(league_keys)
    fetched = {row[0] for row in conn.execute(
        "SELECT league_key FROM leagues "
        "WHERE league_key = ANY(%s) AND settings_fetched_at IS NOT NULL",
        (keys,),
    )}
    return set(keys) - fetched
