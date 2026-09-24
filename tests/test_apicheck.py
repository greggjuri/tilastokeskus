"""Tests for the daily API access probe.

The three properties that matter are the ones the timer depends on: it writes nothing, it posts
whatever happened, and it exits 0 no matter what. Each is tested directly rather than inferred.
"""

from __future__ import annotations

from datetime import UTC, datetime

from tilastokeskus.apicheck import CheckResult, check, post_to_discord
from tilastokeskus.cli import main
from tilastokeskus.config import Settings

AT = datetime(2026, 9, 24, 13, 0, 5, tzinfo=UTC)


def settings(**overrides) -> Settings:
    base = {
        "pg_host": "localhost", "pg_port": 5432, "pg_database": "t", "pg_user": "u",
        "pg_password": "p", "season": 2026, "raw_dir": "raw", "yahoo_client_id": "id",
        "yahoo_client_secret": "secret", "yahoo_redirect_uri": "https://localhost:8000",
        "yahoo_refresh_token": "refresh-abc", "discord_webhook_url": "https://discord/hook",
    }
    base.update(overrides)
    return Settings(**base)


class FakeResponse:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


class FakeSession:
    """Records calls so tests can assert what was and was not requested."""

    def __init__(self, token_response=None, probe_response=None, post_response=None, raises=None):
        self.token_response = token_response or FakeResponse(200, {"access_token": "at"})
        self.probe_response = probe_response or FakeResponse(403)
        self.post_response = post_response or FakeResponse(204)
        self.raises = raises
        self.posts = []
        self.gets = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        if "discord" in url:
            if self.raises:
                raise self.raises
            return self.post_response
        return self.token_response

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        if self.raises:
            raise self.raises
        return self.probe_response


# --- timestamp and status appear in every message ---------------------------------------------


def test_every_message_carries_a_timestamp_and_a_status():
    for result in [
        CheckResult(AT, 200, True, "fine"),
        CheckResult(AT, 403, False, "blocked"),
        CheckResult(AT, 401, False, "revoked"),
        CheckResult(AT, 500, False, "odd"),
        CheckResult(AT, None, False, "never got a status"),
    ]:
        message = result.message()
        assert "2026-09-24 13:00:05" in message
        expected = str(result.status) if result.status is not None else "no response"
        assert expected in message


def test_success_and_failure_messages_are_distinguishable():
    assert "LIVE" in CheckResult(AT, 200, True, "").message()
    assert "still blocked" in CheckResult(AT, 403, False, "").message()


# --- the probe itself ---------------------------------------------------------------------------


def test_200_is_reported_as_access_live():
    result = check(settings(), session=FakeSession(probe_response=FakeResponse(200)), now=AT)
    assert (result.ok, result.status) == (True, 200)


def test_403_is_reported_as_still_blocked():
    result = check(settings(), session=FakeSession(probe_response=FakeResponse(403)), now=AT)
    assert (result.ok, result.status) == (False, 403)
    assert "Yahoo-side" in result.detail


def test_401_is_called_out_as_different_from_the_standing_403():
    """A revoked refresh token must not be mistaken for the usual 403."""
    result = check(settings(), session=FakeSession(probe_response=FakeResponse(401)), now=AT)
    assert result.status == 401
    assert "revoked" in result.detail


def test_a_failed_token_refresh_does_not_raise():
    session = FakeSession(token_response=FakeResponse(400))
    result = check(settings(), session=session, now=AT)
    assert result.ok is False and result.status is None
    assert session.gets == [], "must not probe the API when auth failed"


def test_a_network_error_does_not_raise():
    result = check(settings(), session=FakeSession(raises=OSError("boom")), now=AT)
    assert result.ok is False and result.status is None


def test_missing_refresh_token_is_reported_not_raised():
    result = check(settings(yahoo_refresh_token=""), session=FakeSession(), now=AT)
    assert result.ok is False
    assert "No refresh token" in result.detail


# --- posting ------------------------------------------------------------------------------------


def test_post_returns_false_rather_than_raising():
    delivered, detail = post_to_discord("https://discord/hook", "hi",
                                        session=FakeSession(raises=OSError("down")))
    assert delivered is False
    assert "failed" in detail


def test_webhook_url_never_appears_in_the_returned_detail():
    """It is a credential: holding it is enough to post to the channel."""
    secret = "https://discord.com/api/webhooks/123/SUPERSECRETTOKEN"
    _, detail = post_to_discord(secret, "hi", session=FakeSession(raises=OSError("x")))
    assert "SUPERSECRETTOKEN" not in detail
    _, detail = post_to_discord(secret, "hi", session=FakeSession(post_response=FakeResponse(500)))
    assert "SUPERSECRETTOKEN" not in detail


def test_a_4xx_from_discord_is_not_delivered():
    delivered, _ = post_to_discord("https://discord/hook", "hi",
                                   session=FakeSession(post_response=FakeResponse(404)))
    assert delivered is False


# --- CLI: always exit 0 ---------------------------------------------------------------------------


def _patch(monkeypatch, result, delivered=True):
    monkeypatch.setattr("tilastokeskus.apicheck.check", lambda *a, **k: result)
    monkeypatch.setattr("tilastokeskus.apicheck.post_to_discord",
                        lambda *a, **k: (delivered, "ok" if delivered else "webhook returned 500"))
    monkeypatch.setattr("tilastokeskus.cli.load_settings", lambda *a, **k: settings())


def test_exit_zero_when_access_is_live(monkeypatch, capsys):
    _patch(monkeypatch, CheckResult(AT, 200, True, "fine"))
    assert main(["apicheck"]) == 0
    assert "LIVE" in capsys.readouterr().out


def test_exit_zero_when_still_blocked(monkeypatch, capsys):
    _patch(monkeypatch, CheckResult(AT, 403, False, "blocked"))
    assert main(["apicheck"]) == 0
    assert "still blocked" in capsys.readouterr().out


def test_exit_zero_when_the_probe_itself_failed(monkeypatch):
    _patch(monkeypatch, CheckResult(AT, None, False, "network down"))
    assert main(["apicheck"]) == 0


def test_exit_zero_when_the_webhook_post_fails(monkeypatch, capsys):
    """A Discord outage must not park the systemd unit in `failed`."""
    _patch(monkeypatch, CheckResult(AT, 403, False, "blocked"), delivered=False)
    assert main(["apicheck"]) == 0
    assert "webhook returned 500" in capsys.readouterr().err


def test_exit_zero_when_no_webhook_is_configured(monkeypatch, capsys):
    monkeypatch.setattr("tilastokeskus.apicheck.check",
                        lambda *a, **k: CheckResult(AT, 403, False, "blocked"))
    monkeypatch.setattr("tilastokeskus.cli.load_settings",
                        lambda *a, **k: settings(discord_webhook_url=""))
    assert main(["apicheck"]) == 0
    assert "not set" in capsys.readouterr().err


def test_no_post_flag_prints_without_posting(monkeypatch, capsys):
    posted = []
    monkeypatch.setattr("tilastokeskus.apicheck.check",
                        lambda *a, **k: CheckResult(AT, 403, False, "blocked"))
    monkeypatch.setattr("tilastokeskus.apicheck.post_to_discord",
                        lambda *a, **k: posted.append(1) or (True, "ok"))
    monkeypatch.setattr("tilastokeskus.cli.load_settings", lambda *a, **k: settings())
    assert main(["apicheck", "--no-post"]) == 0
    assert posted == []
    assert "still blocked" in capsys.readouterr().out


# --- writes nothing -------------------------------------------------------------------------------


def test_apicheck_does_not_import_the_database():
    """Structural, not a promise: the module has no way to write rows."""
    from pathlib import Path

    import tilastokeskus.apicheck as mod

    source = Path(mod.__file__).read_text()
    assert "from .db" not in source
    assert "connect(" not in source
    assert "raw_dir" not in source


def test_probe_writes_no_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    check(settings(), session=FakeSession(), now=AT)
    assert list(tmp_path.iterdir()) == []
