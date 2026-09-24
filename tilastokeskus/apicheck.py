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
class AppResult:
    """One app's probe. `status` is None when the request never got far enough to have one."""

    label: str
    status: int | None
    ok: bool
    detail: str

    def line(self) -> str:
        code = self.status if self.status is not None else "no response"
        mark = "✅" if self.ok else "⏳"
        return f"{mark} **{self.label}** — `{code}` — {self.detail}"


@dataclass(frozen=True)
class CheckResult:
    """A full check: every configured app, probed once.

    Both apps are watched because the access application was submitted under the Public Client
    while the project runs on the Confidential one. Whichever Yahoo activates, one of these lines
    changes -- and watching only one would look identical to nothing having happened (D-55).
    """

    checked_at: datetime
    apps: list[AppResult]

    @property
    def ok(self) -> bool:
        return any(app.ok for app in self.apps)

    @property
    def status(self) -> int | None:
        """The primary app's status, kept so a single-app read of this is still meaningful."""
        return self.apps[0].status if self.apps else None

    def message(self) -> str:
        """The Discord message. A timestamp and every app's status appear in all of them."""
        stamp = self.checked_at.strftime("%Y-%m-%d %H:%M:%S %z")
        lines = "\n".join(app.line() for app in self.apps)

        if self.ok:
            live = ", ".join(app.label for app in self.apps if app.ok)
            return (
                f"✅ **Yahoo Fantasy API access is LIVE** — {live}\n"
                f"{stamp}\n{lines}\n"
                f"Next: run the phase 3 spike. This check can be turned off."
            )
        return f"⏳ Yahoo Fantasy API still blocked\n{stamp}\n{lines}"


def _access_token(settings: Settings, session: requests.Session, *,
                  client_id: str, client_secret: str, refresh_token: str) -> str:
    """Exchange a refresh token. A Public Client has no secret, so auth is omitted for it."""
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "redirect_uri": settings.yahoo_redirect_uri,
    }
    if not client_secret:
        # PKCE public client: the client_id goes in the body and there is nothing to authenticate
        # with. Sending an empty Basic header instead would be rejected.
        data["client_id"] = client_id
    response = session.post(
        TOKEN_URL,
        data=data,
        auth=(client_id, client_secret) if client_secret else None,
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"token refresh returned {response.status_code}")
    token = response.json().get("access_token")
    if not token:
        raise RuntimeError("token refresh returned no access_token")
    return token


def _probe_app(settings: Settings, session: requests.Session, *, label: str,
               client_id: str, client_secret: str, refresh_token: str) -> AppResult:
    """Probe one app. Never raises — every failure becomes an AppResult."""
    if not (client_id and refresh_token):
        return AppResult(label, None, False, "not configured")

    try:
        token = _access_token(settings, session, client_id=client_id,
                              client_secret=client_secret, refresh_token=refresh_token)
    except Exception as exc:  # noqa: BLE001 - a probe reports failures, it does not raise them
        return AppResult(label, None, False, f"auth failed before the probe: {exc}")

    try:
        response = session.get(
            PROBE_PATH,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            params={"format": "json"},
            timeout=REQUEST_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001
        return AppResult(label, None, False, f"request failed: {exc}")

    status = response.status_code
    if status == 200:
        return AppResult(label, status, True, "`/fantasy/v2/game/nfl` returned 200")
    if status == 403:
        return AppResult(label, status, False, "still not authorized")
    if status == 401:
        return AppResult(label, status, False,
                         "token rejected — the refresh token may have been revoked, which is a "
                         "different failure from the standing 403 and needs looking at")
    return AppResult(label, status, False, f"unexpected status {status}")


def check(settings: Settings, *, session: requests.Session | None = None,
          now: datetime | None = None) -> CheckResult:
    """Probe every configured app once. Never raises."""
    session = session or requests.Session()
    checked_at = now or datetime.now().astimezone()

    apps = [
        _probe_app(settings, session, label="Confidential Client",
                   client_id=settings.yahoo_client_id,
                   client_secret=settings.yahoo_client_secret,
                   refresh_token=settings.yahoo_refresh_token),
    ]
    if settings.yahoo_public_client_id or settings.yahoo_public_refresh_token:
        apps.append(_probe_app(settings, session, label="Public Client (applied under)",
                               client_id=settings.yahoo_public_client_id,
                               client_secret="",
                               refresh_token=settings.yahoo_public_refresh_token))
    return CheckResult(checked_at, apps)


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
