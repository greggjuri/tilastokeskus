"""No term of the API agreement reaches a committed file (D-52).

A grep that reports hits is skimmed once its known hits are all false positives, so this asserts
the expected number of matching lines per file instead. The known matches are the places where the
rule itself is stated. A change in any count is the signal: a new match is a possible leak, and a
missing one means a statement of the rule was deleted or reworded out of the pattern.

Files ignored by git are not scanned — `docs/AGREEMENT.md` is where the terms are meant to live.
Untracked files that are not ignored are scanned, so a leak is caught before it is staged.
"""

from __future__ import annotations

import re
import subprocess
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

AGREEMENT_TERM = re.compile(r"[0-9]+ (business )?days|clause|section [0-9]+", re.IGNORECASE)

# Matching lines per file. Each one states the rule; none of them is a term.
EXPECTED = {
    "CLAUDE.md": 1,                       # Critical Rules: what committed files may not carry
    "docs/DECISIONS.md": 1,               # D-52: what a committed document may not say
    "tests/test_agreement_terms.py": 1,   # AGREEMENT_TERM above
}


def scanned_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=REPO, capture_output=True, text=True, check=True,
    )
    return [line for line in result.stdout.splitlines() if (REPO / line).is_file()]


def term_hits() -> Counter[str]:
    hits: Counter[str] = Counter()
    for name in scanned_files():
        try:
            text = (REPO / name).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # binary — the logo asset
        hits[name] += sum(1 for line in text.splitlines() if AGREEMENT_TERM.search(line))
    return +hits


def test_scan_covers_the_repository() -> None:
    # A scan that silently found no files would pass the count check below vacuously.
    files = scanned_files()
    assert "CLAUDE.md" in files
    assert "docs/DECISIONS.md" in files
    assert "docs/AGREEMENT.md" not in files


def test_agreement_term_counts_are_unchanged() -> None:
    assert dict(term_hits()) == EXPECTED, (
        "agreement-term matches changed (D-52). A new match may be a term leaking into a "
        "committed file; a missing one means a statement of the rule changed. Inspect with: "
        f"git grep -nEi '{AGREEMENT_TERM.pattern}'"
    )
