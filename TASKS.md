# Tasks

Chronological. Phase 0 is done; phase 1 is the blocking application; phase 2 is everything that can
proceed without a token, and should be worked while phase 1 waits. Phases 3 onward need the token.

Rationale for anything non-obvious lives in `DECISIONS.md`, referenced as D-nn.

Last updated 2026-08-17.

**Now:** application submitted and waiting. **Phase 2 is complete** — database live, schema
migrated, read-only role verified, timers enabled with lingering. Everything remaining needs
the Yahoo token, except the rate-limiter backoff and its tests, which do not.

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

## Phase 1 — Yahoo API access · Submitted 2026-08-17, awaiting review

This gates every task from phase 3 onward. It gates nothing in phase 2.

- [x] Draft the use-case text: data required, storage, user count, no redistribution (D-26)
- [x] Submit at <https://sports.yahoo.com/developer/access/>, answering as an individual (D-26)
- [ ] Wait. No data is lost while waiting — everything backfills once the token lands (D-17)

On approval:

- [ ] Register or link the YDN application: Confidential Client, redirect `https://localhost:8000`,
      OpenID Connect and TW Auction both unchecked (D-27)
- [ ] Record `client_id` / `client_secret` into `.env` from `.env.example` (D-28)

---

## Phase 2 — Unblocked work, do this while waiting

Ordered so that each step is testable when the one before it is done.

### Package scaffolding · Complete

- [x] `python3 -m venv .venv` (D-05)
- [x] `pyproject.toml` with the `tilasto` console entry point (D-32)
- [x] Package skeleton: `tilastokeskus/{__init__,cli,config,db,migrate,weeks,yahoo,collect}.py`
- [x] Config loading — `.env` and `~/.config/tilastokeskus/`, season computed rather than
      hardcoded, overridable by `--season` (D-03, D-37)
- [x] CLI: `leagues`, `collect`, `status`, `migrate`, parsing `--backfill --weeks` and `--season`
      from the outset (D-18, D-36)
- [x] Stubs fail loudly with a reason and a pointer, never silently (D-38)
- [x] Tests for week parsing and season rollover; `ruff` clean, 22 tests passing

Verified: argument handling, mutually exclusive `--all` / `--league`, week-range rejection,
and that the Postgres connection error names the target without leaking the password.

### Schema · Applied

- [x] `tilastokeskus/migrations/001_initial.sql` — all ten tables, indexes named explicitly
      (D-11 … D-15, D-35)
- [x] `tilasto migrate` — applies in filename order, tracks versions, one transaction each
- [x] Applied against the real database; every foreign key and index created cleanly

Treat the migration as provisional until the spike confirms the payload shapes (D-33). Because
the tables are empty, revising `001_initial.sql` and recreating the database is still cheaper
than writing a `002` migration — that stops being true the moment real data lands.

### Scheduling units · Complete

- [x] `tilastokeskus-collect.service` — oneshot, user unit, no privilege (D-09, D-34)
- [x] `tilastokeskus-collect.timer` — daily, `Persistent=true`, randomized delay (D-24)
- [x] Installed to `~/.config/systemd/user/`, enabled, and test-fired with the collector still
      a stub — confirmed the timer schedules, the service runs the venv CLI, it fails loudly,
      and journald captures the reason

Getting the plumbing verified before there is anything to collect means the first real run tests
the collector alone.

- [ ] `loginctl enable-linger` so timers run without an active login — needs sudo

### Database · Complete

**One privileged session, not a handoff partway through** (D-41). A fresh cluster has only the
`postgres` role, so role creation and `createdb` need `sudo -u postgres` just as much as the
install does. The full command block is in the README under Setup → Database.

- [x] `sudo apt install postgresql` (D-06)
- [x] Create roles `tilasto_app` and `tilasto_ro` with real passwords (D-10)
- [x] `createdb -O tilasto_app tilastokeskus` — app role owns the database, which is what lets it
      create tables under Postgres 15+ schema rules (D-41)
- [x] `GRANT CONNECT` / `GRANT USAGE`, then **`ALTER DEFAULT PRIVILEGES FOR ROLE tilasto_app`
      before the first migration** (D-41)
- [x] `tilasto_app` password in `.env`; TCP connection confirmed
- [x] `sudo loginctl enable-linger` so user timers survive logout (D-34)

Then, with no further sudo:

- [x] `tilasto migrate` — `001_initial` applied; re-running reports "schema is up to date"
- [x] 10 schema tables plus `schema_migrations`, all owned by `tilasto_app`; 23 indexes,
      15 foreign keys, all created cleanly
- [x] `tilasto_ro` has SELECT on all 11 tables and no INSERT, UPDATE, or DELETE on any;
      `CONNECT` and schema `USAGE` yes, schema `CREATE` no
- [x] **Default privileges proven against a future table**, not just the existing ones: a table
      created by `tilasto_app` after the fact was readable by `tilasto_ro` immediately and not
      writable. This is the check that distinguishes working default privileges from a one-off
      `GRANT SELECT ON ALL TABLES` that would leave the next migration ungranted (D-41)

Still requiring root, not needed until Grafana connects in phase 6:

- [ ] `listen_addresses` and a `pg_hba.conf` entry scoped to the LAN, not `0.0.0.0/0` (D-04)

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
- [ ] Confirm real key formats and that keys are stored verbatim, game_key prefix intact (D-11)
- [ ] **Record the real `game_key` integers** and correct the illustrative examples in
      `DECISIONS.md`, `001_initial.sql`, and the brief. `461`/`449` are guesses (D-11)
- [ ] Confirm `current_week` is present on the league resource and note whether it differs
      between the eight leagues (D-24a)

---

## Phase 4 — Collector

Build in dependency order; each table's foreign keys require the one before it.

- [ ] Raw response archiving — gzipped, dated, written before parsing (D-20)
- [ ] `collector_runs` logging wrapper around every run, success or failure (D-22)
- [ ] Rate limiting with exponential backoff on 999 and 429, plus a conservative default
      inter-request delay (D-21, D-21a)
- [ ] **Unit-test the backoff against simulated 999 / 429 responses.** This needs no token and can
      be written before access is granted — do not wait for a real throttle to discover a bug in a
      delay calculation (D-21a)
- [ ] Log every retry with its response code and applied delay — a backoff that silently works
      looks identical to one that never fired (D-21a)
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

**Prior seasons are test data, not scope.** They validate the collectors and are then left in the
database unread — no dashboard surfaces them and no cross-season feature is built on them (D-01,
amended).

This is the first sustained load ever placed on the token, and the first time the rate limiter
runs outside a unit test. **Escalate; do not open with a full season** (D-21a).

- [ ] `tilasto collect --backfill --weeks N-M` over the existing collectors (D-18)
- [ ] `--season` override for prior seasons
- [ ] Confirm the prior season is genuinely still retrievable before relying on it as the
      fixture — Yahoo's retention for an account is assumed, not verified

Then, in this order, stopping at each step to look at what happened:

- [ ] **One league, two or three weeks.** Exercises every collector at minimum cost
- [ ] Read the retry log: were requests paced, was any throttling seen and handled? Nothing
      throttling is a data point, not a pass — it means untested, not working (D-21a)
- [ ] Inspect the rows written; confirm they match what Yahoo displays for those weeks
- [ ] **One league, full season.** Confirms `matchups`, `standings`, and `player_weekly_stats` —
      the tables 2026 cannot populate until games are played
- [ ] Re-run that same backfill and confirm nothing duplicates (D-19)
- [ ] **Widen to the remaining leagues**, only once the above is clean

---

## Phase 6 — Grafana

- [ ] Add Postgres as a data source on the Grafana host, connecting as `tilasto_ro` (D-10)
- [ ] Dashboard uid `tilastokeskus` (D-32)
- [ ] A `season` template variable defaulting to the current season, and every panel query
      filtered by it — prior-season test data must not leak into dashboards (D-01, amended)
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
- [ ] Confirm labels are `league_key` / `team_key`, never display names — a mid-season rename
      would otherwise orphan the series and break rank graphs in half (D-08a)
- [ ] Grafana joins display names from Postgres for presentation (D-08a)
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
