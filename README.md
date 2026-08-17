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

Roster and standings data is **snapshotted weekly and cannot be backfilled.** Yahoo does not expose
historical rosters retroactively, so a missed collection run loses that week permanently. This is
why the collector reports its own health (see [Monitoring](#monitoring)).

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
| Host | `debian-dev` | Debian trixie |

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

```bash
sudo apt install postgresql
sudo -u postgres createdb tilastokeskus
sudo -u postgres psql -c "CREATE ROLE tilasto_app LOGIN PASSWORD '...';"
sudo -u postgres psql -c "CREATE ROLE tilasto_ro  LOGIN PASSWORD '...';"
tilasto migrate
```

`tilasto_ro` is the read-only role Grafana connects with. Grafana should never hold write
credentials to this database.

### 4. Configuration

Config and credentials live in `~/.config/tilastokeskus/`. Nothing in that directory belongs in
version control — see [Security](#security).

---

## Usage

```bash
tilasto leagues                  # list discovered league keys
tilasto collect --all            # full collection run
tilasto collect --league <key>   # single league
tilasto collect --draft-only     # draft results, one-shot per season
tilasto status                   # last run, row counts, staleness
```

### Scheduling

Two cadences, both as systemd timers:

- **Hourly during game windows** — matchups, standings, live scoring
- **Daily** — rosters, transactions, player metadata

Draft data is fetched once per season and skipped thereafter.

All writes are idempotent upserts (`INSERT ... ON CONFLICT DO UPDATE`). Re-running a collection is
always safe.

---

## Monitoring

Every run is recorded to the `collector_runs` table, success or failure. The Prometheus exporter
surfaces:

```
tilasto_collector_last_success_timestamp
tilasto_collector_last_run_status
tilasto_team_wins{league,team}
tilasto_team_losses{league,team}
tilasto_team_rank{league,team}
tilasto_team_points_for{league,team}
```

The metric that matters most is `tilasto_collector_last_success_timestamp`. Because weekly roster
snapshots cannot be backfilled, a silently broken collector is the one failure mode with permanent
consequences. Alert on staleness, not just on error.

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

## Status

Pre-alpha. Yahoo API access application pending.

Season timing note: as of mid-August 2026, four of eight drafts are complete and the regular season
has not started. Draft results and rosters are available now; matchup and scoring data begins
populating in week 1. Early development targets draft and roster data, which is static and
therefore easier to build against.
