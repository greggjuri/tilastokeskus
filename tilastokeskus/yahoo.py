"""Yahoo Fantasy Sports API client.

Fantasy data provided by Yahoo Fantasy (https://football.fantasysports.yahoo.com/).

Not yet implemented: access is live (docs/DECISIONS.md D-25), and the collectors that will use
this client are phase 4 (docs/TASK.md). This module is a placeholder so that the CLI and collector
can be built and their argument handling exercised in the meantime.

When it is written it must:
  * refresh access tokens transparently and fail loudly on revocation      (D-29)
  * back off exponentially on 999 and 429 responses                        (D-21)
  * write every raw response to disk, gzipped and dated, before parsing    (D-20)
  * preserve Yahoo keys verbatim, including the game-id prefix             (D-11)
"""

from __future__ import annotations

from .config import Settings


class YahooAccessPending(NotImplementedError):
    """Raised by every call here until the phase 4 collectors implement it."""

    def __init__(self, operation: str) -> None:
        super().__init__(
            f"{operation} is not implemented yet; the collectors are phase 4. "
            "See docs/TASK.md."
        )


class YahooClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def league_keys(self, season: int) -> list[str]:
        raise YahooAccessPending("listing league keys")

    def league(self, league_key: str) -> dict:
        raise YahooAccessPending("fetching a league")

    def teams(self, league_key: str) -> list[dict]:
        raise YahooAccessPending("fetching teams")

    def draft_results(self, league_key: str) -> list[dict]:
        raise YahooAccessPending("fetching draft results")

    def roster(self, team_key: str, week: int) -> list[dict]:
        raise YahooAccessPending("fetching a roster")

    def standings(self, league_key: str, week: int) -> list[dict]:
        raise YahooAccessPending("fetching standings")

    def scoreboard(self, league_key: str, week: int) -> list[dict]:
        raise YahooAccessPending("fetching a scoreboard")

    def transactions(self, league_key: str) -> list[dict]:
        raise YahooAccessPending("fetching transactions")
