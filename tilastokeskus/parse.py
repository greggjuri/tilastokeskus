"""Parsers for the Yahoo payloads collected by PRP-01: discovery, league settings, teams.

Pure functions over decoded JSON; no I/O. Designed against archived payloads, not documentation
(D-33): ``raw/2026-10-07/spike/``, ``raw/2026-10-08/{teams,settings,discovery}/``.

Rules that hold throughout, each one a hazard the archive showed:

* Every field is found by name. Yahoo's metadata arrives as lists of single-key dicts with empty
  placeholders, and their order is not something to rely on.
* Numbers are coerced explicitly. The same field is an int in one record and a string in the next,
  and ``current_week`` changes type between seasons.
* Flags are compared, never truth-tested: they arrive as ``'0'`` and ``'1'``, and ``bool('0')`` is
  ``True``.
* An absent flag is not a ``0``. ``is_owned_by_current_login`` and ``is_finished`` are omitted
  rather than sent false.
* Anything unobserved raises ``UnexpectedPayload`` naming the field and the league. Points
  scoring, auction drafts and FAAB are absent from all fifteen leagues (D-33, 2026-10-08), so they
  raise rather than branch (D-38).
"""

from __future__ import annotations

from dataclasses import dataclass

# Values confirmed across all fifteen leagues on 2026-10-08. Anything else is a bug report.
OBSERVED_SCORING_TYPES = {"head"}
OBSERVED_DRAFT_TYPES = {"live"}


class UnexpectedPayload(RuntimeError):
    """A payload differs from every shape or value observed. Names the field and the league."""


@dataclass(frozen=True)
class Discovery:
    game_key: str
    season: int
    league_keys: list[str]


@dataclass(frozen=True)
class LeagueMeta:
    league_key: str
    season: int
    name: str
    num_teams: int
    scoring_type: str
    current_week: int
    start_week: int
    end_week: int
    is_finished: bool


@dataclass(frozen=True)
class LeagueSettings:
    draft_type: str
    is_auction_draft: bool
    playoff_start_week: int


@dataclass(frozen=True)
class Team:
    team_key: str
    league_key: str
    team_id: int
    name: str
    manager_name: str
    is_owned_by_me: bool
    logo_url: str
    draft_position: int
    draft_grade: str | None
    has_draft_grade: bool
    draft_recap_url: str | None


# ---- primitives -----------------------------------------------------------------------------

def flatten(entries: object, where: str = "?") -> dict:
    """Merge Yahoo's list of single-key dicts into one dict. Empty-list placeholders are skipped.

    A key appearing twice with different values raises: merging would silently keep one.
    """
    if not isinstance(entries, list):
        raise UnexpectedPayload(f"{where}: expected a list of metadata entries, "
                                f"got {type(entries).__name__}")
    merged: dict = {}
    for entry in entries:
        if entry == []:
            continue
        if not isinstance(entry, dict):
            raise UnexpectedPayload(f"{where}: metadata entry is {type(entry).__name__}, "
                                    "not an object")
        for key, value in entry.items():
            if key in merged and merged[key] != value:
                raise UnexpectedPayload(f"{where}: field {key!r} appears twice with different "
                                        "values")
            merged[key] = value
    return merged


def as_int(value: object, field: str, where: str = "?") -> int:
    """An int, or a string of digits. Nothing else — not a bool, not a float, not ''."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lstrip("-").isdigit() and value.strip() == value:
        return int(value)
    raise UnexpectedPayload(f"{where}: {field} is {value!r}, not an integer")


def as_flag(value: object, field: str, where: str = "?") -> bool:
    """Yahoo's yes/no: '0', '1', 0 or 1. Compared, never truth-tested."""
    if isinstance(value, bool):
        raise UnexpectedPayload(f"{where}: {field} is a JSON boolean, not Yahoo's 0/1")
    if value in ("1", 1):
        return True
    if value in ("0", 0):
        return False
    raise UnexpectedPayload(f"{where}: {field} is {value!r}, not a 0/1 flag")


def as_str(value: object, field: str, where: str = "?") -> str:
    if not isinstance(value, str) or not value:
        # The value itself is not echoed: these are free-text fields.
        raise UnexpectedPayload(f"{where}: {field} is missing or not a non-empty string "
                                f"({type(value).__name__})")
    return value


def require(mapping: dict, field: str, where: str) -> object:
    if field not in mapping:
        raise UnexpectedPayload(f"{where}: required field {field!r} is absent")
    return mapping[field]


def numbered(block: object, item: str, where: str) -> list:
    """Yahoo's ``{count, "0": {item: ...}, "1": ...}`` collections, in index order.

    ``count`` must agree with the entries: a mismatch means the payload is not what we think.
    """
    if not isinstance(block, dict):
        raise UnexpectedPayload(f"{where}: expected a collection object")
    indices = sorted((k for k in block if k.isdigit()), key=int)
    if indices != [str(i) for i in range(len(indices))]:
        raise UnexpectedPayload(f"{where}: collection indices are not 0..n-1")
    count = as_int(require(block, "count", where), "count", where)
    if count != len(indices):
        raise UnexpectedPayload(f"{where}: count says {count}, found {len(indices)} entries")
    return [require(block[i], item, f"{where}[{i}]") for i in indices]


def part(elements: object, key: str, where: str) -> object:
    """The element of a Yahoo resource list that carries ``key`` — never by position."""
    if not isinstance(elements, list):
        raise UnexpectedPayload(f"{where}: expected a list")
    found = [e[key] for e in elements if isinstance(e, dict) and key in e]
    if len(found) != 1:
        raise UnexpectedPayload(f"{where}: expected exactly one {key!r}, found {len(found)}")
    return found[0]


def league_resource(payload: object, where: str) -> list:
    try:
        league = payload["fantasy_content"]["league"]
    except (KeyError, TypeError) as exc:
        raise UnexpectedPayload(f"{where}: no fantasy_content.league") from exc
    if not isinstance(league, list):
        raise UnexpectedPayload(f"{where}: league is not a list")
    return league


# ---- resources ------------------------------------------------------------------------------

def parse_discovery(payload: object, season: int) -> Discovery:
    """Game key and league keys from ``/users;use_login=1/games;…;seasons={season}/leagues``."""
    where = f"discovery {season}"
    try:
        users = payload["fantasy_content"]["users"]
    except (KeyError, TypeError) as exc:
        raise UnexpectedPayload(f"{where}: no fantasy_content.users") from exc
    user_list = numbered(users, "user", f"{where} users")
    if len(user_list) != 1:
        raise UnexpectedPayload(f"{where}: expected one user, found {len(user_list)}")

    games = numbered(part(user_list[0], "games", f"{where} user"), "game", f"{where} games")
    if len(games) != 1:
        # Zero means no NFL game for this season on the account: loud, never an empty run.
        raise UnexpectedPayload(f"{where}: expected one nfl game, found {len(games)}")
    game = games[0]
    game_meta = next((e for e in game if isinstance(e, dict) and "game_key" in e), None)
    if game_meta is None:
        raise UnexpectedPayload(f"{where}: game has no game_key")
    game_key = str(as_int(game_meta["game_key"], "game_key", where))
    game_season = as_int(require(game_meta, "season", where), "season", where)
    if game_season != season:
        raise UnexpectedPayload(f"{where}: asked for season {season}, got {game_season}")

    leagues = numbered(part(game, "leagues", f"{where} game"), "league", f"{where} leagues")
    keys = []
    for league in leagues:
        meta = flatten(league, where)
        key = as_str(require(meta, "league_key", where), "league_key", where)
        if not key.startswith(f"{game_key}.l."):
            raise UnexpectedPayload(f"{where}: league key {key} lacks the game key {game_key} "
                                    "prefix (D-11)")
        keys.append(key)
    if not keys:
        raise UnexpectedPayload(f"{where}: no leagues")
    if len(set(keys)) != len(keys):
        raise UnexpectedPayload(f"{where}: duplicate league keys")
    return Discovery(game_key=game_key, season=season, league_keys=keys)


def parse_league_meta(payload: object, league_key: str) -> LeagueMeta:
    """League metadata, element ``league_key`` of a teams or settings payload."""
    where = f"league {league_key}"
    meta = next((e for e in league_resource(payload, where)
                 if isinstance(e, dict) and "league_key" in e), None)
    if meta is None:
        raise UnexpectedPayload(f"{where}: no league metadata")
    if meta["league_key"] != league_key:
        raise UnexpectedPayload(f"{where}: payload is for {meta['league_key']}")

    scoring_type = as_str(require(meta, "scoring_type", where), "scoring_type", where)
    if scoring_type not in OBSERVED_SCORING_TYPES:
        raise UnexpectedPayload(f"{where}: scoring_type {scoring_type!r} is unobserved; only "
                                f"{sorted(OBSERVED_SCORING_TYPES)} is handled (D-38)")

    def integer(field: str) -> int:
        return as_int(require(meta, field, where), field, where)

    return LeagueMeta(
        league_key=league_key,
        season=integer("season"),
        name=as_str(require(meta, "name", where), "name", where),
        num_teams=integer("num_teams"),
        scoring_type=scoring_type,
        current_week=integer("current_week"),
        start_week=integer("start_week"),
        end_week=integer("end_week"),
        # Absent while the season runs (all fifteen 2026 leagues), 1 once it is over (all six
        # 2025 leagues). Absent is not finished.
        is_finished=as_flag(meta["is_finished"], "is_finished", where)
        if "is_finished" in meta else False,
    )


def parse_settings(payload: object, league_key: str) -> LeagueSettings:
    """The columns ``leagues`` takes from ``/league/{key}/settings``."""
    where = f"settings {league_key}"
    settings = flatten(part(league_resource(payload, where), "settings", where), where)

    draft_type = as_str(require(settings, "draft_type", where), "draft_type", where)
    if draft_type not in OBSERVED_DRAFT_TYPES:
        raise UnexpectedPayload(f"{where}: draft_type {draft_type!r} is unobserved (D-38)")
    if as_flag(require(settings, "is_auction_draft", where), "is_auction_draft", where):
        raise UnexpectedPayload(f"{where}: auction draft — unobserved in all fifteen leagues, "
                                "not handled (D-38)")
    if as_flag(require(settings, "uses_faab", where), "uses_faab", where):
        raise UnexpectedPayload(f"{where}: FAAB — unobserved in all fifteen leagues, "
                                "not handled (D-38)")

    return LeagueSettings(
        draft_type=draft_type,
        is_auction_draft=False,
        playoff_start_week=as_int(require(settings, "playoff_start_week", where),
                                  "playoff_start_week", where),
    )


def _single(items: object, field: str, inner: str, where: str) -> dict:
    """Exactly one entry of a list like ``managers`` or ``team_logos``; more is unobserved."""
    if not isinstance(items, list) or len(items) != 1:
        n = len(items) if isinstance(items, list) else type(items).__name__
        raise UnexpectedPayload(f"{where}: expected one {field} entry, found {n}")
    return require(items[0], inner, where)


def parse_teams(payload: object, league: LeagueMeta) -> list[Team]:
    """Every team in a league. Exactly ``num_teams`` of them, exactly one owned."""
    where = f"teams {league.league_key}"
    entries = numbered(part(league_resource(payload, where), "teams", where), "team", where)
    if len(entries) != league.num_teams:
        raise UnexpectedPayload(f"{where}: {len(entries)} teams, league says {league.num_teams}")

    teams = []
    for entry in entries:
        meta = flatten(part_list(entry, where), where)
        key = as_str(require(meta, "team_key", where), "team_key", where)
        if not key.startswith(f"{league.league_key}.t."):
            raise UnexpectedPayload(f"{where}: team key {key} is not in this league")
        team_where = f"team {key}"
        team_id = as_int(require(meta, "team_id", team_where), "team_id", team_where)
        if key != f"{league.league_key}.t.{team_id}":
            raise UnexpectedPayload(f"{team_where}: team_id {team_id} disagrees with its key")

        manager = _single(require(meta, "managers", team_where), "managers", "manager",
                          team_where)
        logo = _single(require(meta, "team_logos", team_where), "team_logos", "team_logo",
                       team_where)
        has_grade = as_flag(require(meta, "has_draft_grade", team_where), "has_draft_grade",
                            team_where)
        teams.append(Team(
            team_key=key,
            league_key=league.league_key,
            team_id=team_id,
            name=as_str(require(meta, "name", team_where), "name", team_where),
            manager_name=as_str(require(manager, "nickname", team_where), "nickname",
                                team_where),
            # Present only on the owned team; absent — not 0 — on the rest.
            is_owned_by_me=as_flag(meta["is_owned_by_current_login"],
                                   "is_owned_by_current_login", team_where)
            if "is_owned_by_current_login" in meta else False,
            logo_url=as_str(require(logo, "url", team_where), "team_logo.url", team_where),
            draft_position=as_int(require(meta, "draft_position", team_where),
                                  "draft_position", team_where),
            has_draft_grade=has_grade,
            draft_grade=as_str(require(meta, "draft_grade", team_where), "draft_grade",
                               team_where) if has_grade else None,
            draft_recap_url=as_str(require(meta, "draft_recap_url", team_where),
                                   "draft_recap_url", team_where) if has_grade else None,
        ))

    owned = sum(t.is_owned_by_me for t in teams)
    if owned != 1:
        # The unique index catches two; nothing but this catches none (D-33).
        raise UnexpectedPayload(f"{where}: {owned} owned teams; exactly one expected")
    return teams


def part_list(team: object, where: str) -> list:
    """A team is ``[[metadata entries], …]``: the one element that is itself a list."""
    if not isinstance(team, list):
        raise UnexpectedPayload(f"{where}: team is not a list")
    lists = [e for e in team if isinstance(e, list)]
    if len(lists) != 1:
        raise UnexpectedPayload(f"{where}: expected one metadata list in a team, found "
                                f"{len(lists)}")
    return lists[0]
