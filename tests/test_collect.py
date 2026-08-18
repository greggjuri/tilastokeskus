import pytest

from tilastokeskus.collect import CollectionPlan, resolve_weeks


def test_explicit_weeks_win():
    plan = CollectionPlan(season=2026, weeks=[1, 2, 3])
    assert resolve_weeks(plan, {"current_week": 9}) == [1, 2, 3]


def test_falls_back_to_league_current_week():
    plan = CollectionPlan(season=2026)
    assert resolve_weeks(plan, {"current_week": 9}) == [9]


def test_current_week_is_per_league():
    """Eight leagues can sit on different weeks; each resolves from its own resource (D-24a)."""
    plan = CollectionPlan(season=2026)
    leagues = [{"current_week": 3}, {"current_week": 4}]
    assert [resolve_weeks(plan, lg) for lg in leagues] == [[3], [4]]


def test_refuses_to_guess_from_the_calendar():
    plan = CollectionPlan(season=2026)
    with pytest.raises(ValueError, match="refusing to guess"):
        resolve_weeks(plan, {"league_key": "x.l.1"})
