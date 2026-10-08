"""Upserts, against the throwaway-schema database. Inputs are parsed from redacted fixtures."""

import dataclasses

import psycopg
import pytest
from conftest import load_fixture

from tilastokeskus.parse import parse_league_meta, parse_settings, parse_teams
from tilastokeskus.store import (
    leagues_needing_settings,
    store_league_settings,
    upsert_league_meta,
    upsert_teams,
)


def parsed():
    payload = load_fixture("teams")
    key = payload["fantasy_content"]["league"][0]["league_key"]
    meta = parse_league_meta(payload, key)
    settings_payload = load_fixture("settings")
    settings_payload["fantasy_content"]["league"][0]["league_key"] = key
    settings = parse_settings(settings_payload, key)
    return meta, settings, parse_teams(payload, meta)


def counts(db):
    return tuple(db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("leagues", "teams"))


def write_all(db, meta, settings, teams):
    upsert_league_meta(db, meta)
    store_league_settings(db, meta.league_key, settings)
    upsert_teams(db, meta.league_key, teams)


def test_writes_one_league_and_ten_teams(db):
    meta, settings, teams = parsed()
    assert upsert_league_meta(db, meta) == 1
    assert store_league_settings(db, meta.league_key, settings) == 1
    assert upsert_teams(db, meta.league_key, teams) == 10
    assert counts(db) == (1, 10)


def test_twice_changes_no_row_counts_and_refreshes_updated_at(db):
    """D-19. Within one transaction now() is fixed, so the rows are backdated first."""
    meta, settings, teams = parsed()
    write_all(db, meta, settings, teams)
    db.execute("UPDATE leagues SET updated_at = now() - interval '1 day'")
    db.execute("UPDATE teams SET updated_at = now() - interval '1 day'")

    write_all(db, meta, settings, teams)

    assert counts(db) == (1, 10)
    stale = db.execute(
        "SELECT (SELECT count(*) FROM leagues WHERE updated_at < now()) "
        "     + (SELECT count(*) FROM teams WHERE updated_at < now())").fetchone()[0]
    assert stale == 0


def test_metadata_upsert_preserves_settings(db):
    meta, settings, teams = parsed()
    write_all(db, meta, settings, teams)
    upsert_league_meta(db, dataclasses.replace(meta, current_week=meta.current_week + 1))
    row = db.execute("SELECT draft_type, is_auction_draft, playoff_start_week, "
                     "settings_fetched_at IS NOT NULL, current_week FROM leagues").fetchone()
    assert row == ("live", False, 16, True, meta.current_week + 1)


def test_tier_set_by_hand_survives_every_upsert(db):
    """D-54: presentation only, never written by the collector."""
    meta, settings, teams = parsed()
    write_all(db, meta, settings, teams)
    db.execute("UPDATE leagues SET tier = 'focus'")
    write_all(db, meta, settings, teams)
    assert db.execute("SELECT tier FROM leagues").fetchone()[0] == "focus"


def test_settings_columns_stay_null_until_fetched(db):
    meta, _, _ = parsed()
    upsert_league_meta(db, meta)
    row = db.execute("SELECT draft_type, is_auction_draft, playoff_start_week, "
                     "settings_fetched_at FROM leagues").fetchone()
    assert row == (None, None, None, None)


def test_settings_for_an_unstored_league_raise(db):
    _, settings, _ = parsed()
    with pytest.raises(LookupError, match="not stored"):
        store_league_settings(db, "470.l.424242", settings)


def test_two_owned_teams_are_refused_by_the_index(db):
    meta, _, teams = parsed()
    upsert_league_meta(db, meta)
    teams = [dataclasses.replace(t, is_owned_by_me=True) for t in teams[:2]]
    with pytest.raises(psycopg.errors.UniqueViolation, match="teams_owned_one_per_league_idx"):
        upsert_teams(db, meta.league_key, teams)


def test_empty_and_stray_teams_are_refused(db):
    meta, _, teams = parsed()
    upsert_league_meta(db, meta)
    with pytest.raises(ValueError, match="no teams"):
        upsert_teams(db, meta.league_key, [])
    with pytest.raises(ValueError, match="do not belong"):
        upsert_teams(db, "470.l.424242", teams)
    assert counts(db) == (1, 0)


def test_leagues_needing_settings(db):
    meta, settings, _ = parsed()
    unknown = "470.l.424242"
    assert leagues_needing_settings(db, [meta.league_key, unknown]) == {meta.league_key, unknown}
    upsert_league_meta(db, meta)
    assert leagues_needing_settings(db, [meta.league_key]) == {meta.league_key}
    store_league_settings(db, meta.league_key, settings)
    assert leagues_needing_settings(db, [meta.league_key, unknown]) == {unknown}


def test_read_only_role_can_read_what_the_collector_writes(db):
    """D-41: default privileges reach these tables. Checked against the real schema's tables."""
    for table in ("leagues", "teams"):
        assert db.execute("SELECT has_table_privilege('tilasto_ro', %s, 'SELECT')",
                          (f"public.{table}",)).fetchone()[0]
