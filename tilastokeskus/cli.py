"""Command-line interface.

    tilasto migrate                          apply schema migrations
    tilasto leagues                          list discovered league keys
    tilasto collect --all                    full collection run, current week
    tilasto collect --backfill --weeks 1-10  completed weeks, same code path
    tilasto status                           last run, row counts, staleness

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
from .weeks import WeekRangeError, parse_weeks

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tilasto",
        description="Fantasy football statistics pipeline for Yahoo NFL redraft leagues.",
        epilog="Fantasy data provided by Yahoo Fantasy.",
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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    season = args.season or default_season()

    handlers = {
        "migrate": cmd_migrate,
        "collect": cmd_collect,
        "leagues": cmd_leagues,
        "status": cmd_status,
    }

    try:
        return handlers[args.command](args, season)
    except WeekRangeError as exc:
        print(f"tilasto: {exc}", file=sys.stderr)
        return EXIT_USAGE
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
