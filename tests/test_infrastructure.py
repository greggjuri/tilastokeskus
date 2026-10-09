"""The test infrastructure itself: the throwaway-schema database fixture, the redactor, and the
fixtures it produced. A fixture that lost its hazards would make every parser test pass for the
wrong reason."""

import importlib.util
import json
from pathlib import Path

from conftest import FIXTURES, load_fixture, throwaway_schema

REDACT = Path(__file__).parent.parent / "scripts" / "redact_payload.py"
spec = importlib.util.spec_from_file_location("redact_payload", REDACT)
redact_payload = importlib.util.module_from_spec(spec)
spec.loader.exec_module(redact_payload)


def flat(entries):
    merged = {}
    for item in entries:
        if isinstance(item, dict):
            merged.update(item)
    return merged


# ---- db fixture ------------------------------------------------------------------------------

def test_db_fixture_applies_the_schema_in_a_throwaway_schema(db):
    schema = db.execute("SELECT current_schema()").fetchone()[0]
    assert schema.startswith("test_")
    tables = {r[0] for r in db.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = %s", (schema,))}
    assert {"leagues", "teams", "collector_runs"} <= tables


def test_db_fixture_writes_only_to_its_own_schema(db):
    before = db.execute("SELECT count(*) FROM public.leagues").fetchone()[0]
    db.execute("INSERT INTO leagues (league_key, season, name) VALUES ('470.l.1', 2026, 'x')")
    assert db.execute("SELECT count(*) FROM leagues").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM public.leagues").fetchone()[0] == before


def test_throwaway_schema_is_gone_afterwards(db):
    with throwaway_schema() as conn:
        schema = conn.execute("SELECT current_schema()").fetchone()[0]
        conn.execute("INSERT INTO leagues (league_key, season, name) VALUES ('470.l.1', 2026, 'x')")
    found = db.execute("SELECT count(*) FROM pg_namespace WHERE nspname = %s", (schema,))
    assert found.fetchone()[0] == 0


# ---- redactor --------------------------------------------------------------------------------

def test_redactor_preserves_structure_and_scalar_types():
    source = {"league": [{"league_key": "470.l.98765", "name": "Real Name", "num_teams": 10,
                          "start_week": "1", "is_finished": 1, "logo_url": "https://x.y/z.png",
                          "renew": ""},
                         {"teams": {"count": 1, "0": {"team": [[
                             {"team_key": "470.l.98765.t.4"}, [], {"guid": "ABCDEFG"}]]}}}]}
    out = redact_payload.Redactor().walk(source)

    league, teams = out["league"]
    assert league["name"] == "redacted"
    assert league["num_teams"] == 10                         # allowlisted
    assert league["start_week"] == "1"                        # numeric string stays a string
    assert league["logo_url"].startswith("https://example.invalid")
    assert league["renew"] == ""
    team = teams["teams"]["0"]["team"][0]
    assert team[1] == []                                     # placeholder kept
    assert team[2]["guid"] == "redacted"
    # The league id is rewritten, consistently, in league and team keys alike.
    assert league["league_key"] == "470.l.100001"
    assert team[0]["team_key"] == "470.l.100001.t.4"


def test_redactor_records_every_removed_value_with_its_field():
    r = redact_payload.Redactor()
    r.walk({"nickname": "Someone Real", "name": "abc"})
    assert r.removed == {"Someone Real": "nickname"}           # short values are not tracked


def test_redactor_refuses_to_write_when_a_value_survives(tmp_path, capsys):
    import gzip
    source = tmp_path / "in.json.gz"
    # "week" is redacted under an unknown field but kept under coverage_type.
    with gzip.open(source, "wt") as fh:
        json.dump({"mystery": "week", "coverage_type": "week"}, fh)
    assert redact_payload.main([str(tmp_path / "out"), f"x={source}"]) == 1
    assert "mystery" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


# ---- the fixtures still carry their hazards --------------------------------------------------

def _teams(name):
    block = load_fixture(name)["fantasy_content"]["league"][1]["teams"]
    return [flat(block[k]["team"][0]) for k in block if k.isdigit()]


def test_teams_fixture_has_one_owned_team_and_nine_absent_flags():
    teams = _teams("teams")
    assert len(teams) == 10
    assert sum("is_owned_by_current_login" in t for t in teams) == 1


def test_teams_fixture_mixes_int_and_string_number_of_trades():
    types = {type(t["number_of_trades"]) for t in _teams("teams")}
    assert types == {int, str}


def test_previous_rank_fixture_has_the_field_on_some_teams_only():
    present = [("previous_season_team_rank" in t) for t in _teams("teams_previous_rank")]
    assert any(present) and not all(present)


def test_settings_fixtures_carry_string_flags():
    for name in ("settings", "settings_invite_permission"):
        s = load_fixture(name)["fantasy_content"]["league"][1]["settings"][0]
        assert s["is_auction_draft"] == "0" and s["uses_faab"] == "0"


def test_discovery_2025_has_finished_leagues_and_a_string_current_week():
    text = (FIXTURES / "discovery_2025.json").read_text()
    assert '"is_finished": 1' in text
    assert '"current_week": "17"' in text


def test_db_fixture_fails_rather_than_skips_when_postgres_is_unreachable(monkeypatch):
    import pytest
    monkeypatch.setenv("PGPORT", "1")                      # nothing listens there
    with (
        pytest.raises(pytest.fail.Exception, match="fail rather than skip"),
        throwaway_schema(),
    ):
        pass


# ---- draft fixture (PRP-02) ------------------------------------------------------------------

def _draft_picks():
    league = load_fixture("draft_idp")["fantasy_content"]["league"]
    block = next(e["draft_results"] for e in league if "draft_results" in e)
    return [block[k]["draft_result"] for k in block if k.isdigit()]


def _drafted_player(pick):
    return flat(pick["0"]["players"]["0"]["player"][0])


def test_draft_fixture_has_190_picks_each_with_its_player_nested():
    picks = _draft_picks()
    assert len(picks) == 190
    assert all(_drafted_player(p)["player_key"] == p["player_key"] for p in picks)


def test_draft_fixture_keeps_the_idp_hazards():
    players = [_drafted_player(p) for p in _draft_picks()]
    assert sum(p["primary_position"] == "D" for p in players) == 9
    assert any("," in p["display_position"] for p in players)
    assert {type(p["uniform_number"]) for p in players} == {str, bool}
    assert sum("linked_player" in p for p in players) == 1


def test_draft_fixture_player_keys_still_end_in_their_player_id():
    """The redactor rewrites keys and ids through one map, so the parser's check still holds."""
    players = [_drafted_player(p) for p in _draft_picks()]
    assert all(p["player_key"].split(".")[-1] == p["player_id"] for p in players)
    assert all(p["player_key"].startswith("470.p.9") for p in players)


def test_redactor_maps_player_keys_and_ids_consistently():
    r = redact_payload.Redactor()
    out = r.walk({"player_key": "470.p.31002", "player_id": "31002",
                  "linked": {"player_key": "470.p.31002"}, "other": "470.p.555"})
    assert out["player_key"] == "470.p.900001" == out["linked"]["player_key"]
    assert out["player_id"] == "900001"
    assert out["other"] == "470.p.900002"
