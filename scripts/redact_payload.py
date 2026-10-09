"""Turn archived Yahoo payloads into committable test fixtures.

    python scripts/redact_payload.py OUT_DIR NAME=raw/…/file.json.gz [NAME=…]

Structure is preserved exactly — every key, list length, nesting level and scalar type, so a
fixture carries the same hazards as the payload it came from: a numeric string stays a numeric
string, an int stays an int, an absent field stays absent.

Values are replaced unless the field is on an allowlist of enumerations and structural integers.
Unknown fields are redacted by default. League ids inside keys are rewritten to fake ids, the same
fake id for the same real league across every file in one invocation; the game key prefix and team
numbers are kept, since key format is what the parsers check (D-11).

Run locally. Only the output is ever read in an AI tool (D-49). Before writing, the script checks
that no redacted string value survives anywhere in its output, and refuses to write if one does.
"""

from __future__ import annotations

import gzip
import json
import re
import sys
from pathlib import Path

# Kept verbatim: enumerations, flags and structural integers. Nothing here identifies a person,
# a team or a league.
KEEP = {
    "game_key", "game_id", "code", "type", "season", "is_registration_over", "is_game_over",
    "is_offseason", "is_live_draft_lobby_active", "draft_status", "num_teams", "max_teams",
    "edit_key", "weekly_deadline", "scoring_type", "league_type", "felo_tier", "is_highscore",
    "is_guillotine", "matchup_week", "allow_add_to_dl_extra_pos", "is_pro_league", "is_cash_league",
    "current_week", "start_week", "start_date", "end_week", "end_date", "is_finished",
    "is_plus_league", "game_code", "count", "draft_type", "is_auction_draft", "uses_playoff",
    "has_playoff_consolation_games", "playoff_start_week", "uses_playoff_reseeding",
    "uses_lock_eliminated_teams", "num_playoff_teams", "num_playoff_consolation_teams",
    "has_multiweek_championship", "waiver_type", "waiver_rule", "uses_faab", "post_draft_players",
    "trade_ratify_type", "player_pool", "cant_cut_list", "draft_together", "uses_median_score",
    "pickem_enabled", "uses_fractional_points", "uses_negative_points", "position",
    "position_type", "is_starting_position", "stat_id", "enabled", "sort_order", "value",
    "is_owned_by_current_login", "waiver_priority", "number_of_moves", "number_of_trades",
    "coverage_type", "coverage_value", "league_scoring_type", "draft_position", "has_draft_grade",
    "draft_grade", "is_current_login", "is_commissioner", "previous_season_team_rank",
    "invite_permission", "size", "team_id", "is_disabled", "roster_type",
    # Draft and player structure (PRP-02). NFL team abbreviations and positions are public
    # enumerations, not anything about a league or its members.
    "pick", "round", "primary_position", "display_position", "editorial_team_abbr",
}

KEY_RE = re.compile(r"^(\d+)\.l\.(\d+)((?:\.[a-z]+\.\d+)*)$")
PLAYER_KEY_RE = re.compile(r"^(\d+)\.p\.(\d+)$")


class Redactor:
    def __init__(self) -> None:
        self.league_ids: dict[str, str] = {}
        self.player_ids: dict[str, str] = {}
        self.removed: dict[str, str] = {}      # redacted value -> field it came from

    def fake_league_id(self, real: str) -> str:
        return self.league_ids.setdefault(real, str(100001 + len(self.league_ids)))

    def fake_player_id(self, real: str) -> str:
        # One map for keys and ids alike, so a fixture's player_key suffix still equals its
        # player_id — a check the parser makes.
        return self.player_ids.setdefault(real, str(900001 + len(self.player_ids)))

    def scalar(self, field: str | None, value: object) -> object:
        if isinstance(value, str):
            key = KEY_RE.match(value)
            if key:
                return f"{key[1]}.l.{self.fake_league_id(key[2])}{key[3]}"
            player = PLAYER_KEY_RE.match(value)
            if player:
                return f"{player[1]}.p.{self.fake_player_id(player[2])}"
        if field == "league_id" and isinstance(value, str):
            return self.fake_league_id(value)
        if field == "player_id" and isinstance(value, str):
            return self.fake_player_id(value)
        if field in KEEP or value is None or isinstance(value, bool):
            return value
        if isinstance(value, int):
            return 1
        if isinstance(value, float):
            return 1.0
        if isinstance(value, str):
            if len(value) >= 4:
                self.removed.setdefault(value, field or "?")
            if value == "":
                return ""
            if re.fullmatch(r"-?\d+", value):
                return "1"
            if re.fullmatch(r"-?\d+\.\d+", value):
                return "1.0"
            if value.startswith(("http://", "https://")):
                return "https://example.invalid/redacted"
            return "redacted"
        return value

    def walk(self, node: object, field: str | None = None) -> object:
        if isinstance(node, dict):
            return {k: self.walk(v, k) for k, v in node.items()}
        if isinstance(node, list):
            return [self.walk(v, field) for v in node]
        return self.scalar(field, node)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    out_dir = Path(argv[0])
    redactor = Redactor()
    outputs = {}
    for spec in argv[1:]:
        name, _, source = spec.partition("=")
        if not (name and source):
            print(f"expected NAME=PATH, got {spec!r}", file=sys.stderr)
            return 2
        with gzip.open(source) as fh:
            payload = json.load(fh)
        outputs[name] = json.dumps(redactor.walk(payload), indent=1, sort_keys=False) + "\n"

    leaked = {redactor.removed[v] for v in redactor.removed for t in outputs.values() if v in t}
    if leaked:
        # Field names only: printing the surviving value would be the leak this check prevents.
        print(f"refusing to write: a redacted value survives in the output, from field(s) "
              f"{sorted(leaked)}", file=sys.stderr)
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    for name, text in outputs.items():
        (out_dir / f"{name}.json").write_text(text)
        print(f"wrote {out_dir / name}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
