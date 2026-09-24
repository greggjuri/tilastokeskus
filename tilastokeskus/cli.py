"""Command-line interface.

    tilasto migrate                          apply schema migrations
    tilasto leagues                          list discovered league keys
    tilasto collect --all                    full collection run, current week
    tilasto collect --backfill --weeks 1-10  completed weeks, same code path
    tilasto status                           last run, row counts, staleness
    tilasto purge --data                     delete collected rows and the raw archive
    tilasto purge --credentials              delete the token file and the Yahoo keys

Season is a parameter everywhere, defaulting to the current season rather than a hardcoded
year (DECISIONS.md D-03).
"""

from __future__ import annotations

import argparse
import sys

from . import __version__, migrate
from .collect import CollectionPlan
from .config import default_season, load_settings
from .db import DatabaseUnavailable
from .purge import PurgeVerificationFailed
from .weeks import WeekRangeError, parse_weeks

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tilasto",
        description="Fantasy football statistics pipeline for Yahoo NFL redraft leagues.",
        epilog="Fantasy data provided by Yahoo Fantasy — https://football.fantasysports.yahoo.com/",
    )
    parser.add_argument("--version", action="version", version=f"tilastokeskus {__version__}")
    parser.add_argument(
        "--season",
        type=int,
        metavar="YEAR",
        help=f"season to operate on (default: {default_season()})",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("leagues", help="list discovered league keys")

    collect = sub.add_parser("collect", help="run a collection")
    target = collect.add_mutually_exclusive_group()
    target.add_argument("--all", action="store_true", help="every league in the season")
    target.add_argument("--league", metavar="KEY", action="append", help="a single league key")
    collect.add_argument(
        "--draft-only", action="store_true", help="draft results only; skipped if present"
    )
    collect.add_argument(
        "--backfill", action="store_true", help="collect an explicit week range rather than current"
    )
    collect.add_argument(
        "--weeks", metavar="RANGE", help="weeks to collect, e.g. '1-10' or '1,3,5' (implies backfill)"
    )
    collect.add_argument(
        "--dry-run",
        action="store_true",
        help="describe the plan without issuing any API request",
    )

    status = sub.add_parser("status", help="last run, row counts, staleness")
    status.add_argument("--json", action="store_true", help="machine-readable output")

    migrate_cmd = sub.add_parser("migrate", help="apply schema migrations")
    migrate_cmd.add_argument(
        "--dry-run", action="store_true", help="list pending migrations without applying"
    )

    # Deletion is split in two on purpose (D-50). --data and --credentials are independent, and
    # neither implies the other: clearing the collection must not cost the Postgres password, and
    # revoking access must not throw away the data. There is no flag that means "both" by default.
    purge_cmd = sub.add_parser("purge", help="delete Yahoo data and/or credentials (D-50)")
    purge_cmd.add_argument(
        "--data", action="store_true", help="collected rows and the raw response archive"
    )
    purge_cmd.add_argument(
        "--credentials",
        action="store_true",
        help="token file and the Yahoo keys in .env; other keys are left untouched",
    )
    purge_cmd.add_argument(
        "--confirm", action="store_true", help="actually delete; without it this is a dry run"
    )

    return parser


def plan_from_args(args: argparse.Namespace, season: int) -> CollectionPlan:
    """Turn parsed arguments into a CollectionPlan, or raise WeekRangeError."""
    weeks = parse_weeks(args.weeks) if args.weeks else None

    if args.backfill and weeks is None:
        raise WeekRangeError("--backfill requires --weeks")

    return CollectionPlan(
        season=season,
        league_keys=args.league,
        weeks=weeks,
        draft_only=args.draft_only,
    )


def cmd_migrate(args: argparse.Namespace, season: int) -> int:
    settings = load_settings(season)
    pending = migrate.run(settings, dry_run=args.dry_run)

    if not pending:
        print("schema is up to date")
    elif args.dry_run:
        print(f"pending ({len(pending)}):")
        for version in pending:
            print(f"  {version}")
    else:
        for version in pending:
            print(f"applied {version}")
    return EXIT_OK


def cmd_collect(args: argparse.Namespace, season: int) -> int:
    from .collect import run  # imported late so `migrate` works without a Yahoo client

    plan = plan_from_args(args, season)

    if args.dry_run:
        print(f"would collect: {plan.describe()}")
        print("no API request issued")
        # Request volume depends on team counts, which are not known until the leagues are
        # fetched. Deliberately not estimated here rather than guessed (D-21a).
        return EXIT_OK

    print(f"collecting: {plan.describe()}")
    run(load_settings(season), plan)
    return EXIT_OK


def cmd_leagues(args: argparse.Namespace, season: int) -> int:
    from .yahoo import YahooClient

    for key in YahooClient(load_settings(season)).league_keys(season):
        print(key)
    return EXIT_OK


def cmd_status(args: argparse.Namespace, season: int) -> int:
    raise NotImplementedError("status is not implemented yet")


def cmd_purge(args: argparse.Namespace, season: int) -> int:
    from .purge import purge_credentials, purge_data

    if not (args.data or args.credentials):
        print(
            "tilasto: purge needs --data, --credentials, or both.\n"
            "  --data         collected rows and the raw response archive\n"
            "  --credentials  token file and the Yahoo keys in .env\n"
            "Nothing is deleted by default, and neither flag implies the other (D-50).",
            file=sys.stderr,
        )
        return EXIT_USAGE

    dry_run = not args.confirm
    if dry_run:
        print("DRY RUN — nothing is deleted. Re-run with --confirm to delete.\n")

    if args.data:
        report = purge_data(load_settings(season), dry_run=dry_run)
        verb = "would delete" if dry_run else "deleted"
        print(f"data: {verb} {report.total_rows} row(s) across {len(report.rows_deleted)} table(s)")
        for table, count in sorted(report.rows_deleted.items()):
            if count:
                print(f"  {table}: {count}")
        print(f"      {verb} {report.archive_files_deleted} raw archive file(s) "
              f"from {report.archive_path}")
        if not dry_run:
            print("      verified: every purged table re-counted at 0, archive gone")

    if args.credentials:
        report = purge_credentials(dry_run=dry_run)
        verb = "would delete" if dry_run else "deleted"
        for path in report.token_files_deleted:
            print(f"credentials: {verb} token file {path}")
        if not report.token_files_deleted:
            print("credentials: no token file found")
        for path, cleared in report.env_files_cleared.items():
            verb_c = "would clear" if dry_run else "cleared"
            print(f"      {verb_c} {len(cleared)} Yahoo key(s) in {path}"
                  + (f": {', '.join(cleared)}" if cleared else ""))
            preserved = report.preserved_keys.get(path, [])
            print(f"      left {len(preserved)} other key(s) untouched"
                  + (f": {', '.join(preserved)}" if preserved else ""))
        if not dry_run:
            print("      verified: no token file, no Yahoo key holds a value")

    if not dry_run:
        print("\nRemaining by hand, not covered here: database dumps, Grafana cached query "
              "results, and any payload copied elsewhere during a spike (D-49, D-50).")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    season = args.season or default_season()

    handlers = {
        "migrate": cmd_migrate,
        "collect": cmd_collect,
        "leagues": cmd_leagues,
        "status": cmd_status,
        "purge": cmd_purge,
    }

    try:
        return handlers[args.command](args, season)
    except WeekRangeError as exc:
        print(f"tilasto: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except PurgeVerificationFailed as exc:
        print(f"tilasto: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except DatabaseUnavailable as exc:
        print(f"tilasto: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except NotImplementedError as exc:
        print(f"tilasto: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("tilasto: interrupted", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
