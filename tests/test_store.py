"""Upserts, against the throwaway-schema database. Inputs are parsed from redacted fixtures."""

import dataclasses

import psycopg
import pytest
from conftest import load_fixture

from tilastokeskus.parse import parse_league_meta, parse_settings, parse_teams
from tilastokeskus.parse_draft import parse_draft
from tilastokeskus.store import (
    leagues_needing_draft,
    leagues_needing_settings,
    store_league_settings,
    upsert_draft_picks,
    upsert_league_meta,
    upsert_players,
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


def test_migration_002_renames_eligible_positions_to_nfl_positions(db):
    """PRP-02. The fixture applies every migration in order, so this is the schema tests see."""
    columns = {r[0] for r in db.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = 'players'")}
    assert "nfl_positions" in columns
    assert "eligible_positions" not in columns
    comment = db.execute("SELECT col_description('players'::regclass, attnum) FROM pg_attribute "
                         "WHERE attrelid = 'players'::regclass AND attname = 'nfl_positions'")
    assert "League-independent" in comment.fetchone()[0]


def test_migrations_apply_in_filename_order():
    from tilastokeskus import migrate
    assert [m.version for m in migrate.discover()][:2] == ["001_initial", "002_players_nfl_positions"]


# ---- players and draft picks (PRP-02) --------------------------------------------------------



def idp_league(db):
    """The IDP league written as PRP-01 would: metadata and its ten teams. Returns (meta, draft)."""
    teams_payload = load_fixture("teams_previous_rank")
    key = teams_payload["fantasy_content"]["league"][0]["league_key"]
    meta = parse_league_meta(teams_payload, key)
    upsert_league_meta(db, meta)
    upsert_teams(db, key, parse_teams(teams_payload, meta))
    return meta, parse_draft(load_fixture("draft_idp"), meta, "470")


def draft_counts(db):
    return tuple(db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                 for t in ("players", "draft_picks"))


def test_draft_writes_players_then_picks(db):
    meta, (players, picks) = idp_league(db)
    assert upsert_players(db, players) == 190
    assert upsert_draft_picks(db, meta.league_key, picks) == 190
    assert draft_counts(db) == (190, 190)
    orphans = db.execute("SELECT count(*) FROM draft_picks d LEFT JOIN players p USING (player_key) "
                         "WHERE p.player_key IS NULL").fetchone()[0]
    assert orphans == 0


def test_draft_twice_changes_no_row_counts_or_values(db):
    meta, (players, picks) = idp_league(db)
    for _ in range(2):
        upsert_players(db, players)
        upsert_draft_picks(db, meta.league_key, picks)
    assert draft_counts(db) == (190, 190)
    row = db.execute("SELECT position, nfl_positions FROM players WHERE position = 'DT,DE'")
    assert row.fetchone() == ("DT,DE", ["DT", "DE"])


def test_a_pick_before_its_player_is_refused_by_the_foreign_key(db):
    meta, (_, picks) = idp_league(db)
    with pytest.raises(psycopg.errors.ForeignKeyViolation, match="player_key"):
        upsert_draft_picks(db, meta.league_key, picks[:1])


def test_the_same_player_from_two_leagues_is_one_row(db):
    _, (players, _) = idp_league(db)
    upsert_players(db, players[:5])
    upsert_players(db, players[:5])              # as a second league drafting the same five
    assert draft_counts(db)[0] == 5


def test_empty_and_stray_draft_input_is_refused(db):
    meta, (_, picks) = idp_league(db)
    with pytest.raises(ValueError, match="no players"):
        upsert_players(db, [])
    with pytest.raises(ValueError, match="no draft picks"):
        upsert_draft_picks(db, meta.league_key, [])
    with pytest.raises(ValueError, match="do not belong"):
        upsert_draft_picks(db, "470.l.424242", picks)


def test_leagues_needing_draft(db):
    meta, (players, picks) = idp_league(db)
    other = "470.l.424242"
    assert leagues_needing_draft(db, [meta.league_key, other]) == {meta.league_key, other}
    upsert_players(db, players)
    upsert_draft_picks(db, meta.league_key, picks)
    assert leagues_needing_draft(db, [meta.league_key, other]) == {other}
    db.execute("DELETE FROM draft_picks")        # purged picks: the gate reopens on its own
    assert leagues_needing_draft(db, [meta.league_key]) == {meta.league_key}
