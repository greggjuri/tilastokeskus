"""Yahoo Fantasy Sports API client.

Fantasy data provided by Yahoo Fantasy (https://football.fantasysports.yahoo.com/).

Fetches and archives; it does not parse. Every response is written to the raw archive before the
method returns it (D-20), so a parsing bug downstream costs a re-parse, not a re-fetch. Requests
go through ``YahooTransport``: paced, backed off on 999 and 429, loud on auth failure (D-21, D-29).

Implemented for PRP-01: discovery, league settings, teams. The rest raise until their collectors
exist — players, draft picks and rosters in init-02, standings and matchups in phase 7.
"""

from __future__ import annotations

from datetime import datetime

from .archive import RawArchive
from .config import Settings
from .transport import YahooTransport

# Game key and league keys in one response, for any season (D-11). /league/{key} is not used: its
# metadata arrives with every teams and settings payload (PRP-01).
DISCOVERY_PATH = "users;use_login=1/games;game_codes=nfl;seasons={season}/leagues"


class YahooCollectorNotImplemented(NotImplementedError):
    """Raised by every call here until the phase 4 collectors implement it."""

    def __init__(self, operation: str) -> None:
        super().__init__(
            f"{operation} is not implemented yet; the collectors are phase 4. "
            "See docs/TASK.md."
        )


class YahooClient:
    def __init__(self, settings: Settings, transport: YahooTransport,
                 archive: RawArchive) -> None:
        # Not `self.settings`: that name is the /settings method, and an instance attribute would
        # shadow it.
        self.config = settings
        self.transport = transport
        self.archive = archive

    @classmethod
    def open(cls, settings: Settings, started_at: datetime) -> YahooClient:
        """A client with a real transport, archiving under ``settings.raw_dir``."""
        return cls(settings, YahooTransport(settings), RawArchive(settings.raw_dir, started_at))

    def _fetch(self, path: str, resource: str, key: str) -> object:
        payload = self.transport.get(path).json()
        self.archive.write(resource, key, payload)           # before anything parses it
        return payload

    def discover(self, season: int) -> object:
        """The season's game and the user's leagues in it."""
        if isinstance(season, bool) or not isinstance(season, int):
            raise TypeError(f"season must be an int, got {season!r}")
        return self._fetch(DISCOVERY_PATH.format(season=season), "discovery", f"nfl-{season}")

    def settings(self, league_key: str) -> object:
        return self._fetch(f"league/{league_key}/settings", "settings", league_key)

    def teams(self, league_key: str) -> object:
        """Every team in a league, with the league's metadata alongside."""
        return self._fetch(f"league/{league_key}/teams", "teams", league_key)

    def draft_results(self, league_key: str) -> list[dict]:
        raise YahooCollectorNotImplemented("fetching draft results")

    def roster(self, team_key: str, week: int) -> list[dict]:
        raise YahooCollectorNotImplemented("fetching a roster")

    def standings(self, league_key: str, week: int) -> list[dict]:
        raise YahooCollectorNotImplemented("fetching standings")

    def scoreboard(self, league_key: str, week: int) -> list[dict]:
        raise YahooCollectorNotImplemented("fetching a scoreboard")

    def transactions(self, league_key: str) -> list[dict]:
        raise YahooCollectorNotImplemented("fetching transactions")
