"""The draft parser, against the redacted IDP draft. Hazards are made by editing the loaded
fixture, never by writing a payload by hand."""

import dataclasses

import pytest
from conftest import load_fixture

from tilastokeskus.parse import UnexpectedPayload, parse_league_meta
from tilastokeskus.parse_draft import parse_draft

GAME_KEY = "470"


def league():
    """The IDP league's metadata, from its own settings fixture — same fake key as the draft."""
    payload = load_fixture("settings_invite_permission")
    return parse_league_meta(payload, payload["fantasy_content"]["league"][0]["league_key"])


def draft():
    return load_fixture("draft_idp")


def entries(payload):
    league_part = payload["fantasy_content"]["league"]
    block = next(e["draft_results"] for e in league_part if "draft_results" in e)
    return block, [block[k]["draft_result"] for k in sorted(
        (k for k in block if k.isdigit()), key=int)]


def player_meta(pick):
    """The nested player's metadata list — edited in place by the hazard tests."""
    return pick["0"]["players"]["0"]["player"][0]


def set_field(pick, field, value):
    for item in player_meta(pick):
        if isinstance(item, dict) and field in item:
            item[field] = value
            return
    raise AssertionError(f"{field} not in fixture")


def parse(payload=None, meta=None):
    return parse_draft(payload or draft(), meta or league(), GAME_KEY)


# ---- the fixture parses ---------------------------------------------------------------------

def test_parses_190_picks_and_190_players():
    players, picks = parse()
    assert len(picks) == 190 and len(players) == 190
    assert {p.player_key for p in players} == {p.player_key for p in picks}
    assert max(p.round for p in picks) == 19


def test_defensive_players_are_kept_with_real_positions():
    players, _ = parse()
    multi = [p for p in players if "," in p.position]
    assert {p.position for p in multi} == {"WR,CB", "DB,CB", "DT,DE", "DL,DE"}
    assert all(p.nfl_positions == p.position.split(",") for p in players)
    assert any(p.position in {"LB", "S", "CB"} for p in players)


def test_slot_names_never_reach_nfl_positions():
    """W/R/T, Q/W/R/T, IR and D are league slots, not positions (migration 002)."""
    players, _ = parse()
    slots = {"W/R/T", "Q/W/R/T", "IR", "D", "BN"}
    assert not any(slots & set(p.nfl_positions) for p in players)


def test_string_numerics_are_coerced():
    players, _ = parse()
    assert all(isinstance(p.player_id, int) and isinstance(p.bye_week, int) for p in players)
    assert all(p.player_key.endswith(f".p.{p.player_id}") for p in players)


def test_the_linked_player_is_not_collected():
    """The two-way player's defender identity rides inside his metadata; only the drafted one counts."""
    players, picks = parse()
    assert len(players) == len(picks) == 190


def test_metadata_order_does_not_matter():
    expected = parse()
    payload = draft()
    for pick in entries(payload)[1]:
        player_meta(pick).reverse()
    assert parse(payload) == expected


# ---- unobserved values raise (D-38) ----------------------------------------------------------

@pytest.mark.parametrize("field, value, message", [
    ("primary_position", "IDP", "primary_position 'IDP' is unobserved"),
    ("position_type", "X", "position_type 'X' is unobserved"),
    ("display_position", "LB,OLB", r"unobserved position\(s\) \['OLB'\]"),
    ("player_id", "12", "disagrees with its key"),
    ("bye_weeks", {"week": ""}, "bye_weeks.week is ''"),
])
def test_unobserved_player_values_raise(field, value, message):
    payload = draft()
    set_field(entries(payload)[1][0], field, value)
    with pytest.raises(UnexpectedPayload, match=message):
        parse(payload)


def test_a_cost_field_means_an_auction_and_raises():
    payload = draft()
    entries(payload)[1][5]["cost"] = 12
    with pytest.raises(UnexpectedPayload, match=r"unobserved field\(s\) \['cost'\]"):
        parse(payload)


# ---- structure: the draft checked against itself --------------------------------------------

def test_a_pick_carrying_another_player_raises():
    payload = draft()
    _, picks = entries(payload)
    picks[0]["player_key"] = picks[1]["player_key"]
    with pytest.raises(UnexpectedPayload, match="names .* but carries"):
        parse(payload)


def test_a_round_short_of_num_teams_raises():
    payload = draft()
    block, _ = entries(payload)
    last = max(int(k) for k in block if k.isdigit())
    del block[str(last)]
    block["count"] -= 1
    with pytest.raises(UnexpectedPayload, match="rounds without exactly 10 picks"):
        parse(payload)


def test_num_teams_disagreeing_with_the_draft_raises():
    meta = dataclasses.replace(league(), num_teams=12)
    with pytest.raises(UnexpectedPayload, match="rounds without exactly 12 picks"):
        parse(meta=meta)


def test_pick_numbers_must_be_contiguous():
    payload = draft()
    entries(payload)[1][0]["pick"] = 999
    with pytest.raises(UnexpectedPayload, match="pick numbers are not 1..190"):
        parse(payload)


def test_a_team_outside_the_league_raises():
    payload = draft()
    entries(payload)[1][0]["team_key"] = "470.l.1.t.1"
    with pytest.raises(UnexpectedPayload, match="is not in this league"):
        parse(payload)


def test_a_player_key_without_the_game_key_raises():
    payload = draft()
    with pytest.raises(UnexpectedPayload, match="lacks the game key 461"):
        parse_draft(payload, league(), "461")


def test_the_same_player_drafted_twice_raises():
    payload = draft()
    _, picks = entries(payload)
    picks[1]["player_key"] = picks[0]["player_key"]
    picks[1]["0"] = picks[0]["0"]
    with pytest.raises(UnexpectedPayload, match="drafted twice"):
        parse(payload)


def test_an_empty_draft_raises_rather_than_returning_nothing():
    payload = draft()
    block, _ = entries(payload)
    for k in [k for k in block if k.isdigit()]:
        del block[k]
    block["count"] = 0
    with pytest.raises(UnexpectedPayload, match="no picks"):
        parse(payload)


def test_a_payload_for_another_league_raises():
    with pytest.raises(UnexpectedPayload, match="payload is for"):
        parse(meta=dataclasses.replace(league(), league_key="470.l.999999"))
