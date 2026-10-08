import json
from datetime import UTC, datetime

import pytest
from conftest import load_fixture

from tilastokeskus.collect import (
    CollectionPlan,
    RunResult,
    UnknownLeague,
    decide_status,
    resolve_weeks,
    run,
)
from tilastokeskus.parse import UnexpectedPayload, parse_discovery
from tilastokeskus.ratelimit import RateLimitExhausted, RequestTimedOut
from tilastokeskus.transport import AuthenticationFailed


def test_explicit_weeks_win():
    plan = CollectionPlan(season=2026, weeks=[1, 2, 3])
    assert resolve_weeks(plan, {"current_week": 9}) == [1, 2, 3]


def test_falls_back_to_league_current_week():
    plan = CollectionPlan(season=2026)
    assert resolve_weeks(plan, {"current_week": 9}) == [9]


def test_current_week_is_per_league():
    """Fifteen leagues can sit on different weeks; each resolves from its own resource (D-24a)."""
    plan = CollectionPlan(season=2026)
    leagues = [{"current_week": 3}, {"current_week": 4}]
    assert [resolve_weeks(plan, lg) for lg in leagues] == [[3], [4]]


def test_refuses_to_guess_from_the_calendar():
    plan = CollectionPlan(season=2026)
    with pytest.raises(ValueError, match="refusing to guess"):
        resolve_weeks(plan, {"league_key": "x.l.1"})


# ---- orchestration (PRP-01) ------------------------------------------------------------------

DISCOVERED = parse_discovery(load_fixture("discovery_2026"), 2026).league_keys


def rekey(payload, old, new):
    """A fixture moved to another league: every key under `old` becomes the same key under `new`."""
    return json.loads(json.dumps(payload).replace(f'"{old}', f'"{new}'))


class FakeClient:
    """Serves the redacted fixtures, re-keyed per league. Records every call."""

    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail or {}                 # (method, league_key) -> exception or callable

    def _maybe_fail(self, method, key, payload):
        action = self.fail.get((method, key))
        if isinstance(action, BaseException):
            raise action
        return action(payload) if callable(action) else payload

    def discover(self, season):
        self.calls.append(("discover", season))
        return load_fixture("discovery_2026")

    def teams(self, key):
        self.calls.append(("teams", key))
        payload = load_fixture("teams")
        payload = rekey(payload, payload["fantasy_content"]["league"][0]["league_key"], key)
        return self._maybe_fail("teams", key, payload)

    def settings(self, key):
        self.calls.append(("settings", key))
        payload = load_fixture("settings")
        payload = rekey(payload, payload["fantasy_content"]["league"][0]["league_key"], key)
        return self._maybe_fail("settings", key, payload)


def plan(keys=None):
    return CollectionPlan(season=2026, league_keys=keys)


def runs(db):
    return db.execute("SELECT status, leagues_synced, rows_written, error "
                      "FROM collector_runs ORDER BY id").fetchall()


def table_counts(db):
    return tuple(db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("leagues", "teams"))


def points_scoring(payload):
    payload["fantasy_content"]["league"][0]["scoring_type"] = "points"
    return payload


def test_every_league_succeeds(db):
    keys = DISCOVERED[:3]
    result = run(plan(keys), FakeClient(), db)
    assert (result.status, result.leagues_synced, result.rows_written) == ("success", 3, 36)
    assert table_counts(db) == (3, 30)
    assert runs(db) == [("success", 3, 36, None)]


def test_all_fifteen_with_no_league_flag(db):
    result = run(plan(), FakeClient(), db)
    assert result.status == "success" and result.leagues_synced == 15
    assert table_counts(db) == (15, 150)


def test_one_failing_league_makes_the_run_partial_and_keeps_the_others(db):
    keys = DISCOVERED[:3]
    client = FakeClient(fail={("teams", keys[1]): points_scoring})
    result = run(plan(keys), client, db)

    assert result.status == "partial"
    assert result.leagues_synced == 2
    assert [k for k, _ in result.failed_leagues] == [keys[1]]
    assert db.execute("SELECT count(*) FROM leagues WHERE league_key = %s",
                      (keys[1],)).fetchone()[0] == 0
    status, synced, _, error = runs(db)[0]
    assert (status, synced) == ("partial", 2)
    assert keys[1] in error and "UnexpectedPayload" in error


def test_a_settings_failure_rolls_back_that_leagues_metadata_and_teams(db):
    keys = DISCOVERED[:2]
    bad = load_fixture("settings")
    bad["fantasy_content"]["league"][1]["settings"][0]["uses_faab"] = "1"
    client = FakeClient(fail={("settings", keys[0]): lambda p: rekey(
        bad, bad["fantasy_content"]["league"][0]["league_key"], keys[0])})
    result = run(plan(keys), client, db)
    assert result.status == "partial"
    assert "FAAB" in result.error
    assert db.execute("SELECT count(*) FROM teams WHERE league_key = %s",
                      (keys[0],)).fetchone()[0] == 0


def test_every_league_failing_is_failed_not_partial(db):
    keys = DISCOVERED[:2]
    client = FakeClient(fail={("teams", k): points_scoring for k in keys})
    result = run(plan(keys), client, db)
    assert result.status == "failed"
    assert runs(db)[0][0] == "failed"


def test_auth_failure_aborts_the_run_records_it_and_stops_requesting(db):
    keys = DISCOVERED[:3]
    client = FakeClient(fail={("teams", keys[0]): AuthenticationFailed(401, "token_rejected")})
    with pytest.raises(AuthenticationFailed):
        run(plan(keys), client, db)
    assert [c for c in client.calls if c[0] == "teams"] == [("teams", keys[0])]
    status, synced, _, error = runs(db)[0]
    assert (status, synced) == ("failed", 0)
    assert error.startswith("AuthenticationFailed")


def test_exhausted_backoff_aborts_rather_than_moving_to_the_next_league(db):
    keys = DISCOVERED[:3]
    client = FakeClient(fail={("teams", keys[1]): RateLimitExhausted(5, 62.0, "999")})
    with pytest.raises(RateLimitExhausted):
        run(plan(keys), client, db)
    assert len([c for c in client.calls if c[0] == "teams"]) == 2
    assert runs(db)[0][:2] == ("failed", 1)


def test_persistent_timeouts_abort_the_run_rather_than_moving_on(db):
    keys = DISCOVERED[:3]
    client = FakeClient(fail={("teams", keys[0]): RequestTimedOut(5, 30.0, "ReadTimeout")})
    with pytest.raises(RequestTimedOut):
        run(plan(keys), client, db)
    assert len([c for c in client.calls if c[0] == "teams"]) == 1
    status, synced, _, error = runs(db)[0]
    assert (status, synced) == ("failed", 0)
    assert error.startswith("RequestTimedOut")


def test_a_discovery_failure_is_recorded(db):
    client = FakeClient()
    client.discover = lambda season: {"fantasy_content": {}}
    with pytest.raises(UnexpectedPayload):
        run(plan(), client, db)
    assert runs(db)[0][0] == "failed"


def test_an_unknown_league_raises_before_any_league_request_and_records_nothing(db):
    client = FakeClient()
    with pytest.raises(UnknownLeague, match="470.l.1"):
        run(plan(["470.l.1"]), client, db)
    assert client.calls == [("discover", 2026)]
    assert runs(db) == []


def test_settings_are_fetched_once_per_league(db):
    keys = DISCOVERED[:2]
    first, second = FakeClient(), FakeClient()
    run(plan(keys), first, db)
    run(plan(keys), second, db)
    assert sorted(k for m, k in first.calls if m == "settings") == sorted(keys)
    assert [c for c in second.calls if c[0] == "settings"] == []


def test_rerun_changes_no_row_counts(db):
    """D-19, through the whole orchestrator rather than the store alone."""
    run(plan(), FakeClient(), db)
    before = table_counts(db)
    run(plan(), FakeClient(), db)
    assert table_counts(db) == before == (15, 150)
    assert [r[0] for r in runs(db)] == ["success", "success"]


@pytest.mark.parametrize("kwargs", [{"weeks": [1, 2]}, {"draft_only": True}])
def test_unbuilt_scopes_raise_before_any_request(db, kwargs):
    client = FakeClient()
    with pytest.raises(NotImplementedError, match="docs/TASK.md"):
        run(CollectionPlan(season=2026, **kwargs), client, db)
    assert client.calls == []


@pytest.mark.parametrize("synced, rows, failed, planned, expected", [
    (3, 36, [], 3, "success"),
    (2, 24, [("k", "e")], 3, "partial"),
    (0, 0, [("k", "e")], 1, "failed"),
    (3, 0, [], 3, "failed"),                   # the Silence Check: success that wrote nothing
])
def test_decide_status(synced, rows, failed, planned, expected):
    result = RunResult(started_at=datetime.now(UTC), leagues_synced=synced, rows_written=rows,
                       failed_leagues=failed, leagues_planned=planned)
    assert decide_status(result) == expected
