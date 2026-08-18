"""HTTP transport for the Yahoo Fantasy API.

This is the call site the rate limiter was written for (D-45). It exists because
``yahoofantasy``'s own request path cannot be wrapped: ``Context.make_request`` performs the
token refresh and then delegates to ``api.fetch.make_request``, which returns ``resp.text`` and
discards the status entirely. Yahoo signals throttling with status 999 (D-43), so a transport
that cannot see the status cannot be rate limited at all.

The division of labour with ``yahoofantasy``:

* ``yahoofantasy login`` still performs the interactive browser flow and writes ``.yahoofantasy``.
  That part is genuinely awkward — a local HTTPS server with a self-signed certificate — and
  there is no reason to reimplement it.
* Everything after that is ours: reading the refresh token, exchanging it for access tokens, and
  issuing requests, so that every response arrives with its status intact.

Nothing here calls a private attribute of the library. The refresh exchange is an ordinary OAuth2
``refresh_token`` grant.
"""

from __future__ import annotations

import pickle
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import requests

from .config import Settings
from .ratelimit import (
    AuthenticationFailed,
    BackoffPolicy,
    Pacer,
    RetryLog,
    call_with_backoff,
)

API_ROOT = "https://fantasysports.yahooapis.com/fantasy/v2"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"

# Refresh slightly early rather than at the instant of expiry, so a token cannot lapse
# mid-request and turn a working run into a spurious auth failure.
EXPIRY_SKEW_SECONDS = 60.0


class TokenUnavailable(RuntimeError):
    """No refresh token could be found. Deliberately loud and specific (D-29, D-38)."""


class Session(Protocol):
    """The slice of requests.Session this module uses. Injectable so the transport can be
    tested against the contract without reaching the network (D-44)."""

    def get(self, url: str, **kwargs: Any) -> Any: ...
    def post(self, url: str, **kwargs: Any) -> Any: ...


@dataclass
class AccessToken:
    value: str
    expires_at: float

    def is_valid(self, now: float, skew: float = EXPIRY_SKEW_SECONDS) -> bool:
        return bool(self.value) and now < self.expires_at - skew


def read_persisted_refresh_token(path: Path | None = None) -> str | None:
    """Read the refresh token written by ``yahoofantasy login``.

    The library persists to ``.yahoofantasy`` in the working directory as a pickle. Only ever
    point this at that file: unpickling is equivalent to executing whatever the file contains,
    so it is not a general-purpose reader.
    """
    path = path or Path(".yahoofantasy")
    if not path.is_file():
        return None
    try:
        with path.open("rb") as handle:
            data = pickle.load(handle)
    except (pickle.UnpicklingError, EOFError, AttributeError, ImportError):
        return None
    if not isinstance(data, dict):
        return None
    auth = data.get("auth")
    return auth.get("refresh_token") if isinstance(auth, dict) else None


def resolve_refresh_token(settings: Settings, path: Path | None = None) -> str:
    """Environment first, then the file written by ``yahoofantasy login``."""
    token = settings.yahoo_refresh_token or read_persisted_refresh_token(path)
    if not token:
        raise TokenUnavailable(
            "no Yahoo refresh token found. Set YAHOO_REFRESH_TOKEN in .env, or run "
            "'yahoofantasy login' in this directory to create .yahoofantasy. See TASKS.md phase 1."
        )
    return token


class YahooTransport:
    """Issues Yahoo Fantasy API requests, rate limited and paced.

    Every request goes through ``call_with_backoff``, so a 999 or 429 is retried within the
    policy's bounds and any other error status raises rather than returning (D-42, D-44).
    """

    def __init__(
        self,
        settings: Settings,
        *,
        policy: BackoffPolicy | None = None,
        session: Session | None = None,
        refresh_token: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self.policy = policy or BackoffPolicy()
        self.session = session or requests.Session()
        self._refresh_token = refresh_token
        self._sleep = sleep
        self._now = now
        self._token: AccessToken | None = None
        self.pacer = Pacer(self.policy.request_interval, sleep=sleep, monotonic=monotonic)
        self.retry_log = RetryLog()

    @property
    def refresh_token(self) -> str:
        if self._refresh_token is None:
            self._refresh_token = resolve_refresh_token(self.settings)
        return self._refresh_token

    def access_token(self) -> str:
        """The current access token, refreshing it when absent or close to expiry."""
        if self._token is None or not self._token.is_valid(self._now()):
            self._token = self._refresh_access_token()
        return self._token.value

    def _refresh_access_token(self) -> AccessToken:
        """Exchange the refresh token for an access token.

        A rejected refresh means the grant is gone, so this raises rather than retrying it into
        something that resembles a throttle (D-29).
        """
        response = self.session.post(
            TOKEN_URL,
            data={
                "client_id": self.settings.yahoo_client_id,
                "client_secret": self.settings.yahoo_client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            },
        )

        if response.status_code != 200:
            raise AuthenticationFailed(response.status_code, getattr(response, "text", None))

        body = response.json()
        token = body.get("access_token")
        if not token:
            raise AuthenticationFailed(200, "token response contained no access_token")

        # Yahoo issues a new refresh token on each exchange; keep it or the next refresh fails.
        if body.get("refresh_token"):
            self._refresh_token = body["refresh_token"]

        return AccessToken(value=token, expires_at=self._now() + float(body.get("expires_in", 3600)))

    def get(self, path: str, **params: Any) -> Any:
        """GET a Fantasy API path, returning the response object on success.

        Raises RateLimitExhausted, AuthenticationFailed, or RequestFailed — never an error
        response dressed as data.
        """
        url = f"{API_ROOT}/{path.lstrip('/')}"

        def attempt() -> tuple[int, Any]:
            response = self.session.get(
                url,
                headers={
                    "Authorization": f"Bearer {self.access_token()}",
                    "Accept": "application/json",
                },
                params={"format": "json", **params},
            )
            return response.status_code, response

        return call_with_backoff(
            attempt,
            self.policy,
            sleep=self._sleep,
            headers_of=lambda response: getattr(response, "headers", None),
            log=self.retry_log,
            pacer=self.pacer,
        )
