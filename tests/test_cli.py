"""`tilasto collect`: exit codes, the dry run, and the request summary."""

from contextlib import contextmanager

import pytest
from test_collect import DISCOVERED, FakeClient, points_scoring

from tilastokeskus import cli
from tilastokeskus.config import Settings
from tilastokeskus.ratelimit import RetryLog
from tilastokeskus.transport import AuthenticationFailed


class CountingClient(FakeClient):
    """FakeClient with the transport attributes the CLI reports from."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.transport = type("T", (), {"requests_issued": 0, "retry_log": RetryLog()})()

    def discover(self, season):
        self.transport.requests_issued += 1
        return super().discover(season)

    def teams(self, key):
        self.transport.requests_issued += 1
        return super().teams(key)

    def settings(self, key):
        self.transport.requests_issued += 1
        return super().settings(key)


@pytest.fixture
def wired(db, monkeypatch, tmp_path):
    """The CLI against a fake client and the throwaway-schema database."""
    client = CountingClient()
    settings = Settings(
        pg_host="h", pg_port=1, pg_database="d", pg_user="u", pg_password="p", season=2026,
        raw_dir=tmp_path, yahoo_client_id="i", yahoo_client_secret="s",
        yahoo_redirect_uri="https://localhost:8000", yahoo_refresh_token="r",
    )

    @contextmanager
    def fake_connect(_settings, autocommit=False):
        yield db

    monkeypatch.setattr(cli, "load_settings", lambda season=None: settings)
    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr("tilastokeskus.yahoo.YahooClient.open",
                        classmethod(lambda cls, s, started: client))
    return client, db


def rows(db):
    return tuple(db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                 for t in ("leagues", "teams", "collector_runs"))


def test_success_exits_zero_and_reports_requests(wired, capsys):
    _client, db = wired
    assert cli.main(["collect", "--league", DISCOVERED[0]]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "status: success  leagues: 1/1  rows written: 12" in out
    assert "requests issued: 3" in out                 # discovery, teams, settings
    assert "retry log: empty" in out
    assert rows(db) == (1, 10, 1)


def test_partial_exits_one(wired, capsys):
    client, _db = wired
    client.fail = {("teams", DISCOVERED[1]): points_scoring}
    assert cli.main(["collect", "--league", DISCOVERED[0], "--league", DISCOVERED[1]]) == \
        cli.EXIT_ERROR
    captured = capsys.readouterr()
    assert "status: partial" in captured.out
    assert f"failed {DISCOVERED[1]}" in captured.err


def test_auth_failure_exits_one_and_still_reports_requests(wired, capsys):
    client, _db = wired
    client.fail = {("teams", DISCOVERED[0]): AuthenticationFailed(401, "token_rejected")}
    assert cli.main(["collect", "--league", DISCOVERED[0]]) == cli.EXIT_ERROR
    captured = capsys.readouterr()
    assert "AuthenticationFailed" in captured.err
    assert "requests issued: 2" in captured.out


def test_unknown_league_exits_two_and_writes_nothing(wired, capsys):
    _client, db = wired
    assert cli.main(["collect", "--league", "470.l.1"]) == cli.EXIT_USAGE
    assert "470.l.1" in capsys.readouterr().err
    assert rows(db) == (0, 0, 0)


def test_dry_run_issues_one_request_and_writes_no_row(wired, capsys):
    client, db = wired
    assert cli.main(["collect", "--all", "--dry-run"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert client.calls == [("discover", 2026)]
    assert "requests issued: 1" in out
    assert "planned requests: 30 (15 teams, 15 settings)" in out
    assert rows(db) == (0, 0, 0)


def test_dry_run_after_a_collection_plans_no_settings(wired, capsys):
    _client, _db = wired
    cli.main(["collect", "--league", DISCOVERED[0]])
    capsys.readouterr()
    cli.main(["collect", "--league", DISCOVERED[0], "--dry-run"])
    assert "planned requests: 1 (1 teams, 0 settings)" in capsys.readouterr().out


def test_unbuilt_scope_exits_one_before_any_request(wired, capsys):
    client, _db = wired
    assert cli.main(["collect", "--all", "--backfill", "--weeks", "1-3"]) == cli.EXIT_ERROR
    assert client.calls == []
    assert "phase 5" in capsys.readouterr().err


def test_dry_run_refuses_an_unbuilt_scope_before_any_request(wired, capsys):
    client, _db = wired
    assert cli.main(["collect", "--all", "--backfill", "--weeks", "1-3", "--dry-run"]) == \
        cli.EXIT_ERROR
    assert client.calls == []
    assert "requests issued: 0" in capsys.readouterr().out
