import pytest

from tilastokeskus.config import Settings
from tilastokeskus.yahoo import YahooClient, YahooCollectorNotImplemented


def settings() -> Settings:
    return Settings(
        pg_host="localhost", pg_port=5432, pg_database="t", pg_user="u", pg_password="p",
        season=2026, raw_dir="raw", yahoo_client_id="id", yahoo_client_secret="secret",
        yahoo_redirect_uri="https://localhost:8000", yahoo_refresh_token="refresh-abc",
    )


CALLS = [
    ("league_keys", (2026,), "listing league keys"),
    ("league", ("470.l.1",), "fetching a league"),
    ("teams", ("470.l.1",), "fetching teams"),
    ("draft_results", ("470.l.1",), "fetching draft results"),
    ("roster", ("470.l.1.t.1", 1), "fetching a roster"),
    ("standings", ("470.l.1", 1), "fetching standings"),
    ("scoreboard", ("470.l.1", 1), "fetching a scoreboard"),
    ("transactions", ("470.l.1",), "fetching transactions"),
]


@pytest.mark.parametrize(("method", "args", "operation"), CALLS)
def test_stub_raises_with_a_reason_and_a_pointer(method, args, operation):
    """Stubs raise rather than return empty, naming what was attempted and where to look (D-38)."""
    with pytest.raises(YahooCollectorNotImplemented) as exc:
        getattr(YahooClient(settings()), method)(*args)

    message = str(exc.value)
    assert message.startswith(f"{operation} is not implemented yet")
    assert "docs/TASK.md" in message


def test_stub_error_is_a_not_implemented_error():
    # The CLI maps NotImplementedError to exit 1; a stub raising anything else would escape it.
    assert issubclass(YahooCollectorNotImplemented, NotImplementedError)


def test_every_public_client_method_is_covered():
    # A method added without a row in CALLS would otherwise go untested.
    public = {name for name in vars(YahooClient) if not name.startswith("_")}
    assert public == {method for method, _, _ in CALLS}
