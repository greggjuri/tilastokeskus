"""Tests for the Yahoo HTTP transport.

This is the limiter's call site, so these tests are what demonstrate the rate limiting is
actually wired in rather than merely present (D-45). Written contract-first per D-44: what each
method returns on every path, and what it refuses.

Nothing here touches the network — the session is injected.
"""

import pickle

import pytest

from tilastokeskus.config import Settings
from tilastokeskus.ratelimit import (
    AuthenticationFailed,
    BackoffPolicy,
    RateLimitExhausted,
    RequestFailed,
)
from tilastokeskus.transport import (
    API_ROOT,
    AccessToken,
    TokenUnavailable,
    YahooTransport,
    read_persisted_refresh_token,
    resolve_refresh_token,
)

EXACT = BackoffPolicy(base_delay=2.0, multiplier=2.0, max_attempts=4, jitter=0.0,
                      request_interval=0.0)


def settings(**overrides) -> Settings:
    base = {
        "pg_host": "localhost", "pg_port": 5432, "pg_database": "t", "pg_user": "u",
        "pg_password": "p", "season": 2026, "raw_dir": "raw", "yahoo_client_id": "id",
        "yahoo_client_secret": "secret", "yahoo_redirect_uri": "https://localhost:8000",
        "yahoo_refresh_token": "refresh-abc",
    }
    base.update(overrides)
    return Settings(**base)


class FakeResponse:
    def __init__(self, status_code, body=None, headers=None, text=""):
        self.status_code = status_code
        self._body = body or {}
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._body


class FakeSession:
    """Records calls and replays queued responses."""

    def __init__(self, get_responses=None, post_responses=None):
        self.get_responses = list(get_responses or [])
        self.post_responses = list(post_responses or [])
        self.gets = []
        self.posts = []

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        return self.get_responses.pop(0) if self.get_responses else FakeResponse(200, {"ok": 1})

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        if self.post_responses:
            return self.post_responses.pop(0)
        return FakeResponse(200, {"access_token": "tok", "expires_in": 3600})


def transport(session, *, policy=EXACT, now=None, **kw):
    clock = now or (lambda: 1000.0)
    return YahooTransport(
        settings(), policy=policy, session=session, sleep=lambda _s: None,
        monotonic=lambda: 0.0, now=clock, **kw,
    )


# --- token resolution -------------------------------------------------------------------


def test_env_refresh_token_is_preferred():
    assert resolve_refresh_token(settings(yahoo_refresh_token="from-env")) == "from-env"


def test_falls_back_to_the_yahoofantasy_file(tmp_path):
    path = tmp_path / ".yahoofantasy"
    path.write_bytes(pickle.dumps({"auth": {"refresh_token": "from-file"}}))
    assert resolve_refresh_token(settings(yahoo_refresh_token=""), path) == "from-file"


def test_missing_token_fails_loudly_with_instructions(tmp_path):
    with pytest.raises(TokenUnavailable) as exc:
        resolve_refresh_token(settings(yahoo_refresh_token=""), tmp_path / "absent")
    assert "yahoofantasy login" in str(exc.value)


@pytest.mark.parametrize(
    "content",
    [b"not a pickle at all", pickle.dumps(["a", "list"]), pickle.dumps({"auth": "not-a-dict"}),
     pickle.dumps({"no_auth_key": 1}), b""],
)
def test_unreadable_token_files_return_none_rather_than_raising(tmp_path, content):
    path = tmp_path / ".yahoofantasy"
    path.write_bytes(content)
    assert read_persisted_refresh_token(path) is None


def test_absent_file_returns_none(tmp_path):
    assert read_persisted_refresh_token(tmp_path / "nope") is None


# --- access token lifecycle -------------------------------------------------------------


def test_token_is_fetched_once_and_reused():
    session = FakeSession()
    t = transport(session)
    assert t.access_token() == "tok"
    assert t.access_token() == "tok"
    assert len(session.posts) == 1


def test_expired_token_is_refreshed():
    clock = [1000.0]
    session = FakeSession(post_responses=[
        FakeResponse(200, {"access_token": "first", "expires_in": 3600}),
        FakeResponse(200, {"access_token": "second", "expires_in": 3600}),
    ])
    t = transport(session, now=lambda: clock[0])
    assert t.access_token() == "first"
    clock[0] += 3600
    assert t.access_token() == "second"
    assert len(session.posts) == 2


def test_token_is_refreshed_slightly_before_expiry():
    """A token that lapses mid-request turns a working run into a spurious auth failure."""
    token = AccessToken(value="x", expires_at=1000.0)
    assert token.is_valid(now=900.0)
    assert not token.is_valid(now=999.0)     # inside the skew window
    assert not token.is_valid(now=1001.0)


def test_empty_token_value_is_never_valid():
    assert not AccessToken(value="", expires_at=1e12).is_valid(now=0.0)


@pytest.mark.parametrize("status", [400, 401, 403, 500])
def test_rejected_refresh_raises_rather_than_retrying(status):
    """A revoked grant must not be retried into looking like a throttle (D-29)."""
    session = FakeSession(post_responses=[FakeResponse(status, text="denied")])
    with pytest.raises(AuthenticationFailed):
        transport(session).access_token()
    assert len(session.posts) == 1


def test_token_response_without_a_token_is_a_failure():
    """A 200 carrying no access_token must not be treated as success."""
    session = FakeSession(post_responses=[FakeResponse(200, {"expires_in": 3600})])
    with pytest.raises(AuthenticationFailed):
        transport(session).access_token()


def test_rotated_refresh_token_is_kept():
    """Yahoo issues a new refresh token on each exchange; dropping it breaks the next refresh."""
    session = FakeSession(post_responses=[
        FakeResponse(200, {"access_token": "a", "expires_in": 0, "refresh_token": "rotated"}),
        FakeResponse(200, {"access_token": "b", "expires_in": 3600}),
    ])
    t = transport(session)
    t.access_token()
    t.access_token()
    assert session.posts[1][1]["data"]["refresh_token"] == "rotated"


# --- the call site: requests go through the limiter --------------------------------------


def test_get_returns_the_response_on_success():
    session = FakeSession(get_responses=[FakeResponse(200, {"fantasy_content": {}})])
    assert transport(session).get("users;use_login=1/games").status_code == 200


def test_get_builds_the_url_and_asks_for_json():
    session = FakeSession()
    transport(session).get("/league/461.l.1/standings")
    url, kwargs = session.gets[0]
    assert url == f"{API_ROOT}/league/461.l.1/standings"
    assert kwargs["params"]["format"] == "json"
    assert kwargs["headers"]["Authorization"] == "Bearer tok"


def test_throttling_is_actually_retried_through_the_transport():
    """The point of this module existing: a 999 reaches the limiter and is retried, rather
    than being handed back as a successful body (D-43, D-45)."""
    session = FakeSession(get_responses=[
        FakeResponse(999), FakeResponse(999), FakeResponse(200, {"ok": 1}),
    ])
    t = transport(session)
    assert t.get("anything").status_code == 200
    assert len(session.gets) == 3
    assert [status for status, _, _ in t.retry_log.waits] == [999, 999]


def test_sustained_throttling_exhausts_the_budget_loudly():
    session = FakeSession(get_responses=[FakeResponse(999) for _ in range(10)])
    with pytest.raises(RateLimitExhausted):
        transport(session).get("anything")


def test_retry_after_is_honoured_through_the_transport():
    session = FakeSession(get_responses=[
        FakeResponse(429, headers={"Retry-After": "40"}), FakeResponse(200),
    ])
    t = transport(session)
    t.get("anything")
    assert [delay for _, delay, _ in t.retry_log.waits] == [40.0]


def test_client_error_raises_and_is_not_retried():
    session = FakeSession(get_responses=[FakeResponse(404, text="no such league")])
    with pytest.raises(RequestFailed) as exc:
        transport(session).get("league/nope")
    assert exc.value.status == 404
    assert len(session.gets) == 1


def test_auth_error_on_a_data_request_raises():
    session = FakeSession(get_responses=[FakeResponse(401)])
    with pytest.raises(AuthenticationFailed):
        transport(session).get("anything")


def test_pacing_is_applied_between_requests():
    """request_interval reaches the wire, rather than being config nobody reads (D-44)."""
    slept, now = [], [0.0]

    def sleep(seconds):
        slept.append(seconds)
        now[0] += seconds

    session = FakeSession()
    t = YahooTransport(
        settings(), policy=BackoffPolicy(request_interval=1.5, jitter=0.0),
        session=session, sleep=sleep, monotonic=lambda: now[0], now=lambda: 1000.0,
    )
    t.get("a")
    t.get("b")
    t.get("c")
    assert slept == [1.5, 1.5]
