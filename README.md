# Tilastokeskus

A self-hosted fantasy football statistics pipeline. Collects league, roster, matchup, and
transaction data from the Yahoo Fantasy Sports API into PostgreSQL, and visualizes it in Grafana.

Named, with entirely unwarranted institutional gravity, after Finland's national statistics agency.

---

## What it does

Yahoo's fantasy web interface is fine for playing but poor for asking questions across leagues or
across a season. Tilastokeskus pulls the underlying data into a real database so it can be queried,
graphed, and kept.

Tracked per league:

- Standings snapshots by week (rank, record, points for/against, streak, FAAB balance)
- Weekly matchups and results, including projected vs. actual points
- Roster snapshots by week — who was started, who was benched
- Draft results, including auction cost where applicable
- Transactions — adds, drops, trades, waiver claims and FAAB bids
- Player weekly scoring

Everything above is **keyed by week and retroactively fetchable.** Rosters, standings, matchups, and
transactions can all be requested for a completed week, and prior seasons remain available for as
long as Yahoo retains them for the account. A missed collection run is therefore a re-fetch, not a
hole — see [Backfill](#backfill).

## Scope

Eight Yahoo NFL redraft leagues for the 2026 season. Single user, read-only, private.

Redraft only — rosters reset each season, so no keeper or dynasty chains are modeled.

## Stack

| Component | Choice | Notes |
|---|---|---|
| Language | Python 3.13 | venv required — Debian trixie enforces PEP 668 |
| Store | PostgreSQL 17 | native install, not containerized |
| Visualization | Grafana | Postgres data source, native, no plugin |
| Metrics | Prometheus | narrow exporter for collector health only |
| Scheduling | systemd timers | logs to journald, simpler than cron here |
| Host | self-hosted | Debian trixie, LAN only |

**Why Postgres rather than Prometheus for the bulk of the data:** Prometheus is built for numeric
time series with low-cardinality labels. Fantasy data is full of entities and strings — player
names, matchups, transactions — and putting those in Prometheus labels is a cardinality explosion
that would also discard everything non-numeric. Postgres handles tables, joins, and text properly.
Prometheus keeps a narrow role: a handful of numeric trends and the collector health signal.

---

## Setup

### 1. Yahoo API access

Access to the Yahoo Fantasy Sports API requires an application reviewed by the Yahoo Fantasy Sports
team — it is no longer a self-serve permission checkbox. Apply at
<https://sports.yahoo.com/developer/access/>. Personal and single-league use is an anticipated
category; say so plainly rather than presenting a personal project as a commercial one.

Access is **read-only**. Write access is not currently offered.

Once approved, register or link a YDN application with:

- OAuth Client Type: **Confidential Client**
- Redirect URI: `https://localhost:8000`

### 2. Token

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
yahoofantasy login
```

This opens a browser for authorization and writes a token file locally. A certificate warning is
expected — Yahoo requires an HTTPS redirect and the local callback server uses a self-signed cert.
Proceeding past it is fine.

Access tokens expire hourly; the refresh token is long-lived. The collector refreshes
transparently and fails loudly if the refresh token is revoked.

### 3. Database

A fresh cluster has only the `postgres` role, so the whole bootstrap runs as `postgres` in one
session — roles and database creation included.

```bash
sudo apt install postgresql

# Roles. Substitute real passwords.
sudo -u postgres psql -v ON_ERROR_STOP=1 <<'SQL'
CREATE ROLE tilasto_app LOGIN PASSWORD 'CHANGE_ME_APP';
CREATE ROLE tilasto_ro  LOGIN PASSWORD 'CHANGE_ME_RO';
SQL

# Database owned by the app role, so migrations create tables it owns.
sudo -u postgres createdb -O tilasto_app tilastokeskus

# Read-only wiring. This must run BEFORE the first migration.
sudo -u postgres psql -v ON_ERROR_STOP=1 -d tilastokeskus <<'SQL'
GRANT CONNECT ON DATABASE tilastokeskus TO tilasto_ro;
GRANT USAGE ON SCHEMA public TO tilasto_ro;

-- Applies to tables tilasto_app creates from now on — that is, every migration, forever.
ALTER DEFAULT PRIVILEGES FOR ROLE tilasto_app IN SCHEMA public
    GRANT SELECT ON TABLES TO tilasto_ro;

-- Anything already present. None on a fresh cluster; harmless, and correct when re-run.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO tilasto_ro;
SQL
```

Then, as your ordinary user, with `PGPASSWORD` for `tilasto_app` set in `.env`:

```bash
tilasto migrate
```

`tilasto_ro` is the read-only role Grafana connects with. Grafana should never hold write
credentials to this database.

**Do not skip the `ALTER DEFAULT PRIVILEGES` line.** Default privileges apply only to tables created
after they are set, so running it after the migration leaves all ten tables invisible to
`tilasto_ro`. The failure mode is nasty: a one-off `GRANT SELECT ON ALL TABLES` appears to fix it,
and then every future migration adds a table with no grant, blanking individual Grafana panels
without any error.

### 4. Configuration

Config and credentials live in `~/.config/tilastokeskus/`. Nothing in that directory belongs in
version control — see [Security](#security).

---

## Usage

```bash
tilasto leagues                  # list discovered league keys
tilasto collect --all            # full collection run, current week
tilasto collect --league <key>   # single league
tilasto collect --draft-only     # draft results, one-shot per season
tilasto status                   # last run, row counts, staleness
```

### Backfill

Because every table is keyed by week and fetchable retroactively, catching up is a normal command
rather than a recovery procedure:

```bash
tilasto collect --backfill --weeks 1-10 --dry-run   # show the plan, issue nothing
tilasto collect --backfill --weeks 1-10             # completed weeks, all leagues
tilasto collect --backfill --weeks 1-3 --league KEY # one league, a few weeks
tilasto collect --backfill --season 2025            # a prior season, if Yahoo still has it
```

Backfill uses the same idempotent upserts as a live run, so re-running over weeks already collected
is safe and simply refreshes them.

**Start small.** A backfill is the heaviest thing this tool does, Yahoo does not document its rate
limit, and access is granted for personal use. Begin with one league and a few weeks, check that
requests were paced and any throttling was handled, and widen from there — rather than opening with
a full season.

Season defaults to the current season and is never hardcoded; override it with `--season` on any
command.

### Scheduling

Two cadences, both as systemd **user** timers — the collector needs no privilege, so nothing in the
scheduling path needs root:

- **Hourly during game windows** — matchups, standings, live scoring
- **Daily** — rosters, transactions, player metadata

```bash
cp systemd/tilastokeskus-collect.* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now tilastokeskus-collect.timer
sudo loginctl enable-linger "$USER"   # so timers run without an active login
```

Draft data is fetched once per season and skipped thereafter.

**Availability.** The collection host is a multi-boot desktop, not an always-on server, so it only
collects while powered on and booted into Linux. `Persistent=true` fires a missed run at next boot
rather than skipping to the next day, and anything still missed is recoverable by backfill. The
exception is in-season hourly collection: final weekly numbers backfill fine, but intra-game live
scoring cannot be reconstructed after the fact.

All writes are idempotent upserts (`INSERT ... ON CONFLICT DO UPDATE`). Re-running a collection is
always safe.

---

## Monitoring

Every run is recorded to the `collector_runs` table, success or failure. The Prometheus exporter
surfaces:

```
tilasto_collector_last_success_timestamp
tilasto_collector_last_run_status
tilasto_team_wins{league_key,team_key}
tilasto_team_losses{league_key,team_key}
tilasto_team_rank{league_key,team_key}
tilasto_team_points_for{league_key,team_key}
```

Labels are Yahoo keys, never display names. Managers rename teams mid-season, and a label value is
part of a series' identity — a rename orphans the old series and starts a new one, breaking a
rank-over-time graph in half. Keys are stable for a season; Grafana joins the display name from
Postgres for presentation.

The metric that matters most is `tilasto_collector_last_success_timestamp`. A collector that fails
loudly is obvious; one that stops running is not, and a stale dashboard looks much like a quiet
week in the data. Alert on staleness, not just on error. The consequence is recoverable — a
backfill fixes it — but only once someone notices.

Player-level data is deliberately absent from Prometheus. It lives in Postgres and is queried
directly by Grafana.

---

## Security

See [`.gitignore`](.gitignore) — it must be in place **before `yahoofantasy login` is ever run**,
because the token file is written to the working directory by default. The rules that matter most:

```
.env
.venv/
*.token
.yahoofantasy
raw/
```

- Yahoo Client ID and Client Secret are credentials. Treat them as such.
- Config and credentials in `~/.config/tilastokeskus/` sit outside the repository entirely, so no
  ignore rule covers them — keep it that way and never copy them in.
- The OAuth token file grants access to your Yahoo fantasy account. Same.
- Raw API response archives may contain manager names and other league-member information.
- Grafana connects with a read-only role.
- Nothing in this project should be exposed to the public internet.

---

## Attribution and disclaimer

Fantasy data provided by [Yahoo Fantasy](https://football.fantasysports.yahoo.com/).

Yahoo's API terms require this attribution to be displayed wherever the data surfaces, with a link
back to Yahoo Fantasy. It appears here and in the Grafana dashboard description. Do not remove it.

This project is not affiliated with, endorsed by, or sponsored by Yahoo, Yahoo Fantasy Sports, the
NFL, or any of their affiliates. All trademarks belong to their respective owners. Yahoo Fantasy
data is used here under read-only API access for personal, non-commercial purposes and is not
redistributed, resold, or exposed to third parties.

Use of the Yahoo Fantasy Sports API is subject to the
[Yahoo Developer Network Terms of Use](https://developer.yahoo.com/terms/) and the
[Yahoo Terms of Service](https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html). This project's
own code is provided as-is, without warranty of any kind.

---

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest
ruff check tilastokeskus tests
```

Migrations are `.sql` files in `tilastokeskus/migrations/`, applied in filename order and recorded
in a `schema_migrations` table. Each runs in its own transaction, so a failed migration leaves no
partial schema.

## Status

Pre-alpha. Yahoo API access application submitted and under review.

Working: package scaffolding, CLI argument handling, systemd timers, and a live PostgreSQL
database with the schema applied and the read-only Grafana role verified.

The collectors themselves are stubs — they need real API payloads to be written against, and every
one of them fails with an explicit message rather than returning empty data.

Season timing note: as of mid-August 2026, four of eight drafts are complete and the regular season
has not started. Draft results and rosters are available now; matchup and scoring data begins
populating in week 1. Early development targets draft and roster data, which is static and
therefore easier to build against.
