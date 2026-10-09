# Tilastokeskus - Task Tracker

## Current Sprint: Context engineering retrofit + phase 4 collector

The project ran phases 0–3 without the CE workflow. Retrofitting it now, before
the largest build in the project (the collectors) rather than after.

### In Progress

- [ ] CE retrofit — `CLAUDE.md`, `docs/PLANNING.md`, `docs/TESTING.md`,
      `initials/template/`, `prps/template/` and `.claude/commands/` written;
      `docs/TASK.md` is this file. `examples/` still to come.
      `tilastokeskus-brief.md` retired — no longer on disk; its `.gitignore`
      entry stays so a restored copy cannot be committed
- [x] Move `DECISIONS.md` and `TASKS.md` from repo root into `docs/`, renaming
      `TASKS.md` → `TASK.md` to match the template set
- [x] **Done 2026-10-08** — edited in place, database recreated, D-41 probe passed.
      Revise `001_initial.sql` against the ten spike findings (D-33) — **must
      land before any collector writes a row**; the tables are empty, so this is
      still an edit rather than a `002`

### Up Next

- [ ] `{rosters}` - init-03: `rosters`, with week semantics. Not yet claimed

---

## Spec Numbering

**Next free number: `03`**

Numbers are assigned in the order work is **taken up**, not the order specs are
written — a backlog item has a slug but no number until then. The PRP inherits
the init spec's number and slug: `init-01-collector.md` → `prp-01-collector.md`.

**Numbers are never reused.** An abandoned spec burns its number; the gap is the
record that something was tried and dropped.

| nn | Slug | Init | PRP | Status |
|----|------|------|-----|--------|
| 01 | collector | [init](../initials/init-01-collector.md) | [PRP](../prps/prp-01-collector.md) | **Complete** 2026-10-08 |
| 02 | draft | [init](../initials/init-02-draft.md) | [PRP](../prps/prp-02-draft.md) | **Complete** 2026-10-09 |

Phases 0–3 predate the CE workflow and have no specs.

---

## Recently Completed

### PRP-02 — Players and draft picks (2026-10-09)
- [x] One request per league: `/draftresults/players` nests each pick's player;
      gated on stored picks; own transaction after the league's teams (D-59)
- [x] Migration 002: `players.eligible_positions` → `nfl_positions`, sourced
      from the league-independent `display_position`
- [x] Live: one standard league twice (3 then 2 requests), the IDP league, then
      all fifteen twice (29 then 16). 2,290 picks — 14 × 150 + 190 — every round
      exactly 10, 219 distinct players, no orphan picks, no roster slot in
      `nfl_positions`, IDP defenders stored. No retries, no throttling
- [x] Owner confirmed the first collection against Yahoo, outside the AI tool
      (D-49)
- [x] Timer re-enabled; 374 tests, 91% coverage, ruff clean

### First scheduled collection (2026-10-09)
- [x] The timer ran the collector at 00:12: success, 15/15 leagues, 165 rows,
      16 requests, retry log empty, 15s

### Transport request timeout (2026-10-08)
- [x] Every API GET and token POST carries `(10s connect, 30s read)`; validated
      at construction, asserted on the wire, mutation-checked
- [x] A timeout is retried under the backoff budget and logged; once the budget
      is spent it raises `RequestTimedOut`, which aborts the run (D-58)

### PRP-01 — Collection infrastructure, leagues and teams (2026-10-08)
- [x] Raw archive, gzipped, one directory per run, written before parsing (D-20)
- [x] Strict parsers for discovery, league metadata, settings and teams, against
      redacted fixtures from the archive; mutation-checked
- [x] Idempotent upserts; settings and `tier` never overwritten by a metadata run
- [x] Per-league transactions; success / partial / failed with the Silence
      Check; only success exits 0 (D-56)
- [x] Discovery resolves game key and leagues in one call; `/league/{key}` and
      `/game/nfl` dropped (D-57)
- [x] Live: one league twice (3 then 2 requests), then all fifteen twice (30
      then 16 requests). 15 leagues, 150 teams, exactly one owned per league;
      re-runs change no counts. No retries, no throttling in 50 requests
- [x] Rows checked against Yahoo's own league page by the owner — names,
      managers, the owned team, draft order and grades matched. Done outside
      the AI tool, so no league data entered it (D-49)
- [x] Collect timer re-enabled; first scheduled run 2026-10-09 00:11
- [x] 316 tests, 91% coverage, ruff clean

### Phase 3 — Teams across all fifteen leagues (2026-10-08)
- [x] Read-only `/teams` probe of all fifteen, archived to `raw/2026-10-08/teams/`;
      uniform, exactly one owned team per league. Deviations recorded in D-33
- [x] 2025 discovery: `is_finished` is `1` when a season is over and absent
      before; the `seasons` filter works on the discovery path
- [x] Pacer fix confirmed live: fifteen gaps, all exactly 1.000s

### Phase 3 — Settings across all fifteen leagues (2026-10-08)
- [x] `/settings` fetched for all fifteen, archived to `raw/2026-10-08/settings/`
      before parsing. Nothing written to the database
- [x] All fifteen: `draft_type: live`, `is_auction_draft: 0`, `uses_faab: 0`,
      `scoring_type: head`, ten teams, playoffs from week 16 with four teams.
      **No auction, no FAAB** — those paths may raise (D-33)
- [x] First fifteen-request loop: 14 pacing sleeps, no throttling, no retries
- [x] **Pacer gap fixed.** The transport now takes the token, then paces, then
      sends — so a refresh no longer shortens the gap to the next API call.
      Regression test fails on the old code (`[0.6, 1.0]`) and passes on the new

### Phase 3 — First contact with the API (2026-10-07)
- [x] Yahoo API access live on the Confidential Client after a ~10-week process
- [x] OAuth, token refresh, transport and backoff exercised against the real API
- [x] Fifteen league keys returned for `nfl` 2026, all `470.l.{id}`, all on
      current_week 5
- [x] Recorded the real game keys: **470 = 2026, 461 = 2025**. The earlier
      example had a real key on the wrong season (D-11)
- [x] Read-only spike: eight gzipped payloads to `raw/2026-10-07/spike/`
- [x] Ten schema differences recorded against observed payloads (D-33)

### `tilasto purge` (2026-09)
- [x] `--data` and `--credentials` as independent targets; neither implies the other
- [x] Dry run by default; nothing deletes without `--confirm`
- [x] Independent re-read verification, raising `PurgeVerificationFailed` rather
      than returning
- [x] `collector_runs` purged with the data — its `error` column can capture
      payload fragments
- [x] `schema_migrations` never purged — truncating it would leave a migrated
      database that believes it is empty

### Rate limiter (2026-09)
- [x] Bounded exponential backoff, asserted delay sequences, injectable `sleep`
      and `jitter_source`
- [x] Two ceilings: `max_attempts` and `max_cumulative_delay`
- [x] Elapsed-aware pacing — backoff does not compound with the inter-request delay
- [x] Self-review by execution found five defects the 37-test suite missed; all
      fixed, bad test rewritten rather than supplemented (D-44)
- [x] Given a call site; `(status, payload)` rather than exception-keyed

### Phase 2 — Unblocked work (2026-08/09)
- [x] PostgreSQL 17 native, database owned by `tilasto_app`
- [x] `tilasto_ro` read-only, default privileges applied **before** the first
      migration and proven with a probe table (D-41)
- [x] Package, CLI with `--backfill --weeks` from the first commit, migration
      runner, `schema_migrations`
- [x] systemd user units installed and test-fired with the service still a stub
- [x] `loginctl enable-linger` — without it user timers stop at logout

### Phase 0 — Repository groundwork (2026-08)
- [x] Public repo, README with attribution, `.gitignore` committed **before any
      token existed** (D-28, D-30, D-31)
- [x] Corrected the data availability model: everything is backfillable (D-16 → D-17)

---

## Backlog

Backlog items carry a `{slug}` only. The number is claimed from **Spec
Numbering** above at the moment work is taken up.

### Phase 4 — Collector (Prioritized)
- [ ] `{collector}` - Raw archiving, gzipped and dated, written before parsing (D-20)
- [ ] `{collector}` - `collector_runs` wrapper around every run (D-22)
- [ ] `{collector}` - `leagues` + `/settings` second fetch for `draft_type`,
      `playoff_start_week`
- [ ] `{collector}` - `teams`, with the `is_owned_by_current_login`
      absence handled and asserted: exactly one team per league, fail loudly otherwise
- [ ] `{collector}` - `players` — must precede `draft_picks`, which carry
      no player names
- [ ] `{collector}` - `draft_picks`, `rosters`
- [ ] Verify idempotency directly: run twice, confirm identical row counts (D-19)

### Phase 5 — Backfill
- [ ] `{backfill}` - `--weeks N-M` over the existing collectors (D-18)
- [ ] Escalation ladder: one league / 2–3 weeks → read the retry log → inspect
      rows against Yahoo → one league / full season → re-run for idempotency →
      only then widen (D-21a)

### Phase 6 — Grafana
- [ ] `{dashboard}` - Postgres data source as `tilasto_ro` (D-10)
- [ ] `{dashboard}` - Dashboard uid `tilastokeskus`, season template
      variable defaulting to current
- [ ] `{dashboard}` - Tier colouring — bulk leagues visually distinct (D-54)
- [ ] Attribution in the dashboard description (D-31)

### Phase 7 — In-season collection
- [ ] `{inseason}` - `matchups`, `standings`, `player_weekly_stats`
- [ ] `{inseason}` - `result` derived from `winner_team_key`, not fetched
- [ ] Rank-over-time panels
- [ ] **Verify week 1 totals against Yahoo's own displayed values** — the one
      check that catches scoring misinterpretation

### Phase 8 — Observability
- [ ] `{exporter}` - Prometheus exporter over `collector_runs` (D-07)
- [ ] `{exporter}` - Labels are keys only: `{league_key, team_key}` (D-08a)
- [ ] Alert on `tilasto_collector_last_success_timestamp` staleness, calibrated
      for a desktop that spends days in another OS (D-23, D-40)
- [ ] Verify the alert fires: stop the timer, wait out the threshold

An alert that has never fired is a hypothesis, not a safeguard.

---

## Completed

### Access
- [x] Applied at the access portal as an individual (D-26)
- [x] Approved 2026-08-28; agreement signed 2026-09-01; countersigned 2026-10-06
- [x] Discovered the real binding step: a **new** app must be created after the
      account is enabled, and its Client ID submitted through the confirmation
      form. Editing the pre-existing app does nothing
- [x] Access live 2026-10-07 on the Confidential Client

### Operational tooling
- [x] `tilasto apicheck` — daily probe, posted to Discord unconditionally at 13:00.
      **Timer disabled 2026-10-07** once access went live; unit files stay
      installed, the command runs by hand. Re-enable with
      `systemctl --user enable --now tilastokeskus-apicheck.timer` if access is lost
- [x] `Persistent=true` on every timer (D-40)

---

## Architecture Decisions

Full records in `docs/DECISIONS.md`. The ones that changed how the project is
built:

### Key learnings from the rate limiter
1. **Tests can enshrine a bug while appearing to prove correctness.**
   `test_auth_failure_is_not_retried` asserted the broken return contract. It
   had to be rewritten, not supplemented (D-44)
2. **A config field that exists is not a behaviour that happens.**
   `request_interval` was defined, documented, reported as done, and never read
3. **Nothing throttling is not a pass.** It means the limiter is untested

### Key learnings from first API contact
1. **Yahoo's throttle arrives as a non-error status carrying a throttle page.**
   An exception-keyed retry layer would have sailed straight through it, parsing
   HTML as data
2. **`yahoofantasy` cannot log in on Python 3.12+** — it calls `ssl.wrap_socket`,
   removed in 3.12. Verify third-party libraries *run*, not just import
3. **Approval emails fire before enablement.** Two "your access is live" emails
   arrived while every client still 403'd. Probe, don't trust

### Key learnings from the spike
1. **Parse by name, never by position.** Fields arrive as lists with empty
   placeholders and no fixed ordering
2. **An absent field is not `false`** — and a naive parser reading missing as
   false happens to be *correct* here, which is why it would never be caught
3. **A real key on the wrong season is worse than no example.** `461` was
   recorded as 2026; it is 2025

### Implemented: append-only decision log
Reversed decisions are marked **Superseded** and kept with their original
reasoning intact. D-16 survives as a record of assuming an API limitation rather
than confirming one — which is exactly the mistake the spike was designed to
prevent repeating.

---

## Notes

### Configuration
- Season: never a constant — `default_season()` computes it (D-03, D-37)
- Game key 2026: `470` · 2025: `461` — confirmed 2026-10-07, and still read from
  the league resource, never hardcoded (D-11)
- Leagues: 15, all NFL redraft, all head-to-head, 10 teams each
- Cadence: daily, one timer (D-53)
- Collection host: triple-boot desktop, not a server (D-40)

### Cost tracking
$0/month. The budget is **request volume**, not money — see `docs/PLANNING.md`.

### Known Issues

- **Discord webhook URL leaked into a chat transcript** during the spike. Rotate
  it: delete in Discord, create a new one, update `.env`. Write-only to one
  channel, so the blast radius is spam, but rotate anyway
- **`--refresh-settings` does not exist yet.** `/settings` is fetched once per
  season (`leagues.settings_fetched_at`), but commissioners can edit settings
  mid-season, so the once-per-season rule needs an escape hatch. Belongs with
  the settings-fetch work, not the schema revision
- **`examples/` is referenced by `CLAUDE.md` and `docs/PLANNING.md` but does not
  exist.** Needs at least one real pattern — the upsert shape, the retry policy,
  a parse-by-name helper
- **Points scoring, auction drafts and FAAB are absent** from all fifteen
  leagues' `/settings` (2026-10-08), so those paths raise rather than branch
  speculatively
- **One league uses `Q/W/R/T` and `D` roster slots** — superflex and an
  individual defensive player. The schema comment on `players.position` lists
  only offensive positions and `DEF`; that league's defensive players are
  unobserved

---

*Last updated: 2026-10-08 (CE retrofit; phase 3 complete, phase 4 next)*

---

## Template Usage Notes

**Task Status Flow:**
```
Backlog → Up Next → In Progress → Recently Completed → Completed
```

**Update Triggers:**
- Starting a task: move to "In Progress"
- Completing a task: move to "Recently Completed"
- Starting a new phase: move "Recently Completed" into "Completed"
- Learning something: add to "Architecture Decisions", and an ADR in
  `docs/DECISIONS.md` if it changes how the project is built
- Finding an issue: add to "Known Issues"

**Format for task items:**
- Backlog (unclaimed): `- [ ] {slug} - {Brief description}`
- In progress / up next: `- [ ] init-{nn}-{slug}.md - {Brief description}`
- Completed: `- [x] {description}`

**Claiming a number:** take the next free number from **Spec Numbering**,
increment it there, add the row to the table, then write
`initials/init-{nn}-{slug}.md`. Never reuse a number.

**Project-specific:** a task is not complete because it was reported complete.
The rate limiter's `request_interval` was marked done with only the config field
present. Verify by execution before ticking a box.
