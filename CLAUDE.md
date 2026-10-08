# CLAUDE.md - Claude Code Instructions

This file provides project-specific instructions and conventions for Claude Code.

## Project Overview

Tilastokeskus: a self-hosted fantasy football statistics pipeline. Collects
league, roster, matchup and transaction data from the Yahoo Fantasy Sports API
into PostgreSQL, and visualizes it in Grafana. Fifteen NFL redraft leagues, one
user, read-only, LAN only.

**Tech Stack**: Grafana | Python 3.13 CLI (`tilasto`) | PostgreSQL 17 | systemd user units

## Quick Commands

```bash
# Always activate first — never install into system Python
source .venv/bin/activate

# Run tests
pytest

# Lint — must be clean before commit
ruff check tilastokeskus tests

# Apply migrations
tilasto migrate

# Collection
tilasto leagues                                   # list discovered league keys
tilasto collect --all                             # full run, current week
tilasto collect --all --dry-run                   # one discovery request, then the plan
tilasto status                                    # last run, row counts, staleness

# Operational
tilasto apicheck                    # probe Yahoo auth, posts to Discord
tilasto purge --data --confirm      # delete all Yahoo data (irreversible)
```

## File Structure

```
tilastokeskus/
├── CLAUDE.md                    # This file
├── README.md                    # Public-facing; carries required attribution
├── docs/
│   ├── PLANNING.md              # Architecture, schema, data model
│   ├── TASK.md                  # Current tasks, phase order
│   ├── DECISIONS.md             # ADRs (D-nn)
│   └── TESTING.md               # Testing standards
├── initials/                    # Feature specifications
│   └── template/
│       └── init-template.md
├── prps/                        # Implementation plans
│   └── template/
│       └── prp-template.md
├── .claude/
│   └── commands/
│       ├── generate-prp.md
│       └── execute-prp.md
├── examples/                    # Code patterns to follow
├── systemd/                     # User units: collect, apicheck
└── tilastokeskus/               # Package
    ├── cli.py                   # argument parsing, subcommands, exit codes
    ├── config.py                # .env + ~/.config/, season resolution
    ├── db.py                    # psycopg3 connection handling
    ├── migrate.py               # migration runner, schema_migrations
    ├── weeks.py                 # week range parsing and validation
    ├── transport.py             # requests, OAuth token refresh, pacing
    ├── yahoo.py                 # Yahoo client — stubs until the collectors land
    ├── apicheck.py              # API access probe, reported to Discord
    ├── ratelimit.py             # backoff, pacing, retry policy
    ├── collect.py               # orchestration, week resolution
    ├── purge.py                 # deletion of Yahoo data and credentials
    └── migrations/
        └── 001_initial.sql
```

Not committed, present locally: `docs/AGREEMENT.md`, `.env`, `raw/`.

## Critical Rules

### 1. File Size Limit
- **Maximum 500 lines per source file** — Python, SQL, unit files, tests
- When approaching limit: split into modules
- Prefer many small files over few large files
- **Documents are exempt.** `docs/DECISIONS.md`, PRPs and init specs are as long
  as their content needs; an append-only decision log is supposed to grow

### 2. Commit Strategy
- **Commit after every feature** - atomic, working commits
- Use conventional commits: `feat:`, `fix:`, `refactor:`, `docs:`, `test:`
- Each commit should leave the package installable and tests passing

### 3. Testing Requirements
- **Unit test coverage**: 80% minimum
- Run tests before committing
- **Test the contract, not only the arithmetic.** See `docs/TESTING.md` — this
  project has already shipped five defects that a thorough arithmetic suite
  missed entirely (D-44)
- Manual verification against Yahoo's own displayed values after collection

### 4. Documentation
- Update `docs/TASK.md` when starting/completing tasks
- Create an ADR in `docs/DECISIONS.md` for architectural choices
- Entries are append-only: a reversed decision is marked **Superseded** and kept,
  because the reasoning that was wrong is usually more useful later than the
  conclusion that replaced it
- Add learnings to relevant docs when debugging issues

### 5. Repository Is Public
- Nothing committed names a host or an address — say "the collection host" and
  "the Grafana host" (D-30)
- Committed files carry no clause text, clause number, territory, deadline,
  retention window or address from the API agreement (D-52)
- Required attribution — "Fantasy data provided by Yahoo Fantasy", linked —
  stays in the README and the Grafana dashboard description (D-31)

## Coding Conventions

### Python

```python
# Type hints everywhere
def resolve_weeks(league: League, requested: WeekRange | None) -> list[int]:
    """Explicit --weeks wins; otherwise the league's own current_week."""

# psycopg 3, no ORM. Explicit SQL, natural keys, named parameters.
# Every write is an idempotent upsert (D-19).
INSERT INTO standings (league_key, team_key, week, rank, ...)
VALUES (%(league_key)s, %(team_key)s, %(week)s, %(rank)s, ...)
ON CONFLICT (team_key, week) DO UPDATE SET
    rank = EXCLUDED.rank, ...

# Unimplemented paths raise with a reason and a pointer — never return empty
raise NotImplementedError(
    "listing league keys is not implemented yet; the collectors are phase 4. "
    "See docs/TASK.md."
)

# Dependencies stay minimal: psycopg[binary], python-dotenv.
# pytest and ruff for development. No ORM, no framework.
```

### SQL / Migrations

```sql
-- Yahoo keys are the primary keys, stored exactly as returned with the
-- game_key prefix intact: 470.l.{id} for 2026, 461 for 2025 (D-11).
-- Never hardcode a game key — read it from the league resource.

-- NUMERIC for points, never FLOAT: decimal PPR plus float arithmetic drifts
-- and makes sums disagree with Yahoo's own totals (D-13).
points NUMERIC(8,2)

-- All timestamps TIMESTAMPTZ, converted from Yahoo's epoch seconds (D-14).
-- Week belongs in the primary key for rosters, standings, matchups (D-15).
PRIMARY KEY (team_key, week)

-- Indexes named explicitly, {table}_{columns}_idx; anonymous indexes are
-- awkward to replace later.
CREATE INDEX matchups_league_week_idx ON matchups (league_key, week);
```

Migrations apply in filename order, one transaction each, recorded in
`schema_migrations` (D-35). Season is a column, never a constant — no literal
year anywhere in the package (D-03, D-37).

### systemd

```ini
# User units, never system units — the collector needs no privilege, and
# nothing committed names a user or a machine (D-34).
# Paths use %h, not a literal home directory.
ExecStart=%h/projects/tilastokeskus/.venv/bin/tilasto collect --all

# Persistent=true on every timer. The collection host is a triple-boot
# desktop, so availability is a property of which OS is booted (D-40).
Persistent=true
```

## Error Handling Patterns

The characteristic failure of this pipeline is **silence**. A stale dashboard
looks much like a quiet week in the data. Prefer loud failure over a plausible
empty result, everywhere.

- **Unimplemented paths raise**, naming what was attempted and where the blocker
  is documented. A stub returning `[]` is indistinguishable from a league with
  no data (D-38)
- **Auth failure surfaces immediately.** 401/403 are never retried; a revoked
  refresh token must be visible, not absorbed into a backoff (D-29)
- **Unobserved cases raise rather than being handled speculatively.** An
  unexpected value is a bug report, not a branch. Observed in `/settings` across
  **all fifteen** leagues (2026-10-08): `scoring_type: head`, `draft_type: live`,
  `is_auction_draft: 0`, `uses_faab: 0`. Points scoring, auction drafts and FAAB
  are absent from real data, so those paths may raise. The sample is fifteen
  leagues in one season — a league joined later is re-checked, not assumed
- **Every retry is logged** with its response code and applied delay. A backoff
  that silently works is indistinguishable from one that never fired (D-21a)
- **Exit codes**: 2 for usage errors, 1 for runtime failures

## Yahoo API Consumption

This project exposes no API; it consumes one. These rules replace the usual
request/response conventions, and are parser-wide rather than per-field.
Observed during the phase 3 spike, 2026-10-07 (D-33).

### Parsing

- **Look fields up by name, never by position.** Responses arrive as lists with
  empty placeholders and no fixed ordering
- **Coerce number types explicitly.** The same field is a number in one record
  and a string in the next
- **Flags arrive as the strings `'0'` and `'1'`** — `is_auction_draft`,
  `uses_faab`, `uses_playoff` and more. `bool('0')` is `True` in Python, so a
  flag is compared to `'1'` after coercion, never tested for truthiness
- **An absent field is not `false`.** `is_owned_by_current_login` is omitted
  entirely on teams you don't own rather than sent as `0`. Assert exactly one
  team per league carries it and fail loudly otherwise — it drives dashboard
  filtering, so silent breakage there is invisible
- **The same key can hold a list or a single object.** An add's player detail is
  a list; a drop's is an object, inside one transaction
- **Some fields need a second fetch.** `draft_type` and `playoff_start_week` come
  from `/settings`, not the league resource
- **Draft picks carry no player names**, so `players` must be populated before
  `draft_picks` can be written
- **`eligible_positions` includes slots like `W/R/T` and `IR`**, so it cannot
  derive `players.position`
- **Roster slots differ between leagues.** Fourteen use `QB RB WR TE W/R/T K DEF
  BN IR`; one adds `Q/W/R/T` and `D`, an individual-defensive-player slot, so
  that league's players will include positions no other league has

### Transport

- **Archive the raw response before parsing** — gzipped and dated. A parsing bug
  then costs a re-parse rather than a re-fetch (D-20)
- **Yahoo's throttle arrives as a non-error status carrying a throttle page**, so
  retry logic keys off `(status, payload)` rather than exceptions. Neither a real
  999 nor a `Retry-After` header has been observed yet
- **Current week comes from the league resource, per league, read fresh each
  run.** Never computed from a date: Yahoo's rollover is Tuesday, in a timezone
  that is not the host's, and drifts around holidays. A wrong week number writes
  cleanly because upserts are keyed on week, so it corrupts silently (D-24a)
- **Explicit `--weeks` bypasses current-week resolution entirely.** Backfill is
  the same code path as a live run, not a separate importer (D-18)

## PRP Workflow

### Naming and numbering
`initials/init-{nn}-{slug}.md` and `prps/prp-{nn}-{slug}.md` — always a matched
pair, the PRP inheriting the init spec's number and slug.

Numbers are assigned in the order work is **taken up**, not the order specs are
written; a backlog item has a slug but no number until then. **Numbers are never
reused** — an abandoned spec burns its number, and the gap is the record that
something was tried and dropped. The next free number lives in `docs/TASK.md`
under **Spec Numbering**: claim it, increment it there, then write the file.

### Generating PRPs
```bash
/generate-prp initials/init-{nn}-{slug}.md
```

This command:
1. Reads project documentation
2. Reads the init specification
3. Researches existing codebase
4. Generates comprehensive PRP
5. Scores confidence and lists concerns

### Executing PRPs
```bash
/execute-prp prps/prp-{nn}-{slug}.md
```

This command:
1. Reads the PRP
2. Executes each step sequentially
3. Runs tests after each step
4. Reports progress
5. Handles errors gracefully

Do not proceed on a PRP scoring below 7. Answer the open questions in the init
spec first.

## Common Patterns

### Database Access
psycopg 3 directly, no ORM. Every write is `INSERT ... ON CONFLICT ... DO UPDATE`
so re-running any collection is safe and never duplicates (D-19). The collector
connects as `tilasto_app`; Grafana connects separately as `tilasto_ro` and never
holds write credentials (D-10).

### Authentication
OAuth 2.0 against a Confidential Client. Access tokens expire hourly; the
refresh token is long-lived and lives in `.env` as `YAHOO_REFRESH_TOKEN`. The
transport refreshes transparently and fails loudly on revocation (D-29).
Credentials live in `.env` or `~/.config/tilastokeskus/`, both ignored (D-28).

### Scheduling
One cadence, daily (D-53). systemd user timers with `Persistent=true`, so a
missed run fires at next boot rather than being skipped.

### Caching
The gzipped raw archive under `raw/` is the only cache, and it exists so a
parsing bug costs a re-parse rather than a re-fetch (D-20). Nothing else caches:
every table is week-keyed and retroactively fetchable, so a missed run is a
re-fetch, not a hole (D-17).

### Presentation vs Collection
`leagues.tier` distinguishes leagues followed closely from bulk leagues. It is
**presentation only** — it does not affect what is collected, how often, or in
what order. The name invites the opposite assumption (D-54).

## Cost Awareness

Not billed, but rate-limited, and access was granted on the basis of modest
personal use. The cost of being wrong is asymmetric: unremarkable if it works,
disproportionate if it draws throttling or review.

- Exponential backoff on 999 and 429, plus a conservative inter-request delay
  (D-21, D-21a)
- Yahoo does not document its limit, so the safe rate is unknown rather than
  merely unenforced. Pace conservatively; tighten later, with evidence
- Fifteen leagues once daily is the steady-state volume
- **Backfill escalates, never opens wide**: one league / two-to-three weeks →
  read the retry log → one league / full season → re-run to prove idempotency →
  only then widen (D-21a)
- `--dry-run` issues exactly one request — discovery, archived like any other —
  then prints the league keys and the planned request count. It writes no row
  and no `collector_runs` entry. A plan that cannot name the real leagues is
  barely a plan, so it costs one call (PRP-01)
- Every collection prints the requests it issued and the retry log, empty or not
- Nothing throttling is a data point, not a pass — it means the limiter is
  untested, not that it works

## Debugging Checklist

When something isn't working:

1. **Check the unit**: `systemctl --user status tilastokeskus-collect.service`,
   then `journalctl --user -u tilastokeskus-collect -n 50`
2. **Check `collector_runs`**: every run is logged, success or failure (D-22)
3. **Check the raw archive**: `raw/{date}/` holds the payload before parsing, so
   a parse bug is reproducible without another API call
4. **Check auth separately**: `tilasto apicheck`. Token mints and refreshes fine
   while the API still rejects the app — those are different failures
5. **Check the timer actually fired**: the host is a triple-boot desktop. A gap
   usually means it was booted into another OS, not that anything broke (D-40)
6. **Review recent changes**: git log, and `docs/DECISIONS.md` for whether the
   behaviour is deliberate

### Known gotchas

- **`DROP DATABASE` takes `pg_default_acl` with it.** Re-apply `ALTER DEFAULT
  PRIVILEGES` *before* the migration, or `tilasto_ro` silently loses access to
  every table a future migration adds (D-41)
- **`yahoofantasy` cannot log in on Python 3.12+** — it calls `ssl.wrap_socket`,
  removed in 3.12. The README documents the manual code exchange instead
- **Refresh tokens are bound to the client that minted them.** Changing the Yahoo
  app means re-authorizing, not refreshing

## DO NOT

- Commit secrets, API keys, the token file, or the raw archive
- Commit `docs/AGREEMENT.md`, or quote its contents into a tracked file (D-30, D-52)
- Name a host or an address in a committed file (D-30)
- Use `--break-system-packages` or install outside `.venv` (D-05)
- Hardcode a game key, a season, or a week number (D-03, D-11, D-24a)
- Use `FLOAT` for points (D-13)
- Put player names, or any human-editable name, in a Prometheus label — labels
  are keys only (D-08, D-08a)
- Return an empty result where a raise belongs (D-38)
- Skip tests to save time
- Create source files over 500 lines
- Contradict existing ADRs without discussion

## Reference Documents

- `docs/PLANNING.md` - Architecture and data models
- `docs/DECISIONS.md` - Past decisions to respect
- `docs/TASK.md` - Current work status
- `docs/TESTING.md` - Testing standards
- `examples/` - Code patterns to follow
