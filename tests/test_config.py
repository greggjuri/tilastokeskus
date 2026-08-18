from datetime import date

import pytest

from tilastokeskus.config import default_season


@pytest.mark.parametrize(
    "today,expected",
    [
        (date(2026, 8, 17), 2026),   # preseason: current year
        (date(2026, 12, 25), 2026),  # mid-season
        (date(2027, 1, 15), 2026),   # playoffs run into the new calendar year
        (date(2027, 2, 28), 2026),   # still the 2026 season the day before rollover
        (date(2027, 3, 1), 2027),    # Yahoo publishes the new game key in spring
    ],
)
def test_season_rollover(today, expected):
    assert default_season(today) == expected


def test_season_is_never_hardcoded():
    """The default must track the calendar, not a literal (D-03)."""
    assert default_season(date(2031, 9, 1)) == 2031
