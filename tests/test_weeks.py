import pytest

from tilastokeskus.weeks import WeekRangeError, parse_weeks


@pytest.mark.parametrize(
    "spec,expected",
    [
        ("3", [3]),
        ("1-4", [1, 2, 3, 4]),
        ("1,3,5", [1, 3, 5]),
        ("1-3,7", [1, 2, 3, 7]),
        ("18", [18]),
        (" 2 , 1 ", [1, 2]),          # whitespace tolerated, result sorted
        ("1-3,2-4", [1, 2, 3, 4]),    # overlapping ranges deduplicated
    ],
)
def test_parses(spec, expected):
    assert parse_weeks(spec) == expected


@pytest.mark.parametrize("spec", ["0", "19", "-1", "5-2", "abc", "", "   ", "1-", "-"])
def test_rejects(spec):
    with pytest.raises(WeekRangeError):
        parse_weeks(spec)
