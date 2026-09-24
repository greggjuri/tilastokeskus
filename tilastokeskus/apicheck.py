"""Daily probe of whether Yahoo Fantasy API access has become live.

Access was approved and the agreement signed, but every application on the account is refused at
the API with a 403 (see `TASKS.md`). That is a Yahoo-side state which can change without anyone
being told, so this checks once a day and reports the answer to Discord whether it changed or not.

Three properties this module is built around, all of them deliberate:

* **It writes nothing.** No rows, no raw archive, no `collector_runs` entry. It does not import
  the database module at all, so "no writes" is a property of the code rather than a promise.
  D-47's reasoning is not what makes this safe — a probe that persisted results would be
  collection, and this is not collection.
* **It posts unconditionally.** A heartbeat that only speaks up on change is indistinguishable
  from a heartbeat that has silently stopped running, which is the failure D-23 is about. An
  unchanged 403 every day is the message.
* **It always exits 0.** A failing oneshot leaves a user unit in `failed` state, and the point of
  this is to keep firing until the answer changes. Failure is reported in the Discord message and
  in the journal, never as an exit status.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import requests

from .config import Settings

# The cheapest resource the Fantasy API offers, and the one that has been refused. It needs no
# league, no season, and no user context, so a 200 here means access, not a lucky query.
PROBE_PATH = "https://fantasysports.yahooapis.com/fantasy/v2/game/nfl"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"

REQUEST_TIMEOUT = 30


@dataclass(frozen=True)
class CheckResult:
    """One probe. `status` is None when the request never got far enough to have one."""

    checked_at: datetime
    status: int | None
    ok: bool
    detail: str

    def message(self) -> str:
        """The Discord message. Timestamp and status appear in every one of them."""
        stamp = self.checked_at.strftime("%Y-%m-%d %H:%M:%S %z")
        code = self.status if self.status is not None else "no response"

        if self.ok:
            return (
                f"✅ **Yahoo Fantasy API access is LIVE** — `{code}`\n"
                f"{stamp}\n"
                f"{self.detail}\n"
                f"Next: run the phase 3 spike. This check can be turned off."
            )
        return f"⏳ Yahoo Fantasy API still blocked — `{code}`\n{stamp}\n{self.detail}"


def _access_token(settings: Settings, session: requests.Session) -> str:
    response = session.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": settings.yahoo_refresh_token,
            "redirect_uri": settings.yahoo_redirect_uri,
        },
        auth=(settings.yahoo_client_id, settings.yahoo_client_secret),
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"token refresh returned {response.status_code}")
    token = response.json().get("access_token")
    if not token:
        raise RuntimeError("token refresh returned no access_token")
    return token


def check(settings: Settings, *, session: requests.Session | None = None,
          now: datetime | None = None) -> CheckResult:
    """Probe the API once. Never raises — every failure becomes a CheckResult."""
    session = session or requests.Session()
    checked_at = now or datetime.now().astimezone()

    if not settings.yahoo_refresh_token:
        return CheckResult(checked_at, None, False,
                           "No refresh token configured; cannot probe.")

    try:
        token = _access_token(settings, session)
    except Exception as exc:  # noqa: BLE001 - a probe reports failures, it does not raise them
        return CheckResult(checked_at, None, False, f"Auth failed before the probe: {exc}")

    try:
        response = session.get(
            PROBE_PATH,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            params={"format": "json"},
            timeout=REQUEST_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult(checked_at, None, False, f"Request failed: {exc}")

    status = response.status_code
    if status == 200:
        return CheckResult(checked_at, status, True, "`/fantasy/v2/game/nfl` returned 200.")
    if status == 403:
        return CheckResult(checked_at, status, False,
                           "Application still not authorized. Token refresh succeeded, so this "
                           "remains a Yahoo-side access state.")
    if status == 401:
        return CheckResult(checked_at, status, False,
                           "Token rejected — the refresh token may have been revoked. This is a "
                           "different failure from the standing 403 and needs looking at.")
    return CheckResult(checked_at, status, False, f"Unexpected status {status}.")


def post_to_discord(webhook_url: str, content: str, *,
                    session: requests.Session | None = None) -> tuple[bool, str]:
    """Post the message. Returns (delivered, detail); never raises.

    The webhook URL is a credential — anyone holding it can post to the channel — so it never
    appears in a return value, an exception, or a log line here.
    """
    session = session or requests.Session()
    try:
        response = session.post(webhook_url, json={"content": content}, timeout=REQUEST_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        return False, f"webhook request failed: {type(exc).__name__}"
    if response.status_code >= 400:
        return False, f"webhook returned {response.status_code}"
    return True, f"webhook returned {response.status_code}"
