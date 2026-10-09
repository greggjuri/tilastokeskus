import gzip
import json
from datetime import UTC, datetime

import pytest

from tilastokeskus.archive import RawArchive
from tilastokeskus.config import Settings
from tilastokeskus.yahoo import YahooClient, YahooCollectorNotImplemented

STARTED = datetime(2026, 10, 9, 4, 1, 12, tzinfo=UTC)


def settings() -> Settings:
    return Settings(
        pg_host="localhost", pg_port=5432, pg_database="t", pg_user="u", pg_password="p",
        season=2026, raw_dir="raw", yahoo_client_id="id", yahoo_client_secret="secret",
        yahoo_redirect_uri="https://localhost:8000", yahoo_refresh_token="refresh-abc",
    )


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeTransport:
    def __init__(self, payload=None):
        self.paths = []
        self.payload = payload if payload is not None else {"fantasy_content": {}}

    def get(self, path):
        self.paths.append(path)
        return FakeResponse(self.payload)


def client(tmp_path, transport=None):
    return YahooClient(settings(), transport or FakeTransport(), RawArchive(tmp_path, STARTED))


# ---- implemented: fetch, archive, return -----------------------------------------------------

@pytest.mark.parametrize("method, arg, path, archived", [
    ("discover", 2026, "users;use_login=1/games;game_codes=nfl;seasons=2026/leagues",
     "discovery/nfl-2026.json.gz"),
    ("discover", 2025, "users;use_login=1/games;game_codes=nfl;seasons=2025/leagues",
     "discovery/nfl-2025.json.gz"),
    ("settings", "470.l.1", "league/470.l.1/settings", "settings/470.l.1.json.gz"),
    ("teams", "470.l.1", "league/470.l.1/teams", "teams/470.l.1.json.gz"),
    ("draft", "470.l.1", "league/470.l.1/draftresults/players", "draftresults/470.l.1.json.gz"),
])
def test_fetches_the_path_and_archives_before_returning(tmp_path, method, arg, path, archived):
    transport = FakeTransport({"fantasy_content": {"marker": method}})
    c = client(tmp_path, transport)
    payload = getattr(c, method)(arg)

    assert transport.paths == [path]
    archived_path = c.archive.run_dir / archived
    with gzip.open(archived_path, "rt") as fh:
        assert json.load(fh) == payload == {"fantasy_content": {"marker": method}}


def test_an_archive_failure_raises_and_returns_nothing(tmp_path):
    c = client(tmp_path)
    c.teams("470.l.1")
    with pytest.raises(FileExistsError):
        c.teams("470.l.1")                     # same run, same key: the archive refuses


@pytest.mark.parametrize("season", ["2026", 2026.0, True, None])
def test_discover_refuses_a_non_integer_season(tmp_path, season):
    transport = FakeTransport()
    with pytest.raises(TypeError):
        client(tmp_path, transport).discover(season)
    assert transport.paths == []


def test_open_builds_a_real_transport_and_archive(tmp_path):
    s = settings()
    c = YahooClient.open(Settings(**{**vars(s), "raw_dir": tmp_path}), STARTED)
    assert c.transport.requests_issued == 0
    assert c.archive.run_dir == tmp_path / "2026-10-09" / "040112"


# ---- not yet implemented: raise with a reason and a pointer (D-38) ---------------------------

STUBS = [
    ("roster", ("470.l.1.t.1", 1), "fetching a roster"),
    ("standings", ("470.l.1", 1), "fetching standings"),
    ("scoreboard", ("470.l.1", 1), "fetching a scoreboard"),
    ("transactions", ("470.l.1",), "fetching transactions"),
]
IMPLEMENTED = {"open", "discover", "settings", "teams", "draft"}


@pytest.mark.parametrize(("method", "args", "operation"), STUBS)
def test_stub_raises_with_a_reason_and_a_pointer(tmp_path, method, args, operation):
    transport = FakeTransport()
    with pytest.raises(YahooCollectorNotImplemented) as exc:
        getattr(client(tmp_path, transport), method)(*args)

    message = str(exc.value)
    assert message.startswith(f"{operation} is not implemented yet")
    assert "docs/TASK.md" in message
    assert transport.paths == []               # no request issued on the way to raising


def test_stub_error_is_a_not_implemented_error():
    # The CLI maps NotImplementedError to exit 1; a stub raising anything else would escape it.
    assert issubclass(YahooCollectorNotImplemented, NotImplementedError)


def test_every_public_client_method_is_covered():
    # A method added without a row here would otherwise go untested.
    public = {name for name in vars(YahooClient) if not name.startswith("_")}
    assert public == IMPLEMENTED | {method for method, _, _ in STUBS}
