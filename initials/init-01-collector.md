# init-01: Collection infrastructure, leagues and teams

**Created**: 2026-10-08
**Priority**: High
**Phase**: Phase 4 — Collector
**Depends On**: None

---

## Problem Statement

Nothing is collected yet. The schema exists, the transport works, and the API is
live, but no code writes a row. Every table is empty and every collector method
raises `YahooCollectorNotImplemented`.

## Goal

Fifteen leagues and 150 teams in the database, written by a scheduled run that
logs its own health, archives what it fetched, and can be re-run safely. The
collection machinery is proven on the two tables that need no player data, so
`init-02` can build players, draft picks and rosters on top of something working.

## Requirements

### Must Have (P0)
1. Raw response archiving — gzipped, dated, written **before** any parsing (D-20)
2. `collector_runs` wrapper around every run, recording success, partial and
   failure, with rows written and leagues synced (D-22)
3. Collect `leagues` — discovery, metadata, and `/settings` on first fetch
4. Collect `teams`, all ten per league
5. Every write an idempotent upsert; re-running changes no row counts (D-19)
6. At least one and at most one team per league flagged `is_owned_by_me`
7. Raise on auction drafts, FAAB and points scoring — confirmed absent from all
   fifteen leagues as of 2026-10-08

### Should Have (P1)
1. `--dry-run` prints the plan and issues nothing
2. `--league {key}` restricts a run to one league
3. A partial run reports which leagues succeeded and which did not

### Nice to Have (P2)
1. `--refresh-settings` to force a settings refetch (currently in Known Issues)

## Behaviour

```
tilasto collect --all              # fifteen leagues, their settings if unfetched, their teams
tilasto collect --league 470.l.X   # one league
tilasto collect --all --dry-run    # the plan, no requests
```

A successful run leaves behind:

- One row per league in `leagues`, with settings columns populated and
  `settings_fetched_at` set
- Ten rows per league in `teams`, exactly one flagged `is_owned_by_me`
- Gzipped payloads under `raw/{date}/`, one per response, written before parsing
- One row in `collector_runs` with status, counts and duration
- A retry log showing requests were paced

A re-run leaves the same row counts and refreshes `updated_at`.

A failed run leaves a `collector_runs` row saying so, with the error, and
whatever partial data was committed before the failure.

## Technical Considerations

### Data Changes

None. `001_initial.sql` was revised against the spike on 2026-10-08 and the
tables are correct as they stand.

**This spec closes the free-schema-edit window for `leagues` and `teams`.** Both
are fully observed across all fifteen leagues — the settings probe covered every
`leagues` column, and a read-only teams probe on 2026-10-08 covered all fifteen
teams payloads (the spike had covered one) — so this is expected rather than
risky. Changes to either table
after this are a real `002`.

`standings`, `matchups`, `player_weekly_stats`, `rosters`, `players`,
`draft_picks` and `transactions` stay empty and stay editable.

### Yahoo API Usage

| Resource | Path | Notes |
|---|---|---|
| League discovery | `/users;use_login=1/games;game_codes=nfl;seasons={season}/leagues` | one call: game key and all fifteen league keys |
| Settings | `/league/{league_key}/settings` | first fetch only; `settings_fetched_at` gates it |
| Teams | `/league/{league_key}/teams` | ten per league; carries the league metadata, `current_week` included (D-24a) |

`/league/{league_key}` is not called: its metadata arrives in every teams
payload (decided 2026-10-08, PRP-01 Open Question 1).

**Request volume**: per league, not batched (see Open Questions). First run
31 calls — 1 discovery, then settings and teams per league. Subsequent runs 16,
since settings is gated by `settings_fetched_at`.

The game key is resolved from the discovery response, never hardcoded (D-11).

### Payload Shapes

Designed against archived payloads, not documentation:

- **League, settings, teams** — `raw/2026-10-07/spike/`, `raw/2026-10-08/settings/`,
  `raw/2026-10-08/teams/` (all fifteen), `raw/2026-10-08/discovery/` (2025)

Hazards confirmed across all fifteen:

- **Yes/no fields are the strings `'0'` and `'1'`.** `bool('0')` is `True` in
  Python, so a plain truth test marks every league as an auction draft. Compare
  against `'1'`
- **Numbers often arrive as strings.** `num_teams` is an int; `max_teams`,
  `playoff_start_week`, week, season, draft-time and waiver-time fields are
  strings. Each field kept its type across all fifteen, but coerce explicitly
- **`is_owned_by_current_login` is absent** on teams you don't own, not `0`
- **Two fields are missing rather than empty** in most leagues:
  `short_invitation_url`, `invite_permission`
- **Fields have no fixed position.** The league resource was the same
  two-element list in all fifteen, which is exactly the kind of stability that
  invites positional access. Read by key

### CLI Surface
- `tilasto collect --all | --league {key}`, both supporting `--dry-run`
- Exit codes: 2 usage, 1 runtime

### Integration Points
- `transport.py` — existing, with `call_with_backoff` and the pacer
- `collect.py` — orchestration, week resolution
- `db.py` — connection as `tilasto_app`
- `purge.py` — must cover the raw archive and both tables (it already does)
- The systemd timer runs `collect --all`; this is the first run that does work

## Failure Behaviour

- **Auction, FAAB and points scoring raise.** Confirmed absent from all fifteen
  leagues on 2026-10-08. An unexpected value is a bug report, not a branch (D-38)
- **Zero owned teams in a league raises.** The unique index catches two; it
  cannot catch none, which is the real hazard when the absent-field rule is
  missed. The collector checks for at least one
- **Auth failure surfaces immediately**, never retried (D-29)
- **A partial run is recorded as partial**, not success. A league that fails
  does not silently vanish from the results
- **A successful run that writes nothing is a failure.** Fifteen leagues are
  known to exist; zero rows means something broke upstream of the database

## Constraints

- **Request volume** is the budget. This is the first sustained collection, and
  backoff has never met a real throttle across 26 requests to date
- **Escalate, never sweep**: one league first, read the retry log, inspect rows
  against Yahoo, then widen (D-21a)
- **Public repository** — no host, address or agreement term in any committed
  file (D-30, D-52)
- 500-line file limit

## Success Criteria

- [ ] `tilasto collect --league {one key}` writes one league row and ten team
      rows, verified by query
- [ ] The values match what Yahoo displays for that league
- [ ] Re-running changes no row counts and updates `updated_at`
- [ ] `tilasto collect --all` writes fifteen leagues and 150 teams
- [ ] Exactly one team per league is flagged `is_owned_by_me`; a league with
      none raises
- [ ] `settings_fetched_at` is set, and a second run issues no settings requests
- [ ] `collector_runs` has one row per run with accurate counts
- [ ] Raw payloads exist for every response, written before parsing
- [ ] The retry log shows pacing; any throttling is reported
- [ ] `--dry-run` issues exactly one request — discovery — verified by the
      transport's request counter (the retry log records only retry waits)
- [ ] Contract checklist applied to every new public function
- [ ] Coverage ≥ 80%, `ruff check` clean

## Out of Scope

- `players`, `draft_picks`, `rosters` — `init-02`
- `standings`, `matchups`, `player_weekly_stats` — phase 7
- `transactions`
- Backfill and `--weeks` — phase 5. This collects current state only
- Grafana panels — phase 6
- The Prometheus exporter — phase 8
- `--refresh-settings` — Known Issues

## Open Questions

All four answered 2026-10-08. Recorded here rather than deleted, because the
reasoning is what a future reader will want.

- [x] **Batch or per-league?** → **Per league.** Request volume is minimal
      either way (~31 calls vs ~4, both trivial), so volume does not decide it.
      Debuggability does: one archived file per league diffs cleanly when a
      shape changes and serves directly as a test fixture; a malformed payload
      fails one league rather than fifteen; and `--league {key}` becomes the
      same code path as `--all` with a shorter list rather than a separate
      batched branch.
- [x] **Transaction boundary?** → **Per league**, following from the above.
      Fetch, archive, parse, commit, next. A failure at league 12 leaves 11
      committed and the run recorded as partial, which is what the failure
      behaviour above requires.
- [x] **Does `--dry-run` resolve the game key?** → **Yes, exactly one request.**
      The output says so explicitly: one call issued to resolve the game key,
      N further calls planned. A dry run that cannot name the real keys is
      barely a plan.
- [x] **Per-league `current_week` when all fifteen agree?** → **Already
      handled, and moot here.** `resolve_weeks()` takes a single league by
      signature and a test already asserts two leagues resolve independently
      (D-24a). Moot for this spec in any case: neither `leagues` nor `teams` is
      week-scoped, so `current_week` is stored and never read. It starts
      mattering in `init-02`, where rosters are week-keyed.

## Notes

Relevant decisions: D-11 (keys verbatim), D-19 (idempotent upserts), D-20 (raw
archive), D-21a (escalation), D-22 (`collector_runs`), D-24a (per-league current
week), D-29 (auth failure), D-33 (schema vs observed payloads), D-38 (loud
failure), D-54 (`tier` is presentation only).

The pacer has a known sub-interval gap after a token refresh, recorded in Known
Issues. Confirm it is fixed before this runs at volume.

One league uses superflex (`Q/W/R/T`) and an individual defensive player slot
(`D`). That affects `players.position` parsing in `init-02`, not this spec — but
it means the fifteen leagues are not as uniform as the settings table suggests.
