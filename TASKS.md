# Tasks

Chronological. Phase 0 is done; phase 1 is the blocking application; phase 2 is everything that can
proceed without a token, and should be worked while phase 1 waits. Phases 3 onward need the token.

Rationale for anything non-obvious lives in `DECISIONS.md`, referenced as D-nn.

Last updated 2026-08-17.

---

## Phase 0 — Repository groundwork · Complete

- [x] Create the GitHub repository `greggjuri/tilastokeskus`, public (D-30)
- [x] Write the README, including attribution and disclaimer (D-31)
- [x] Write `.gitignore` covering credentials, token files, certs, raw archives, and DB dumps (D-28)
- [x] Add `.env.example` documenting required configuration without real values
- [x] Gitignore the project brief — internal host addresses, public repo (D-30)
- [x] Correct the data availability model across the README and brief (D-16 → D-17)

Committed and pushed. Remote holds `README.md`, `.gitignore`, `.env.example`.

---

## Phase 1 — Yahoo API access · Blocking, manual, unknown latency

This gates every task from phase 3 onward. It gates nothing in phase 2.

- [ ] Draft the use-case text: data required, storage, user count, no redistribution (D-26)
- [ ] Save a copy of the submission before sending
- [ ] Submit at <https://sports.yahoo.com/developer/access/>, answering as an individual (D-26)
- [ ] Wait. No data is lost while waiting — everything backfills once the token lands (D-17)

On approval:

- [ ] Register or link the YDN application: Confidential Client, redirect `https://localhost:8000`,
      OpenID Connect and TW Auction both unchecked (D-27)
- [ ] Record `client_id` / `client_secret` into `.env` from `.env.example` (D-28)

---

## Phase 2 — Unblocked work, do this while waiting

Ordered so that each step is testable when the one before it is done.

### Database

- [ ] `sudo apt install postgresql` on the collection host (D-06)
- [ ] `createdb tilastokeskus`
- [ ] Create roles `tilasto_app` (read/write) and `tilasto_ro` (read-only), with real passwords (D-10)
- [ ] Grant `tilasto_ro` SELECT on all tables plus default privileges for future tables — otherwise
      Grafana breaks the next time a table is added
- [ ] Confirm the collection host accepts connections from the Grafana host: `listen_addresses`
      and a `pg_hba.conf` entry scoped to the LAN, not `0.0.0.0/0` (D-04)

### Package scaffolding

- [ ] `python3 -m venv .venv` (D-05)
- [ ] `pyproject.toml` with the `tilasto` console entry point (D-32)
- [ ] Package skeleton: `tilastokeskus/{__init__,cli,config,db,yahoo,collect}.py`
- [ ] Config loading — `.env` and `~/.config/tilastokeskus/`, with season as a parameter
      defaulting to current, never a literal (D-03)
- [ ] CLI skeleton: `leagues`, `collect`, `status`, `migrate` as no-op stubs that parse arguments
      correctly, including `--backfill --weeks` and `--season` from the start (D-18)

### Schema

- [ ] `migrations/001_initial.sql` from the brief's schema — all ten tables (D-11 … D-15)
- [ ] `tilasto migrate` — apply migrations, track applied versions in a table
- [ ] Apply against the real database and confirm every foreign key and index creates cleanly

Treat the migration as provisional until the spike confirms the payload shapes (D-33).

### Scheduling units

- [ ] `tilastokeskus-collect.service` — oneshot, runs the collector as an unprivileged user (D-09)
- [ ] `tilastokeskus-collect.timer` — daily cadence to start with (D-24)
- [ ] Install and enable, with the service still a stub; confirm it fires and journald captures it

Getting the plumbing verified before there is anything to collect means the first real run tests
the collector alone.

---

## Phase 3 — First contact with the API

Begins the moment the token exists.

- [ ] `yahoofantasy login`; accept the expected certificate warning (D-27)
- [ ] Verify: list leagues for `nfl`, 2026 — eight league keys should return. If the 2026 game key
      is not live yet, try 2025 to confirm auth works, then return to 2026
- [ ] **Read-only spike** — dump one league's draft, one roster, and one transaction list to raw
      JSON. Do not write parsing code yet
- [ ] Compare observed payloads against the schema and record every discrepancy (D-33)
- [ ] Revise `001_initial.sql` before it is treated as settled — `transactions.payload`,
      `player_weekly_stats.stats`, `eligible_positions`, `draft_type`, `scoring_type`
- [ ] Confirm real key formats and that keys are stored verbatim, game-id prefix intact (D-11)

---

## Phase 4 — Collector

Build in dependency order; each table's foreign keys require the one before it.

- [ ] Raw response archiving — gzipped, dated, written before parsing (D-20)
- [ ] `collector_runs` logging wrapper around every run, success or failure (D-22)
- [ ] Rate limiting with exponential backoff on 999 and 429 (D-21)
- [ ] Transparent token refresh that fails loudly on revocation (D-29)
- [ ] Collect `leagues` → `teams` → `players` → `draft_picks` → `rosters`, all idempotent
      upserts (D-19)
- [ ] Flag `teams.is_owned_by_me` — it drives dashboard filtering
- [ ] Verify idempotency directly: run twice, confirm row counts are identical

At this point four drafted leagues have real data and four do not. That asymmetry is useful — it
tests the empty-draft path before the season makes it unreachable.

---

## Phase 5 — Backfill

Deliberately before the season starts, because prior seasons are the only complete test data
available (D-17).

- [ ] `tilasto collect --backfill --weeks N-M` over the existing collectors (D-18)
- [ ] `--season` override for prior seasons
- [ ] Backfill a full prior season and confirm it exercises `matchups`, `standings`, and
      `player_weekly_stats` — tables the 2026 season cannot populate until games are played
- [ ] Re-run the same backfill and confirm nothing duplicates (D-19)

---

## Phase 6 — Grafana

- [ ] Add Postgres as a data source on the Grafana host, connecting as `tilasto_ro` (D-10)
- [ ] Dashboard uid `tilastokeskus` (D-32)
- [ ] Draft recap panels — real data, available today
- [ ] Attribution in the dashboard description (D-31)

---

## Phase 7 — In-season collection

Unreachable until week 1 has played, but validated ahead of time by the phase 5 backfill.

- [ ] Collect `matchups`, `standings`, `player_weekly_stats`
- [ ] Hourly timer for game windows, alongside the existing daily timer (D-24)
- [ ] Standings and rank-over-time panels in Grafana
- [ ] Confirm the first live week against Yahoo's own displayed totals — the one check that
      catches scoring misinterpretation

---

## Phase 8 — Observability

- [ ] Prometheus exporter reading `collector_runs`, metrics limited to the agreed list (D-07)
- [ ] Confirm no player names appear in any label (D-08)
- [ ] Scrape config for job `tilastokeskus`
- [ ] Alert on `tilasto_collector_last_success_timestamp` staleness, not only on error (D-23)
- [ ] Collector health panel on the dashboard
- [ ] Verify the alert fires: stop the timer, wait out the threshold, confirm it triggers

An alert that has never fired is a hypothesis, not a safeguard.

---

## Deferred

Not scheduled, recorded so they are not rediscovered as surprises.

- Backup strategy for the database. Backfillable data lowers the stakes, but `collector_runs`
  history and any derived tables are not re-fetchable.
- Retention policy for raw JSON archives — they grow weekly and are never pruned as designed.
- The four undrafted leagues will draft at some point; `--draft-only` needs a re-run per league
  rather than a single season-wide one-shot (D-24).
- Cross-season queries once a second season exists — joining on `player_id`, not `player_key`
  (D-12).
