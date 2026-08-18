"""Week range parsing.

The collector takes a week range from the outset rather than assuming "current week", so
that backfill is the same code path over a different range rather than a separate importer.
See DECISIONS.md D-18.
"""

from __future__ import annotations

# Yahoo NFL fantasy weeks. Week 1 is the regular season opener; the tail covers the longest
# plausible playoff configuration. Ranges outside this are a typo, not a request.
MIN_WEEK = 1
MAX_WEEK = 18


class WeekRangeError(ValueError):
    """Raised when a week specification cannot be parsed or is out of range."""


def parse_weeks(spec: str) -> list[int]:
    """Parse a week specification into a sorted list of distinct weeks.

    Accepts comma-separated weeks and hyphenated ranges, in any combination:

        "3"        -> [3]
        "1-4"      -> [1, 2, 3, 4]
        "1,3,5"    -> [1, 3, 5]
        "1-3,7"    -> [1, 2, 3, 7]
    """
    if not spec or not spec.strip():
        raise WeekRangeError("empty week specification")

    weeks: set[int] = set()

    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue

        if "-" in part:
            start_text, _, end_text = part.partition("-")
            start, end = _as_week(start_text, part), _as_week(end_text, part)
            if start > end:
                raise WeekRangeError(f"range {part!r} runs backwards")
            weeks.update(range(start, end + 1))
        else:
            weeks.add(_as_week(part, part))

    if not weeks:
        raise WeekRangeError(f"no weeks in {spec!r}")

    return sorted(weeks)


def _as_week(text: str, context: str) -> int:
    text = text.strip()
    try:
        week = int(text)
    except ValueError:
        raise WeekRangeError(f"{text!r} in {context!r} is not a week number") from None

    if not MIN_WEEK <= week <= MAX_WEEK:
        raise WeekRangeError(
            f"week {week} in {context!r} is outside {MIN_WEEK}-{MAX_WEEK}"
        )
    return week
