"""Parser for ``/league/{league_key}/draftresults/players`` — every pick with its player nested.

Designed against the IDP league's archived payload (``raw/2026-10-08/<run>/draftresults-players/``),
the hardest of the fifteen: 190 picks, nine individual defensive players, comma-joined positions,
one two-way player with a linked defender identity. Same rules as ``parse``: fields by name,
explicit coercion, and anything unobserved raises ``UnexpectedPayload`` (D-38).

Only league-independent player fields are kept. ``players`` holds one row per player across every
league (D-12), and the same player's ``eligible_positions`` differed by league in 47 of 50 compared,
so it is not read; ``display_position``, identical in 50 of 50, is the source of both ``position``
and ``nfl_positions`` (migration 002).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .parse import (
    LeagueMeta,
    UnexpectedPayload,
    as_int,
    as_str,
    flatten,
    league_resource,
    numbered,
    part,
    part_list,
    require,
)

# Observed in the IDP league's draft (2026-10-08). Anything else is a bug report, not a branch.
OBSERVED_PRIMARY_POSITIONS = {"QB", "RB", "WR", "TE", "K", "DEF", "D"}
OBSERVED_POSITION_TYPES = {"O", "K", "DT", "DP"}
OBSERVED_DISPLAY_TOKENS = {"QB", "RB", "WR", "TE", "K", "DEF", "LB", "S", "CB", "DT", "DE", "DL",
                           "DB"}
# A pick carries exactly these, plus "0" holding its player. `cost` would mean an auction draft,
# absent from all fifteen leagues (D-33).
PICK_FIELDS = {"pick", "round", "team_key", "player_key"}


@dataclass(frozen=True)
class Player:
    player_key: str
    player_id: int
    full_name: str
    position: str
    nfl_positions: list[str]
    nfl_team: str
    bye_week: int


@dataclass(frozen=True)
class Pick:
    league_key: str
    pick: int
    round: int
    team_key: str
    player_key: str


def parse_draft(payload: object, league: LeagueMeta,
                game_key: str) -> tuple[list[Player], list[Pick]]:
    """Players and picks from one league's draft. Players are unique by key."""
    where = f"draft {league.league_key}"
    resource = league_resource(payload, where)
    meta_key = part(resource, "league_key", where)
    if meta_key != league.league_key:
        raise UnexpectedPayload(f"{where}: payload is for {meta_key}")

    entries = numbered(part(resource, "draft_results", where), "draft_result", where)
    if not entries:
        # Fifteen drafts are known to have happened: none is a failure, not an empty draft.
        raise UnexpectedPayload(f"{where}: no picks")

    players: dict[str, Player] = {}
    picks = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise UnexpectedPayload(f"{where}: draft_result is {type(entry).__name__}")
        unexpected = set(entry) - PICK_FIELDS - {"0"}
        if unexpected:
            raise UnexpectedPayload(f"{where}: pick carries unobserved field(s) "
                                    f"{sorted(unexpected)}")
        pick = Pick(
            league_key=league.league_key,
            pick=as_int(require(entry, "pick", where), "pick", where),
            round=as_int(require(entry, "round", where), "round", where),
            team_key=as_str(require(entry, "team_key", where), "team_key", where),
            player_key=as_str(require(entry, "player_key", where), "player_key", where),
        )
        if not pick.team_key.startswith(f"{league.league_key}.t."):
            raise UnexpectedPayload(f"{where}: pick {pick.pick} team {pick.team_key} is not in "
                                    "this league")
        if not pick.player_key.startswith(f"{game_key}.p."):
            raise UnexpectedPayload(f"{where}: player key {pick.player_key} lacks the game key "
                                    f"{game_key} prefix (D-11)")

        player = _nested_player(entry, pick, where)
        if player.player_key in players:
            raise UnexpectedPayload(f"{where}: {player.player_key} drafted twice")
        players[player.player_key] = player
        picks.append(pick)

    _check_structure(picks, league.num_teams, where)
    return list(players.values()), picks


def _nested_player(entry: dict, pick: Pick, where: str) -> Player:
    """The player under ``"0" → players → "0" → player``; it must be the pick's own."""
    nested = require(require(entry, "0", where), "players", where)
    found = numbered(nested, "player", f"{where} pick {pick.pick}")
    if len(found) != 1:
        raise UnexpectedPayload(f"{where}: pick {pick.pick} carries {len(found)} players")
    meta = flatten(part_list(found[0], where), where)

    player_where = f"{where} player {pick.player_key}"
    key = as_str(require(meta, "player_key", player_where), "player_key", player_where)
    if key != pick.player_key:
        raise UnexpectedPayload(f"{where}: pick {pick.pick} names {pick.player_key} but carries "
                                f"{key}")
    player_id = as_int(require(meta, "player_id", player_where), "player_id", player_where)
    if key != f"{key.split('.p.')[0]}.p.{player_id}":
        raise UnexpectedPayload(f"{player_where}: player_id {player_id} disagrees with its key")

    # Validated though not stored: an unobserved value means the payload is not what we think.
    primary = as_str(require(meta, "primary_position", player_where), "primary_position",
                     player_where)
    if primary not in OBSERVED_PRIMARY_POSITIONS:
        raise UnexpectedPayload(f"{player_where}: primary_position {primary!r} is unobserved")
    position_type = as_str(require(meta, "position_type", player_where), "position_type",
                           player_where)
    if position_type not in OBSERVED_POSITION_TYPES:
        raise UnexpectedPayload(f"{player_where}: position_type {position_type!r} is unobserved")

    display = as_str(require(meta, "display_position", player_where), "display_position",
                     player_where)
    tokens = display.split(",")
    unknown = [t for t in tokens if t not in OBSERVED_DISPLAY_TOKENS]
    if unknown:
        raise UnexpectedPayload(f"{player_where}: display_position {display!r} has unobserved "
                                f"position(s) {unknown}")

    name = require(meta, "name", player_where)
    if not isinstance(name, dict):
        raise UnexpectedPayload(f"{player_where}: name is not an object")
    bye = require(meta, "bye_weeks", player_where)
    if not isinstance(bye, dict):
        raise UnexpectedPayload(f"{player_where}: bye_weeks is not an object")

    return Player(
        player_key=key,
        player_id=player_id,
        full_name=as_str(require(name, "full", player_where), "name.full", player_where),
        position=display,
        nfl_positions=tokens,
        nfl_team=as_str(require(meta, "editorial_team_abbr", player_where),
                        "editorial_team_abbr", player_where),
        bye_week=as_int(require(bye, "week", player_where), "bye_weeks.week", player_where),
    )


def _check_structure(picks: list[Pick], num_teams: int, where: str) -> None:
    """Picks 1..n, rounds 1..R, exactly ``num_teams`` picks in every round.

    Probe-confirmed against roster size in all four drafts observed. Roster size itself is not
    stored, so this is the draft checked against itself (PRP-02 Q2).
    """
    numbers = sorted(p.pick for p in picks)
    if numbers != list(range(1, len(picks) + 1)):
        raise UnexpectedPayload(f"{where}: pick numbers are not 1..{len(picks)}")
    per_round = Counter(p.round for p in picks)
    if sorted(per_round) != list(range(1, len(per_round) + 1)):
        raise UnexpectedPayload(f"{where}: rounds are not contiguous from 1")
    short = {r: n for r, n in per_round.items() if n != num_teams}
    if short:
        raise UnexpectedPayload(f"{where}: rounds without exactly {num_teams} picks: {short}")
