import gzip
import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from tilastokeskus.archive import RawArchive

STARTED = datetime(2026, 10, 9, 4, 1, 12, tzinfo=UTC)


def read(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def test_round_trip(tmp_path):
    payload = {"fantasy_content": {"league": [{"league_key": "470.l.1"}, {"teams": {"count": 0}}]}}
    path = RawArchive(tmp_path, STARTED).write("teams", "470.l.1", payload)
    assert read(path) == payload


def test_layout_is_utc_date_then_run_time(tmp_path):
    path = RawArchive(tmp_path, STARTED).write("teams", "470.l.1", {})
    assert path == tmp_path / "2026-10-09" / "040112" / "teams" / "470.l.1.json.gz"


def test_layout_converts_local_time_to_utc(tmp_path):
    # 23:30 on the 8th in UTC-5 is 04:30 on the 9th in UTC.
    local = datetime(2026, 10, 8, 23, 30, 0, tzinfo=timezone(timedelta(hours=-5)))
    path = RawArchive(tmp_path, local).write("discovery", "470", {})
    assert path.parent.parent == tmp_path / "2026-10-09" / "043000"


def test_naive_start_time_is_refused(tmp_path):
    with pytest.raises(ValueError, match="timezone-aware"):
        RawArchive(tmp_path, datetime(2026, 10, 9, 4, 1, 12))  # noqa: DTZ001 - naive on purpose


@pytest.mark.parametrize("resource, key", [
    ("", "470.l.1"), ("teams", ""), ("../teams", "470.l.1"), ("teams", "a/b"),
    ("teams", ".."), (".", "470.l.1"),
])
def test_invalid_names_are_refused(tmp_path, resource, key):
    with pytest.raises(ValueError, match="invalid archive"):
        RawArchive(tmp_path, STARTED).write(resource, key, {})
    assert not any(tmp_path.rglob("*.gz"))


def test_never_overwrites(tmp_path):
    archive = RawArchive(tmp_path, STARTED)
    archive.write("teams", "470.l.1", {"first": 1})
    with pytest.raises(FileExistsError):
        archive.write("teams", "470.l.1", {"second": 2})
    assert read(archive.run_dir / "teams" / "470.l.1.json.gz") == {"first": 1}


def test_a_failed_write_leaves_neither_file_nor_temporary(tmp_path):
    archive = RawArchive(tmp_path, STARTED)
    with pytest.raises(TypeError):
        archive.write("teams", "470.l.1", {"unserialisable": object()})
    assert list((archive.run_dir / "teams").iterdir()) == []


def test_purge_removes_the_archive(tmp_path, monkeypatch):
    """`tilasto purge --data` covers the new layout (D-50)."""
    from tilastokeskus import purge

    RawArchive(tmp_path / "raw", STARTED).write("teams", "470.l.1", {})
    settings = type("S", (), {"raw_dir": tmp_path / "raw"})()
    monkeypatch.setattr(purge, "_count_rows", lambda _s: {})
    report = purge.purge_data(settings, dry_run=False)
    assert report.archive_files_deleted == 1
    assert not (tmp_path / "raw").exists()
