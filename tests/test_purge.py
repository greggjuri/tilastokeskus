"""Tests for the D-50 deletion procedure.

The interesting assertions here are about what purge *keeps*. Deleting things is easy to get
right; deleting exactly the Yahoo credentials out of a file that also holds the Postgres password
is the part that can quietly cost something, so that is what most of these cover.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tilastokeskus import purge
from tilastokeskus.cli import main
from tilastokeskus.purge import (
    PURGED_TABLES,
    YAHOO_CREDENTIAL_KEYS,
    PurgeVerificationFailed,
    _clear_env_keys,
    purge_credentials,
    verify_credentials_purged,
)

ENV_SAMPLE = """\
# Copy to .env and fill in.

# ---- Yahoo Fantasy Sports API ----
YAHOO_CLIENT_ID=some-client-id
YAHOO_CLIENT_SECRET=some-secret
YAHOO_REDIRECT_URI=https://localhost:8000
YAHOO_REFRESH_TOKEN=some-refresh-token
YAHOO_PUBLIC_CLIENT_ID=some-public-id
YAHOO_PUBLIC_REFRESH_TOKEN=some-public-refresh

# ---- PostgreSQL ----
PGHOST=localhost
PGPORT=5432
PGDATABASE=tilastokeskus
PGUSER=tilasto_app
PGPASSWORD=the-database-password

# ---- Collector ----
TILASTO_SEASON=2026
TILASTO_RAW_DIR=raw

# ---- Not a Yahoo credential; purge must leave this alone ----
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/keep-me
"""


def write_env(tmp_path: Path, text: str = ENV_SAMPLE) -> Path:
    path = tmp_path / ".env"
    path.write_text(text)
    return path


# --- the preservation guarantee -------------------------------------------------------------


def test_postgres_password_survives_a_credential_purge(tmp_path):
    """The whole reason credentials are a separate flag: this must never be collateral damage."""
    path = write_env(tmp_path)
    _clear_env_keys(path, YAHOO_CREDENTIAL_KEYS, dry_run=False)
    assert "PGPASSWORD=the-database-password" in path.read_text()


def test_only_yahoo_credentials_are_cleared(tmp_path):
    path = write_env(tmp_path)
    cleared, preserved = _clear_env_keys(path, YAHOO_CREDENTIAL_KEYS, dry_run=False)

    assert sorted(cleared) == ["YAHOO_CLIENT_ID", "YAHOO_CLIENT_SECRET", "YAHOO_PUBLIC_CLIENT_ID",
                               "YAHOO_PUBLIC_REFRESH_TOKEN", "YAHOO_REFRESH_TOKEN"]
    for key in ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD",
                "TILASTO_SEASON", "TILASTO_RAW_DIR", "YAHOO_REDIRECT_URI",
                "DISCORD_WEBHOOK_URL"):
        assert key in preserved


def test_the_discord_webhook_survives_a_yahoo_credential_purge(tmp_path):
    """It is a secret, but it is ours rather than Yahoo's, so D-50 does not reach it (D-55)."""
    path = write_env(tmp_path)
    _clear_env_keys(path, YAHOO_CREDENTIAL_KEYS, dry_run=False)
    assert "DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/keep-me" in path.read_text()


def test_both_watched_apps_credentials_are_purged(tmp_path):
    """apicheck watches two apps; both sets are Yahoo credentials and both go."""
    path = write_env(tmp_path)
    _clear_env_keys(path, YAHOO_CREDENTIAL_KEYS, dry_run=False)
    text = path.read_text()
    assert "YAHOO_PUBLIC_CLIENT_ID=\n" in text
    assert "YAHOO_PUBLIC_REFRESH_TOKEN=\n" in text


def test_redirect_uri_is_not_a_credential(tmp_path):
    """It is our own configuration, not something Yahoo issued, so it stays."""
    path = write_env(tmp_path)
    _clear_env_keys(path, YAHOO_CREDENTIAL_KEYS, dry_run=False)
    assert "YAHOO_REDIRECT_URI=https://localhost:8000" in path.read_text()


def test_comments_blank_lines_and_ordering_survive(tmp_path):
    path = write_env(tmp_path)
    before = path.read_text().splitlines()
    _clear_env_keys(path, YAHOO_CREDENTIAL_KEYS, dry_run=False)
    after = path.read_text().splitlines()

    assert len(before) == len(after)
    for b, a in zip(before, after, strict=True):
        if b.split("=", 1)[0] not in YAHOO_CREDENTIAL_KEYS:
            assert b == a


def test_keys_are_blanked_not_deleted(tmp_path):
    """The file should still document which keys belong there."""
    path = write_env(tmp_path)
    _clear_env_keys(path, YAHOO_CREDENTIAL_KEYS, dry_run=False)
    text = path.read_text()
    for key in YAHOO_CREDENTIAL_KEYS:
        assert f"{key}=\n" in text


# --- dry run must not write ------------------------------------------------------------------


def test_dry_run_reports_without_changing_the_file(tmp_path):
    path = write_env(tmp_path)
    original = path.read_text()
    cleared, _ = _clear_env_keys(path, YAHOO_CREDENTIAL_KEYS, dry_run=True)

    assert cleared  # it still reports what it would do
    assert path.read_text() == original


def test_dry_run_leaves_the_token_file_in_place(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "nohome"))
    token = tmp_path / ".yahoofantasy"
    token.write_bytes(b"pickled")

    report = purge_credentials(dry_run=True)

    assert report.token_files_deleted == [Path(".yahoofantasy")]
    assert token.is_file()
    assert report.verified is False


# --- the real thing --------------------------------------------------------------------------


def test_purge_deletes_the_token_and_verifies(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "nohome"))
    monkeypatch.setattr(purge, "CONFIG_DIR", tmp_path / "noconfig")
    (tmp_path / ".yahoofantasy").write_bytes(b"pickled")
    write_env(tmp_path)

    report = purge_credentials(dry_run=False)

    assert not (tmp_path / ".yahoofantasy").exists()
    assert report.verified is True
    assert "PGPASSWORD=the-database-password" in (tmp_path / ".env").read_text()


def test_verification_fails_when_a_yahoo_key_still_holds_a_value(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "nohome"))
    monkeypatch.setattr(purge, "CONFIG_DIR", tmp_path / "noconfig")
    write_env(tmp_path)
    assert verify_credentials_purged() is False

    _clear_env_keys(tmp_path / ".env", YAHOO_CREDENTIAL_KEYS, dry_run=False)
    assert verify_credentials_purged() is True


def test_a_surviving_token_file_fails_verification_loudly(tmp_path, monkeypatch):
    """If the unlink silently did not happen, the command must not report success."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "nohome"))
    monkeypatch.setattr(purge, "CONFIG_DIR", tmp_path / "noconfig")
    write_env(tmp_path)
    (tmp_path / ".yahoofantasy").write_bytes(b"pickled")

    # Simulate a delete that did not take: unlink is a no-op, so the file survives.
    monkeypatch.setattr(Path, "unlink", lambda self, **kw: None)

    with pytest.raises(PurgeVerificationFailed, match="token"):
        purge_credentials(dry_run=False)


# --- scope ------------------------------------------------------------------------------------


def test_schema_migrations_is_never_purged():
    """Truncating it would leave a migrated database that believes it is empty."""
    assert "schema_migrations" not in PURGED_TABLES


def test_collector_runs_is_purged():
    """Its error column can carry payload fragments, so it goes with the data."""
    assert "collector_runs" in PURGED_TABLES


# --- CLI contract -------------------------------------------------------------------------------


def test_purge_without_a_target_refuses(capsys):
    assert main(["purge"]) == 2
    assert "needs --data, --credentials, or both" in capsys.readouterr().err


def test_neither_flag_implies_the_other(tmp_path, monkeypatch, capsys):
    """--credentials must not touch data, and must say so by not mentioning tables."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "nohome"))
    monkeypatch.setattr(purge, "CONFIG_DIR", tmp_path / "noconfig")
    write_env(tmp_path)

    assert main(["purge", "--credentials"]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "row(s)" not in out


def test_confirm_is_required_to_delete(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "nohome"))
    monkeypatch.setattr(purge, "CONFIG_DIR", tmp_path / "noconfig")
    write_env(tmp_path)
    token = tmp_path / ".yahoofantasy"
    token.write_bytes(b"pickled")

    main(["purge", "--credentials"])
    assert token.is_file(), "a run without --confirm must delete nothing"

    main(["purge", "--credentials", "--confirm"])
    assert not token.exists()


def test_env_file_absent_is_not_an_error(tmp_path, monkeypatch):
    """A purge on a host that never held credentials should succeed, not raise."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "nohome"))
    monkeypatch.setattr(purge, "CONFIG_DIR", tmp_path / "noconfig")

    report = purge_credentials(dry_run=False)
    assert report.verified is True
    assert report.token_files_deleted == []


def test_purge_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "nohome"))
    monkeypatch.setattr(purge, "CONFIG_DIR", tmp_path / "noconfig")
    write_env(tmp_path)

    purge_credentials(dry_run=False)
    second = purge_credentials(dry_run=False)
    assert second.verified is True
    assert second.env_files_cleared[Path(".env")] == []  # nothing left to clear


def test_os_environ_is_not_consulted(tmp_path, monkeypatch):
    """Verification must read the files, not the process environment.

    A shell that still exports YAHOO_CLIENT_SECRET from before the purge would otherwise make a
    clean file look dirty, or worse, a dirty file look clean.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "nohome"))
    monkeypatch.setattr(purge, "CONFIG_DIR", tmp_path / "noconfig")
    write_env(tmp_path)
    _clear_env_keys(tmp_path / ".env", YAHOO_CREDENTIAL_KEYS, dry_run=False)

    os.environ["YAHOO_CLIENT_SECRET"] = "still-exported-in-this-shell"
    try:
        assert verify_credentials_purged() is True
    finally:
        del os.environ["YAHOO_CLIENT_SECRET"]
