# Tilastokeskus - Project Planning

## Project Vision

Yahoo's fantasy web interface is fine for playing but poor for asking questions
across leagues or across a season. Tilastokeskus pulls the underlying data for
fifteen NFL redraft leagues into a real database so it can be queried, graphed
and kept.

Single user, read-only, private, non-commercial, LAN only. Named, with entirely
unwarranted institutional gravity, after Finland's national statistics agency.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                   YAHOO FANTASY SPORTS API                          │
│              fantasysports.yahooapis.com/fantasy/v2                 │
│          OAuth 2.0 Confidential Client · read-only · 15 leagues     │
└─────────────────────────────────────────────────────────────────────┘
                              │  HTTPS, paced + backed off
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    COLLECTOR  (collection host)                     │
│                    tilasto CLI · Python 3.13 · .venv                │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐  ┌──────────┐            │
│  │ transport│→ │ raw      │→ │  parse    │→ │  upsert  │            │
│  │ + retry  │  │ archive  │  │ by name   │  │ psycopg3 │            │
│  └──────────┘  └──────────┘  └───────────┘  └──────────┘            │
│         driven by systemd user timer, daily, Persistent=true        │
└─────────────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
┌─────────────────────────┐      ┌──────────────────────────┐
│   PostgreSQL 17         │      │  raw/{date}/*.json.gz    │
│   (collection host)     │      │  gzipped, dated, local   │
│   10 tables + migrations│      │  gitignored              │
│   tilasto_app  (rw)     │      └──────────────────────────┘
│   tilasto_ro   (ro)     │
└─────────────────────────┘
         │                                   │
         │ LAN, scram-sha-256                │ collector_runs
         ▼                                   ▼
┌─────────────────────────┐      ┌──────────────────────────┐
│   GRAFANA               │◀─────│  PROMETHEUS EXPORTER     │
│   (Grafana host)        │      │  collector health only   │
│   uid: tilastokeskus    │      │  labels = keys, never    │
│   Postgres data source  │      │  display names           │
│   native, no plugin     │      └──────────────────────────┘
└─────────────────────────┘
```

Nothing in this system is exposed to the public internet (D-04).

## Cost

**$0/month.** Entirely self-hosted on existing hardware; the Yahoo API is free
under the approved personal-use agreement.

The real budget is **request volume**, not money. Access was granted on the basis
of modest personal use, Yahoo does not document its rate limit, and the cost of
exceeding it is asymmetric — unremarkable if it works, disproportionate if it
draws throttling or review.

### Protection strategies
1. Exponential backoff on 999 and 429, with a conservative inter-request delay (D-21, D-21a)
2. One cadence, daily — fifteen leagues once per day is the steady-state volume (D-53)
3. Raw archive, so a parsing bug costs a re-parse rather than a re-fetch (D-20)
4. `--dry-run` issues one discovery request, prints the plan with its request count, and writes nothing
5. Backfill escalates rather than opening wide (D-21a)

## Tech Stack

### Collector
- **Runtime**: Python 3.13 in `.venv` — Debian trixie enforces PEP 668 (D-05)
- **Database driver**: psycopg 3, no ORM (D-39)
- **Config**: python-dotenv, `.env` + `~/.config/tilastokeskus/`
- **Entry point**: `tilasto` console script
- **Lint/test**: ruff, pytest

### Store
- **Database**: PostgreSQL 17, native install, not containerized (D-06)
- **Roles**: `tilasto_app` (read/write, collector), `tilasto_ro` (read-only, Grafana) (D-10)

### Visualization
- **Grafana**, Postgres data source, native, no plugin
- **Prometheus**, narrow exporter for collector health only (D-07)

### Infrastructure
- **Scheduling**: systemd *user* timers, `Persistent=true` (D-09, D-34, D-40)
- **Version control**: GitHub, public repository (D-30)
- **CI/CD**: none — single host, no deployment step

## Data Models

Ten tables plus `schema_migrations`. Yahoo keys are the primary keys, stored
exactly as returned with the `game_key` prefix intact — `470.l.{id}` for 2026,
`461` for 2025. Never hardcode a game key (D-11).

Revised 2026-10-07 against the spike payloads (D-33). `standings`, `matchups` and
`player_weekly_stats` were not fetched by the spike and are still unvalidated.
No CHECK constraints on Yahoo enumerations: an unobserved value raises in the
collector with a reason (D-38) rather than failing at insert.

### leagues
```
PK: league_key            TEXT        '470.l.123456'
    season                INT         never a constant (D-03)
    name, num_teams
    scoring_type          TEXT        'head' observed in all 15
    current_week          INT         source of truth for "what week is it" (D-24a)
    start_week, end_week
  from /settings, not the league resource — NULL until fetched, never defaulted:
    draft_type            TEXT        'live' in all 15
    is_auction_draft      BOOLEAN     its own field, not a draft_type value;
                                      '0' in all 15
    playoff_start_week    INT
    settings_fetched_at   TIMESTAMPTZ /settings fetched once per season; NULL = not yet
    tier                  TEXT        presentation only, never affects collection (D-54)
    is_finished           BOOLEAN
    updated_at            TIMESTAMPTZ
```

### teams
```
PK: team_key              TEXT        '470.l.123456.t.4'
FK: league_key → leagues
    team_id, name, manager_name, logo_url
    is_owned_by_me        BOOLEAN     from is_owned_by_current_login, which is
                                      ABSENT on teams you don't own, not 0
    draft_position, draft_grade, has_draft_grade, draft_recap_url
                                      fixed once drafted, so here and not in the
                                      weekly standings snapshot
    updated_at            TIMESTAMPTZ
INDEX: (league_key)
UNIQUE INDEX: (league_key) WHERE is_owned_by_me — at most one owned team per
       league. "At least one" is not expressible; the collector asserts it
```

### standings
```
PK: (team_key, week)      snapshot per week, not one mutable row, so rank over
                          time can be graphed at all (D-15)
FK: league_key, team_key
    rank, wins, losses, ties
    points_for, points_against    NUMERIC(8,2) (D-13)
    streak_type, streak_value         TEXT, INT — Yahoo returns {type, value}
    playoff_seed          INT
    clinched_playoffs     BOOLEAN
    waiver_priority, faab_balance
    number_of_moves, number_of_trades INT — change weekly, so snapshotted here
    captured_at           TIMESTAMPTZ
```

### matchups
```
PK: (team_key, week)
FK: league_key, team_key, opponent_key → teams
    points, projected_points      NUMERIC(8,2)
    result                TEXT        derived from winner_team_key, not fetched
    is_playoffs, is_consolation   BOOLEAN
    updated_at            TIMESTAMPTZ
INDEX: (league_key, week)
```

### players
```
PK: player_key            TEXT        '470.p.31002' — season-scoped
    player_id             INT         cross-season identity; any multi-season
                                      join uses this, not player_key (D-12)
    full_name, nfl_team, bye_week     league-independent only — one row
                                      serves every league that drafted them
    position              TEXT        display_position verbatim: 'LB', 'DT,DE'
    nfl_positions         TEXT[]      display_position split: {DT,DE}. Real
                                      positions, never roster slots. Renamed from
                                      eligible_positions by migration 002
    updated_at            TIMESTAMPTZ
```

### rosters
```
PK: (team_key, week, player_key)
FK: league_key, team_key, player_key
    selected_position     TEXT        'QB', 'BN', 'IR', …
    is_starting           BOOLEAN     selected_position NOT IN ('BN','IR')
    captured_at           TIMESTAMPTZ
INDEX: (league_key, week)
```

### player_weekly_stats
```
PK: (league_key, player_key, week)    scoring is league-specific
FK: league_key, player_key
    points, projected_points      NUMERIC(8,2)
    stats                 JSONB       raw stat map, varies by position
    updated_at            TIMESTAMPTZ
```

### draft_picks
```
PK: (league_key, pick)
FK: league_key, team_key, player_key
    round, cost           cost is auction only — no league is auction
                          (is_auction_draft '0' in all 15), so cost stays NULL
NOTE: picks carry only pick, round, team key and player key — no names, so
      players must be populated first
```

### transactions
```
PK: transaction_key       TEXT
FK: league_key
    type                  TEXT        'add/drop' is the commonest (78 of 110
                                      observed); 'commish' never appeared
    status, timestamp     TIMESTAMPTZ converted from epoch seconds (D-14)
    faab_bid              INT
    payload               JSONB       the whole transaction, kept deliberately.
                                      Shape varies: an add's player detail is a
                                      list, a drop's an object, same txn.
                                      A child table later needs no re-fetch
    updated_at            TIMESTAMPTZ
INDEX: (league_key, timestamp DESC)
```

### collector_runs
```
PK: id                    BIGSERIAL
    started_at, finished_at       TIMESTAMPTZ
    status                TEXT        'success' | 'partial' | 'failed'
    leagues_synced, rows_written  INT
    error                 TEXT        may capture payload fragments, so it is
                                      purged with the data (D-50)
```

This is the only table backfill cannot reconstruct.

## Yahoo API Endpoints Consumed

Read-only. No endpoints are exposed by this project.

| Resource | Path | Notes |
|---|---|---|
| Discovery | `/users;use_login=1/games;game_codes=nfl;seasons={yyyy}/leagues` | game key **and** league keys in one call, any season (D-57) |
| Settings | `/league/{league_key}/settings` | `draft_type`, `is_auction_draft`, `playoff_start_week`; first collection only |
| Teams | `/league/{league_key}/teams` | carries the league metadata, `current_week` included (D-24a, D-57) |
| Standings | `/league/{league_key}/standings` | |
| Scoreboard | `/league/{league_key}/scoreboard;week={n}` | matchups |
| Draft | `/league/{league_key}/draftresults/players` | picks with players nested; once per league, gated on stored picks (D-59) |
| Transactions | `/league/{league_key}/transactions` | |
| Roster | `/team/{team_key}/roster;week={n}` | week param is what makes backfill possible (D-17) |

Not called: `/league/{league_key}` and `/game/nfl` — the first's metadata arrives
with every teams payload, the second's game key with discovery (D-57).
`/players;player_keys=` — it silently truncates to 25 players (D-59).

### Authentication
OAuth 2.0, Confidential Client, redirect `https://localhost:8000`. Access tokens
expire hourly; the refresh token is long-lived and lives in `.env` as
`YAHOO_REFRESH_TOKEN`. Refresh is transparent and fails loudly on revocation
(D-29). Refresh tokens are bound to the client that minted them.

### Error responses
Yahoo returns XML (`?format=json` available). Auth failures arrive as
`<yahoo:error><yahoo:description>…`. Throttling arrives as a **non-error status
carrying a throttle page**, so retry logic keys off `(status, payload)` rather
than exceptions.

## Project Structure

```
tilastokeskus/
├── CLAUDE.md                    # Project rules for Claude Code
├── README.md                    # Public-facing, carries attribution
├── docs/
│   ├── PLANNING.md              # This file
│   ├── TASK.md                  # Current tasks
│   ├── DECISIONS.md             # Architecture decisions (D-nn)
│   └── TESTING.md               # Testing standards
├── initials/                    # Feature specifications (init-*.md)
│   └── template/init-template.md
├── prps/                        # Implementation plans (prp-*.md)
│   └── template/prp-template.md
├── .claude/commands/            # generate-prp.md, execute-prp.md
├── examples/                    # Code patterns for Claude Code
├── systemd/                     # User units: collect, apicheck
└── tilastokeskus/
    ├── cli.py  config.py  db.py  migrate.py  weeks.py
    ├── transport.py  yahoo.py  ratelimit.py  collect.py
    ├── apicheck.py  purge.py
    └── migrations/001_initial.sql
```

Local, never committed: `docs/AGREEMENT.md`, `.env`, `raw/`.

## Development Phases

Detailed checkboxes live in `docs/TASK.md`. This is the shape.

### Phase 0-2: Foundation · Complete
Repository, gitignore before any token existed, package scaffolding, CLI with
`--backfill --weeks` from the first commit, schema migration, systemd units,
Postgres with read-only role and default privileges proven.

### Phase 3: First contact · Complete
OAuth, league listing, read-only spike. Fifteen leagues, game_key 470, ten
schema differences recorded against the real payloads.

### Phase 4: Collector · Next
Raw archiving → `collector_runs` wrapper → rate limiting → `leagues` → `teams` →
`players` → `draft_picks` → `rosters`, all idempotent upserts, built in
foreign-key order.

### Phase 5: Backfill
Escalating, never a single sweep: one league / few weeks → read the retry log →
one league / full season → re-run for idempotency → widen (D-21a).

### Phase 6: Grafana
Postgres data source as `tilasto_ro`, dashboard uid `tilastokeskus`, season
template variable, attribution in the description.

### Phase 7: In-season collection
`matchups`, `standings`, `player_weekly_stats`. First live week verified against
Yahoo's own displayed totals — the one check that catches scoring
misinterpretation.

### Phase 8: Observability
Prometheus exporter over `collector_runs`, staleness alerting, verified by
actually stopping the timer and waiting it out.

### Future
Backup strategy. Raw-archive retention. Cross-season queries once a second
season exists, joining on `player_id`.

## Key Constraints

1. **Read-only API access** — write access is not offered by Yahoo, so nothing
   is designed around it (D-25)
2. **Public repository** — nothing committed names a host, an address, or any
   term of the API agreement (D-30, D-52)
3. **Rate limit is undocumented** — the safe request rate is unknown rather than
   merely unenforced (D-21)
4. **The collection host is a triple-boot desktop, not a server** — availability
   is a property of which OS is booted. `Persistent=true` plus backfill covers
   it (D-40)
5. **500-line file limit** — split into modules when approaching
6. **Commit after each feature** — atomic, working commits
7. **Redraft only** — rosters reset each season; no keeper or dynasty chains
   modeled (D-02)
8. **Deletion obligation** — `tilasto purge` exists so the agreement's deletion
   requirement is executable rather than theoretical (D-50)

## Success Criteria

1. [ ] All fifteen leagues collect on a daily timer without manual intervention
2. [ ] Re-running any collection changes no row counts (idempotency proven, not assumed)
3. [ ] A missed day closes itself — backfill reproduces the gap exactly
4. [ ] Grafana shows standings and rank-over-time for the current season, tiered
       leagues distinguishable at a glance
5. [ ] Week 1 totals match Yahoo's own displayed values exactly
6. [ ] A broken collector is visible within one alert threshold, not discovered
       via a stale dashboard
7. [ ] `tilasto purge --data --confirm` verifiably empties every table and the
       raw archive

## Non-Functional Requirements

### Performance
Not a constraint. A full collection run across fifteen leagues is minutes, has
no deadline, and is deliberately paced slower than necessary. Query performance
matters only to Grafana panel load, which is a handful of indexed reads.

### Security
- Nothing exposed to the public internet; Postgres listens on the LAN only,
  `pg_hba.conf` scoped to the subnet with `scram-sha-256`, never `0.0.0.0/0`
- Grafana connects read-only and never holds write credentials
- Credentials in `.env` or `~/.config/tilastokeskus/`, both ignored, with ignore
  rules committed *before* any token existed (D-28)
- Raw archives may contain manager names and other league-member information
- Breach notification obligations are recorded in `AGREEMENT.md`, local only

### Reliability
- No uptime target. The host is a desktop and gaps are expected (D-40)
- Every table is week-keyed and retroactively fetchable, so a missed run is a
  re-fetch, not a hole (D-17)
- Alert on **staleness**, not only on error — a collector that stops running
  looks exactly like a quiet week in the data (D-23)
- Backup strategy is deferred and acknowledged: `collector_runs` history is the
  only thing not re-fetchable

### Scalability
Explicitly not a goal. Fifteen leagues, one user, one season at a time. Season
is a column rather than a constant so next August is a new row rather than a
migration (D-03) — that is the only growth the design accommodates.
