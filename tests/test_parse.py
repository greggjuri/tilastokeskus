"""Parsers, against redacted fixtures from the raw archive. Hazard variants are made by editing a
loaded fixture, never by writing a payload by hand (docs/TESTING.md)."""

import copy

import pytest
from conftest import load_fixture

from tilastokeskus.parse import (
    UnexpectedPayload,
    as_flag,
    as_int,
    flatten,
    parse_discovery,
    parse_league_meta,
    parse_settings,
    parse_teams,
)

# ---- primitives: contract first --------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [("0", False), ("1", True), (0, False), (1, True)])
def test_as_flag_accepts_yahoos_forms(value, expected):
    assert as_flag(value, "f") is expected


def test_as_flag_string_zero_is_false():
    """The trap: bool('0') is True. A truth test would read every league as an auction."""
    assert bool("0") is True
    assert as_flag("0", "is_auction_draft") is False


@pytest.mark.parametrize("value", ["2", "", None, "true", "yes", True, False, 2, "01", " 1"])
def test_as_flag_refuses_anything_else(value):
    with pytest.raises(UnexpectedPayload, match="f"):
        as_flag(value, "f", "league X")


@pytest.mark.parametrize("value, expected", [(5, 5), ("5", 5), ("17", 17), ("-1", -1), (0, 0)])
def test_as_int_accepts_ints_and_digit_strings(value, expected):
    assert as_int(value, "f") == expected


@pytest.mark.parametrize("value", ["", "5.0", None, True, 5.0, " 5", "5 ", "five", [], {}])
def test_as_int_refuses_anything_else(value):
    with pytest.raises(UnexpectedPayload):
        as_int(value, "f")


def test_flatten_merges_and_skips_placeholders():
    assert flatten([{"a": 1}, [], {"b": 2}, []]) == {"a": 1, "b": 2}


def test_flatten_refuses_conflicting_duplicates_and_non_objects():
    with pytest.raises(UnexpectedPayload, match="twice"):
        flatten([{"a": 1}, {"a": 2}])
    with pytest.raises(UnexpectedPayload, match="not an object"):
        flatten([{"a": 1}, "b"])
    with pytest.raises(UnexpectedPayload, match="list"):
        flatten({"a": 1})


def test_errors_name_the_field_and_where():
    with pytest.raises(UnexpectedPayload, match=r"league 470\.l\.9: num_teams is 'x'"):
        as_int("x", "num_teams", "league 470.l.9")


# ---- discovery -------------------------------------------------------------------------------

def test_discovery_2026_yields_the_game_key_and_fifteen_leagues():
    d = parse_discovery(load_fixture("discovery_2026"), 2026)
    assert d.game_key == "470"
    assert len(d.league_keys) == 15
    assert all(k.startswith("470.l.") for k in d.league_keys)


def test_discovery_2025_yields_its_own_game_key():
    d = parse_discovery(load_fixture("discovery_2025"), 2025)
    assert d.game_key == "461"
    assert len(d.league_keys) == 6


def _leagues_block(payload):
    return payload["fantasy_content"]["users"]["0"]["user"][1]["games"]["0"]["game"][1]["leagues"]


def test_discovery_refuses_the_wrong_season():
    with pytest.raises(UnexpectedPayload, match="asked for season 2026, got 2025"):
        parse_discovery(load_fixture("discovery_2025"), 2026)


def test_discovery_refuses_a_count_that_disagrees_with_its_entries():
    payload = load_fixture("discovery_2026")
    _leagues_block(payload)["count"] = 16
    with pytest.raises(UnexpectedPayload, match="count says 16, found 15"):
        parse_discovery(payload, 2026)


def test_discovery_refuses_a_key_without_the_game_key_prefix():
    payload = load_fixture("discovery_2026")
    _leagues_block(payload)["3"]["league"][0]["league_key"] = "461.l.1"
    with pytest.raises(UnexpectedPayload, match="lacks the game key 470"):
        parse_discovery(payload, 2026)


def test_discovery_with_no_game_raises_rather_than_returning_empty():
    payload = load_fixture("discovery_2026")
    payload["fantasy_content"]["users"]["0"]["user"][1]["games"] = {"count": 0}
    with pytest.raises(UnexpectedPayload, match="expected one nfl game, found 0"):
        parse_discovery(payload, 2026)


# ---- league metadata -------------------------------------------------------------------------

def _key(payload):
    return payload["fantasy_content"]["league"][0]["league_key"]


def test_league_meta_from_a_teams_payload():
    payload = load_fixture("teams")
    meta = parse_league_meta(payload, _key(payload))
    assert meta.season == 2026 and meta.num_teams == 10
    assert meta.scoring_type == "head"
    assert meta.start_week == 1 and meta.end_week == 17       # both strings in the payload
    assert meta.is_finished is False                           # absent mid-season


def test_league_meta_from_a_settings_payload_is_the_same_shape():
    payload = load_fixture("settings")
    assert parse_league_meta(payload, _key(payload)).num_teams == 10


def test_league_meta_reads_a_finished_league_and_a_string_current_week():
    """2025 discovery carries is_finished=1 and current_week as the string '17'."""
    league = _leagues_block(load_fixture("discovery_2025"))["0"]["league"]
    payload = {"fantasy_content": {"league": league}}
    meta = parse_league_meta(payload, league[0]["league_key"])
    assert meta.is_finished is True
    assert meta.current_week == 17


def test_league_meta_raises_on_points_scoring():
    payload = load_fixture("teams")
    payload["fantasy_content"]["league"][0]["scoring_type"] = "points"
    with pytest.raises(UnexpectedPayload, match="scoring_type 'points' is unobserved"):
        parse_league_meta(payload, _key(payload))


def test_league_meta_raises_when_the_payload_is_for_another_league():
    payload = load_fixture("teams")
    with pytest.raises(UnexpectedPayload, match="payload is for"):
        parse_league_meta(payload, "470.l.999999")


def test_league_meta_raises_on_a_missing_required_field():
    payload = load_fixture("teams")
    del payload["fantasy_content"]["league"][0]["current_week"]
    with pytest.raises(UnexpectedPayload, match="'current_week' is absent"):
        parse_league_meta(payload, _key(payload))


def test_league_meta_does_not_depend_on_element_order():
    payload = load_fixture("teams")
    payload["fantasy_content"]["league"].reverse()
    assert parse_league_meta(payload, _key(load_fixture("teams"))).num_teams == 10


# ---- settings --------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["settings", "settings_invite_permission"])
def test_settings_parse(name):
    payload = load_fixture(name)
    s = parse_settings(payload, _key(payload))
    assert s.draft_type == "live"
    assert s.is_auction_draft is False
    assert s.playoff_start_week == 16                          # a string in the payload


def _settings_block(payload):
    return payload["fantasy_content"]["league"][1]["settings"][0]


@pytest.mark.parametrize("field, value, message", [
    ("is_auction_draft", "1", "auction draft"),
    ("uses_faab", "1", "FAAB"),
    ("draft_type", "self", "draft_type 'self' is unobserved"),
    ("is_auction_draft", "yes", "not a 0/1 flag"),
])
def test_settings_raise_on_the_unobserved(field, value, message):
    payload = load_fixture("settings")
    _settings_block(payload)[field] = value
    with pytest.raises(UnexpectedPayload, match=message):
        parse_settings(payload, _key(payload))


def test_settings_missing_flag_raises_rather_than_defaulting():
    payload = load_fixture("settings")
    del _settings_block(payload)["uses_faab"]
    with pytest.raises(UnexpectedPayload, match="'uses_faab' is absent"):
        parse_settings(payload, _key(payload))


# ---- teams -----------------------------------------------------------------------------------

def _meta(payload):
    return parse_league_meta(payload, _key(payload))


def _team_entries(payload):
    block = payload["fantasy_content"]["league"][1]["teams"]
    return [block[k]["team"][0] for k in block if k.isdigit()]


@pytest.mark.parametrize("name", ["teams", "teams_previous_rank"])
def test_teams_parse_ten_with_exactly_one_owned(name):
    payload = load_fixture(name)
    teams = parse_teams(payload, _meta(payload))
    assert len(teams) == 10
    assert sum(t.is_owned_by_me for t in teams) == 1
    assert all(t.team_key == f"{_key(payload)}.t.{t.team_id}" for t in teams)
    assert all(isinstance(t.draft_position, int) for t in teams)


def test_absent_owned_flag_is_false_not_an_error():
    payload = load_fixture("teams")
    teams = parse_teams(payload, _meta(payload))
    absent = [e for e in _team_entries(payload)
              if not any("is_owned_by_current_login" in x for x in e if isinstance(x, dict))]
    assert len(absent) == 9
    assert sum(not t.is_owned_by_me for t in teams) == 9


def _drop_owned_flag(payload):
    for entry in _team_entries(payload):
        entry[:] = [x for x in entry if not (isinstance(x, dict) and "is_owned_by_current_login" in x)]


def test_zero_owned_teams_raises():
    """The case the unique index cannot catch."""
    payload = load_fixture("teams")
    _drop_owned_flag(payload)
    with pytest.raises(UnexpectedPayload, match="0 owned teams"):
        parse_teams(payload, _meta(payload))


def test_two_owned_teams_raises():
    payload = load_fixture("teams")
    entries = _team_entries(payload)
    for entry in entries[:2]:
        entry[:] = [x for x in entry if not (isinstance(x, dict) and "is_owned_by_current_login" in x)]
        entry.append({"is_owned_by_current_login": 1})
    with pytest.raises(UnexpectedPayload, match="2 owned teams"):
        parse_teams(payload, _meta(payload))


def test_team_count_must_match_num_teams():
    payload = load_fixture("teams")
    meta = _meta(payload)
    with pytest.raises(UnexpectedPayload, match="10 teams, league says 12"):
        parse_teams(payload, copy.replace(meta, num_teams=12))


def test_two_managers_raises():
    payload = load_fixture("teams")
    entry = _team_entries(payload)[0]
    managers = next(x for x in entry if isinstance(x, dict) and "managers" in x)["managers"]
    managers.append(copy.deepcopy(managers[0]))
    with pytest.raises(UnexpectedPayload, match="expected one managers entry, found 2"):
        parse_teams(payload, _meta(payload))


def test_teams_do_not_depend_on_metadata_order():
    payload = load_fixture("teams")
    expected = parse_teams(payload, _meta(payload))
    for entry in _team_entries(payload):
        entry.reverse()
    assert parse_teams(payload, _meta(payload)) == expected


def test_a_team_from_another_league_raises():
    payload = load_fixture("teams")
    entry = _team_entries(payload)[0]
    next(x for x in entry if isinstance(x, dict) and "team_key" in x)["team_key"] = "470.l.1.t.1"
    with pytest.raises(UnexpectedPayload, match="is not in this league"):
        parse_teams(payload, _meta(payload))
