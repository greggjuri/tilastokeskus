# PRP-01: Collection infrastructure, leagues and teams

**Created**: 2026-10-08
**Initial**: `initials/init-01-collector.md`
**Phase**: Phase 4 — Collector
**Status**: Complete (2026-10-08)

> Filename: `prps/prp-01-collector.md` — the number and slug are inherited from
> the init spec, never chosen independently. Numbers are assigned in the order
> work is taken up and are never reused.

---

## Overview

### Problem Statement
Nothing is collected yet. The schema is revised, the transport works against the
real API, and access is live — but every `YahooClient` method raises
`YahooCollectorNotImplemented` and every table is empty.

### Proposed Solution
Build the collection machinery once, on the two tables that need no player data:

- a raw archive that writes every response, gzipped, before anything parses it
- strict, by-name parsers for the league, settings and teams payloads, designed
  against archived responses and raising on anything unobserved
- idempotent upserts for `leagues` and `teams`
- a per-league orchestrator — fetch, archive, parse, commit, next — wrapped in a
  `collector_runs` record that is written whatever happens
- `tilasto collect --all | --league KEY [--dry-run]` wired to all of it

`init-02` then adds players, draft picks and rosters on top of machinery that is
already proven.

### Success Criteria
Each must be verifiable by running something, not by reading the code.

- [ ] `tilasto collect --league {one key}` writes one `leagues` row and ten `teams`
      rows, verified by query
- [ ] Those rows match what Yahoo displays for that league — name, team names,
      the owned team, draft positions
- [ ] Re-running changes no row counts and advances `updated_at` (D-19)
- [ ] `tilasto collect --all` writes fifteen leagues and 150 teams
- [ ] Exactly one team per league has `is_owned_by_me`; a league with none fails
      that league loudly, and a league with two is refused by the unique index
- [ ] `settings_fetched_at` is set on first collection, and a second run issues
      no settings requests — verified by the run's request count
- [ ] `collector_runs` has one row per run with accurate status and counts,
      including a run that fails
- [ ] A raw payload exists for every response, written before parsing
- [ ] Pacing is visible in the run summary; any throttling is reported
- [ ] `--dry-run` issues exactly one request — discovery — and writes nothing,
      verified by the transport's request counter
- [ ] Contract checklist applied to every new public function
- [ ] Coverage ≥ 80%, `ruff check` clean, no file over 500 lines

---

## Context

### Related Documentation
- `docs/PLANNING.md` — architecture, schema, endpoints consumed
- `docs/DECISIONS.md` — D-03 (season a parameter), D-11 (keys verbatim, never
  hardcode a game key), D-19 (idempotent upserts), D-20 (raw archive before
  parsing), D-21a (escalate, never sweep), D-22 (`collector_runs`), D-24a
  (per-league current week), D-29 (auth failure loud), D-33 (schema vs observed
  payloads; the 2026-10-08 settings findings), D-38 (raise, never return empty),
  D-41 (read-only role sees new tables), D-44 (contract tests), D-49 (shapes, not
  values, in AI tools), D-50 (purge covers everything Yahoo), D-54 (`tier` is
  presentation only)
- `docs/TESTING.md` — contract checklist, parsing hazards, Lessons Learned
- Archived payloads:
  - `raw/2026-10-07/spike/user_leagues.json.gz` — discovery, all fifteen leagues
  - `raw/2026-10-08/teams/*.json.gz` — teams, all fifteen (pre-step probe)
  - `raw/2026-10-08/settings/*.json.gz` — settings, all fifteen
  - `raw/2026-10-08/discovery/seasons-2025.json.gz` — 2025 discovery, six leagues

### Dependencies
- **Required**: `001_initial.sql` as revised 2026-10-08 (applied, empty). The
  pacer fix `f493de7` — the init's note asking to confirm it is satisfied
- **Foreign-key order within a league**: `leagues` → `teams`
- **Optional**: none

### Files to Modify/Create
```
tilastokeskus/archive.py        # NEW: gzipped, dated raw archive, written before parsing
tilastokeskus/parse.py          # NEW: flatten, coercion, league/settings/teams parsers,
                                #      UnexpectedPayload
tilastokeskus/store.py          # NEW: upserts for leagues (metadata, settings) and teams
tilastokeskus/yahoo.py          # discover / settings / teams become real; league() removed;
                                #      the phase 7 and init-02 stubs keep raising
tilastokeskus/transport.py      # a requests_issued counter
tilastokeskus/collect.py        # per-league orchestration, run status, collector_runs
tilastokeskus/cli.py            # collect: --dry-run plan, run summary, exit codes
pyproject.toml                  # pytest-cov in the dev extra
scripts/redact_payload.py       # NEW: archive payload → committed, redacted fixture
tests/conftest.py               # NEW: throwaway-schema database fixture
tests/fixtures/*.json           # NEW: redacted fixtures, generated, never hand-written
tests/test_archive.py           # NEW
tests/test_parse.py             # NEW
tests/test_store.py             # NEW: database-backed
tests/test_yahoo.py             # updated: implemented methods leave the stub table
tests/test_collect.py           # extended: orchestration and run status
tests/test_transport.py         # extended: request counter
tests/test_cli.py               # NEW: collect argument handling and exit codes
CLAUDE.md, docs/PLANNING.md,
docs/TASK.md, docs/TESTING.md   # dry-run semantics, endpoints, status
```

Largest after the change: `cli.py` ~310 and `ratelimit.py` 280. Each new module
is planned well under 300. Nothing approaches 500.

---

## Technical Specification

### Data Changes
```sql
-- None. 001_initial.sql is used as revised on 2026-10-08.
```

Writing rows to `leagues` and `teams` ends the free-edit window for those two
tables: a later change to either is a real `002`. Both are now observed across
all fifteen leagues — `leagues` by the settings probe, `teams` by the pre-step
teams probe — so closing the window is expected rather than risky.

Schema checklist:
- [x] `NUMERIC` for points — no points in these tables
- [x] `TIMESTAMPTZ` — `settings_fetched_at`, `updated_at` set with `now()`
- [x] `week` in the primary key where week-scoped — neither table is
- [x] Keys verbatim, game key prefix intact (D-11) — asserted, see Parsing
- [x] Indexes named explicitly — no new indexes
- [x] `tilasto_ro` can read — no new tables; D-41 verified 2026-10-08
- [x] `tilasto purge` covers it — both tables and `raw/` are already purged (D-50)

### Yahoo API Usage

| Resource | Path | Calls per run | Notes |
|---|---|---|---|
| Discovery | `/users;use_login=1/games;game_codes=nfl;seasons={season}/leagues` | 1 | game key **and** league keys in one response — Open Question 2 |
| Settings | `/league/{league_key}/settings` | 0–15 | only where `settings_fetched_at IS NULL` |
| Teams | `/league/{league_key}/teams` | 15 | league metadata arrives in the same payload |

**Not called: `/league/{league_key}`.** Its metadata — `current_week`, name,
weeks, scoring type — is already present in all three responses above: in the
discovery payload for all fifteen leagues, and as `league[0]` of every teams and
settings payload (checked against the archive). Fetching it separately is
fifteen redundant calls per run (Open Question 1).

**Not called: `/game/nfl`.** The discovery path's `seasons` filter resolves the
game key for any season, `--season` included, and the game block in that
response carries `game_key`. Observed working for `seasons=2025` in the pre-step
probe (game key 461, six leagues, all `461.l.*`); the 2026 call is confirmed
live in Step 5. The init's "resolved from `/game/nfl`" is replaced
by "resolved from the discovery response" — still read, never hardcoded (D-11).

**Total request volume**: first run **31** (1 + 15 + 15); every run after **16**.
The init estimated 46 and 31, with the redundant league call. Steady state is
fifteen leagues once daily; this is below it.

### Payload Shapes

Every parser names its archive and is tested against redacted fixtures generated
from it.

### Pre-step probe — done 2026-10-08

Read-only, sixteen requests, nothing written to the database: `/teams` for all
fifteen leagues, archived to `raw/2026-10-08/teams/`, and 2025 discovery,
archived to `raw/2026-10-08/discovery/`. Every response `200`; all fifteen gaps
exactly 1.000s, the first live confirmation of the pacer fix; retry log empty.

Against the one league observed before:

- **Uniform where it matters.** 150 teams; every `count` matches its entries and
  its league's `num_teams`; every team's metadata is 24 entries; every
  `team_key` extends its league key; one manager and one logo per team
- **Exactly one owned team in every league**, value the int `1`, absent on the
  other 135
- **Absent-not-zero, beyond what was known**: `previous_season_team_rank` on 8
  teams; `is_current_login` on the owned team's manager; `is_commissioner` on one
  manager. None is read by this PRP
- **`number_of_trades` is int on 135 teams and string on 15** — the mixed-type
  hazard, now measured. Not stored by this PRP (it is a `standings` column)
- **`is_finished` observed**: `1` (an int) on all six finished 2025 leagues,
  absent from all fifteen unfinished 2026 leagues. Absent → `FALSE` is now
  backed by data rather than assumed
- **`current_week` changes type between seasons**: int in 2026, the string
  `'17'` in 2025. `as_int` already covers it; a parser trusting one season's
  type would not

- **Discovery** — `raw/2026-10-07/spike/user_leagues.json.gz`,
  `raw/2026-10-08/discovery/seasons-2025.json.gz`
  - `users → "0" → user[1].games → "0" → game` is a two-element list: game
    metadata (with `game_key`, `season`), then `{leagues: {count, "0".."14"}}`.
    Located by key at every level
  - `count` must equal the number of numeric-keyed entries — fifteen observed
  - Every `league_key` must start with the discovered `game_key` (D-11); a
    mismatch raises
- **Settings** — `raw/2026-10-08/settings/*.json.gz` (all fifteen)
  - `league` is `[metadata, {settings: [ {...} ]}]`. Find `settings` by key
  - Flags are the strings `'0'`/`'1'`: `is_auction_draft`, `uses_faab`. Parsed by
    `as_flag`, which accepts only `'0'`, `'1'`, `0`, `1` and raises on anything
    else — never truthiness (`bool('0')` is `True`)
  - `playoff_start_week` is a numeric string → `as_int`
  - `invite_permission`, `short_invitation_url` are absent in fourteen of fifteen;
    neither is read
  - **Raised as unobserved** (`UnexpectedPayload`): `draft_type` other than
    `live`, `is_auction_draft` not `'0'`, `uses_faab` not `'0'` — confirmed
    across all fifteen on 2026-10-08
- **League metadata** — `league[0]` of the teams payload (all three payloads
  carry it; teams is fetched every run, so it is the source)
  - `num_teams` int; `current_week` int in 2026 but a string in 2025;
    `start_week`, `end_week`, `season` numeric strings. All through `as_int`,
    which accepts int or digit-string and raises otherwise
  - `scoring_type` other than `head` raises — confirmed across all fifteen
  - `is_finished`: absent mid-season (all fifteen 2026 leagues), `1` once the
    season is over (all six 2025 leagues). Absent → `FALSE`; present → `as_flag`
- **Teams** — `raw/2026-10-08/teams/*.json.gz` (all fifteen)
  - `teams` is `{count, "0".."9"}`; each `team` is `[ [24 metadata entries], ...]`
    — a list of single-key dicts and empty-list placeholders. Flattened, then
    read by name
  - `team_id` numeric string → `as_int`; `draft_position`, `has_draft_grade` ints
    → `as_int` / `as_flag`
  - `is_owned_by_current_login` present on exactly one team per league (the int
    `1`) and **absent** on the other nine, in all fifteen. Absent → `FALSE`;
    present → `as_flag`
  - Absent on most teams and ignored here: `previous_season_team_rank`, and on
    managers `is_current_login`, `is_commissioner`
  - `managers` is a list — one manager per team observed. `manager_name` is the
    first manager's `nickname`; more than one manager is unobserved and raises
  - `team_logos` is a list — one observed; first logo's `url`
  - Parsed team count must equal the league's `num_teams`, and exactly one team
    must be owned. Either failing raises for that league

A parser designed from documentation rather than an observed payload is the
mistake that produced ten corrections in one spike (D-33). After the pre-step
probe, the one shape this PRP has not seen is the `seasons`-filtered discovery
for 2026 itself — seen for 2025, confirmed in Step 5.

### Upsert semantics
- **`leagues` metadata upsert** sets only the columns the metadata carries.
  It never writes `tier` (D-54), and never writes the settings columns — a
  metadata-only run must not null out settings fetched on an earlier one
- **Settings** are written by a separate statement that sets `draft_type`,
  `is_auction_draft`, `playoff_start_week` and `settings_fetched_at = now()`
- **`teams`** upsert on `team_key`, all parsed columns. The partial unique index
  backs the "at most one owned" rule; the parser enforces "exactly one" before
  the database is touched
- Every statement `ON CONFLICT ... DO UPDATE ... updated_at = now()`

### Raw archive layout
`raw/{UTC date}/{UTC HHMMSS}/{resource}/{key}.json.gz`, e.g.
`raw/2026-10-09/040112/teams/470.l.X.json.gz`. One directory per run, so a
same-day re-run does not overwrite the earlier payloads. Written to a temporary
name and renamed, so a crash cannot leave a truncated file that looks complete.
Dates are UTC; the two spike directories used local dates and stay as they are.

### CLI Surface
```
tilasto collect --all              # discover, settings where unfetched, teams
tilasto collect --league KEY ...   # the same path with a shorter list
tilasto collect --all --dry-run    # discovery only; prints keys and planned calls
```
- `--league` keys are checked against discovery before any other request; a key
  not in the season's leagues is refused, exit 2
- Exit codes: **0** success, **1** partial or failed, **2** usage (Open Question 4)
- The run summary prints status, leagues synced, rows written, requests issued,
  and every retry-log wait with its status and delay — an empty retry log is
  printed as such, not omitted

---

## Implementation Steps

Each step ends installable, tests passing, ruff clean. Commit after each.

### Step 0: Stop the collect timer
**Files**: none committed; `docs/TASK.md`

The timer runs `collect --all` daily. Today that fails loudly at the first stub.
The moment Step 7 lands it would succeed — an unescalated fifteen-league sweep at
the next 00:01, which D-21a forbids. Disable it before any collector code is
written; re-enable in Step 10, after the ladder.

```bash
systemctl --user disable --now tilastokeskus-collect.timer
```

**Validation**:
- [ ] `systemctl --user list-timers --all` shows it inactive
- [ ] Recorded in Known Issues with the re-enable condition

---

### Step 1: Test infrastructure
**Files**: `pyproject.toml`, `tests/conftest.py`, `scripts/redact_payload.py`,
`tests/fixtures/*.json`

- Add `pytest-cov` to the dev extra; install it in `.venv`
- `tests/conftest.py`: a `db` fixture that opens a connection as `tilasto_app`,
  begins a transaction, creates a throwaway schema, sets `search_path`, applies
  `001_initial.sql`, yields the connection, and rolls back. No real table is
  touched and nothing is left behind. If the database is unreachable the fixture
  **fails**, it does not skip (Open Question 5)
- `scripts/redact_payload.py`: reads an archived payload and writes a fixture
  with the same structure and types — every key, list length, nesting and
  scalar type preserved; every free-text and identifying value replaced (names,
  nicknames, GUIDs, URLs, chat ids, invitation links); league and team ids
  rewritten consistently to fake ids under the real game key; enumerations and
  structural integers kept. It is run locally, and only its output is read in
  an AI tool (D-49)
- Fixtures: 2026 discovery; 2025 discovery (finished leagues, string
  `current_week`); one teams payload chosen by structure — a league where some
  team carries `previous_season_team_rank` and `number_of_trades` is a string;
  two settings payloads, one standard and one with `invite_permission` present
- Hazard variants are derived in tests by editing a loaded fixture, never
  written by hand

**Validation**:
- [ ] `pytest --cov=tilastokeskus` runs; existing 185 tests still pass
- [ ] `db` fixture test: schema exists inside, gone after; real tables untouched
- [ ] Redaction test: a fixture's key set, list lengths and scalar types equal
      the source payload's; no value from a redacted field survives
- [ ] `tests/test_agreement_terms.py` still passes with fixtures added

---

### Step 2: Raw archive
**Files**: `tilastokeskus/archive.py`, `tests/test_archive.py`

```python
class RawArchive:
    def __init__(self, root: Path, started_at: datetime) -> None: ...
    def write(self, resource: str, key: str, payload: object) -> Path: ...
```

Contract: returns the path written; refuses an empty `resource` or `key` and a
key containing a path separator; raises on an existing file rather than
overwriting; writes via a temporary name and rename.

**Validation**:
- [ ] Round trip: written payload gunzips to an equal object
- [ ] Layout is `{root}/{UTC date}/{UTC HHMMSS}/{resource}/{key}.json.gz`
- [ ] Refusals tested; no temporary file survives a failed write
- [ ] `purge_data` removes it (existing behaviour, asserted once against the
      new layout)

---

### Step 3: Parsers
**Files**: `tilastokeskus/parse.py`, `tests/test_parse.py`

```python
class UnexpectedPayload(RuntimeError): ...       # names the field, value, league

def flatten(entries: list) -> dict: ...           # single-key dicts merged; [] skipped
def as_int(value: object, field: str) -> int: ...
def as_flag(value: object, field: str) -> bool: ...  # '0'/'1'/0/1 only
def parse_discovery(payload) -> tuple[str, list[str]]: ...   # game_key, league keys
def parse_league_meta(payload) -> LeagueMeta: ...
def parse_settings(payload) -> LeagueSettings: ...
def parse_teams(payload, num_teams: int) -> list[Team]: ...
```

Pure functions over payloads; no I/O. Each parser locates every field by key.

**Validation** — contract first:
- [ ] `as_flag('0')` is `False`; `as_flag('2')`, `as_flag('')`, `as_flag(None)`,
      `as_flag('true')` raise
- [ ] `as_int` accepts `5` and `'5'`; raises on `''`, `'5.0'`, `None`, `True`
- [ ] Each parser against its fixture yields the expected fields
- [ ] Raises, each with field and league in the message: `scoring_type` points;
      `is_auction_draft '1'`; `uses_faab '1'`; `draft_type` not `live`; zero
      owned teams; two owned teams; team count ≠ `num_teams`; two managers; a
      discovered key without the game key prefix; discovery `count` ≠ entries
- [ ] Absent `is_owned_by_current_login` → `False`; absent `is_finished` → `False`;
      `is_finished` `1` in the 2025 fixture → `True`
- [ ] 2025 fixture's string `current_week` parses to an int
- [ ] Fixture with its metadata entries reordered parses identically

---

### Step 4: Upserts
**Files**: `tilastokeskus/store.py`, `tests/test_store.py`

```python
def upsert_league_meta(conn, meta: LeagueMeta) -> int: ...
def store_league_settings(conn, league_key: str, s: LeagueSettings) -> int: ...
def upsert_teams(conn, league_key: str, teams: list[Team]) -> int: ...
def leagues_needing_settings(conn, keys: list[str]) -> set[str]: ...
```

Each takes a connection, not settings, so the caller owns the transaction and
tests can pass the throwaway-schema connection. Returns rows affected.

**Validation** (against the `db` fixture):
- [ ] Twice → identical row counts; `updated_at` advances (D-19)
- [ ] A metadata upsert after settings leaves the settings columns and
      `settings_fetched_at` intact
- [ ] `tier` set by hand survives every upsert (D-54)
- [ ] Two owned teams in one league → `UniqueViolation` from the index
- [ ] `leagues_needing_settings` returns unknown and unfetched keys, not fetched

---

### Step 5: Client
**Files**: `tilastokeskus/yahoo.py`, `tilastokeskus/transport.py`,
`tests/test_yahoo.py`, `tests/test_transport.py`

- `YahooTransport.requests_issued` — incremented per HTTP GET, retries included
- `YahooClient(settings, transport, archive)`: `discover(season)`,
  `settings(league_key)`, `teams(league_key)`. Each fetches, **archives, then
  returns** the payload; parsing is not the client's job. `league()` is removed
  — its metadata comes with teams
- The remaining stubs (`draft_results`, `roster`, `standings`, `scoreboard`,
  `transactions`) keep raising; `test_yahoo.py`'s table loses the implemented
  methods and its completeness test still holds

**Validation**:
- [ ] Exact paths requested, against a fake transport
- [ ] The archive is written before the method returns; an archive failure
      raises and the payload is not returned
- [ ] `requests_issued` counts a retried request twice
- [ ] **One live call**: `discover(2026)` against Yahoo returns fifteen keys and
      game key 470 (Open Question 2). If the `seasons` filter is refused, fall
      back to `game_keys=nfl` plus a separate games lookup and say so

---

### Step 6: Orchestration
**Files**: `tilastokeskus/collect.py`, `tests/test_collect.py`

```
discover → refuse unknown --league keys → which need settings?
for each league:
    with conn.transaction():
        teams payload (archived) → meta, teams parsed → upsert league meta
        if settings needed: settings payload (archived) → parse → store
        upsert teams
    on exception: record the league as failed, continue
record_run(...)   # always — success, partial, failed
```

Run status:
- **success** — every league committed and rows written > 0
- **partial** — at least one league committed, at least one failed
- **failed** — none committed, an error before the loop (auth, discovery), or
  a run that "succeeded" with zero rows (the Silence Check)

`AuthenticationFailed` aborts the run immediately rather than failing league by
league — fifteen identical auth failures are one failure (D-29).

**Validation** (fake client, `db` fixture):
- [ ] All leagues succeed → success, counts right
- [ ] One league raises `UnexpectedPayload` → partial; the others committed; the
      failed league's rows absent; its key in the error text
- [ ] Auth failure on league 1 → failed, no further requests
- [ ] Zero rows written → failed
- [ ] `collector_runs` row written in every case, including an exception
- [ ] Settings requested only for leagues with `settings_fetched_at IS NULL`

---

### Step 7: CLI
**Files**: `tilastokeskus/cli.py`, `tests/test_cli.py`

- `collect` builds transport, archive, client and runs the plan
- `--dry-run`: discovery only, then prints the league keys, how many need
  settings (a database read, no write), and the planned request count. Prints
  the requests actually issued, from the counter
- Prints the run summary; exits 0 / 1 / 2 as above

**Validation**:
- [ ] `--dry-run` with a fake transport issues exactly one request and writes
      nothing — no archive file, no row, no `collector_runs` entry
- [ ] Exit codes per status, and 2 for an unknown `--league`

---

### Step 8: Documentation
**Files**: `CLAUDE.md`, `docs/PLANNING.md`, `docs/TESTING.md`, `docs/TASK.md`,
`docs/DECISIONS.md`

- **`--dry-run` semantics change.** `CLAUDE.md` and `docs/PLANNING.md` say it
  "issues nothing" and "deliberately does not estimate request counts". The
  init's answered question has it issue one request and print a count. Both
  documents are updated to say so, and why (Open Question 3)
- `docs/PLANNING.md` endpoints: discovery path with `seasons`, `/league/{key}`
  marked not used
- `docs/DECISIONS.md`: an ADR for the run-status and exit-code rules, and for
  league metadata coming from the teams payload
- `docs/TASK.md`: Spec Numbering row, In Progress, Known Issues

**Validation**:
- [ ] `tests/test_agreement_terms.py` passes
- [ ] No document still says `--dry-run` issues nothing

---

### Step 9: Live verification — one league
**Commands**:
```bash
tilasto apicheck --no-post
tilasto collect --all --dry-run
tilasto collect --league {one key}
tilasto collect --league {one key}       # again
```

Escalate, never sweep (D-21a).

**Validation**:
- [ ] Dry run: one request issued, fifteen keys printed
- [ ] One `leagues` row, ten `teams` rows, one owned
- [ ] Rows checked against Yahoo's own league page
- [ ] Second run: same counts, `updated_at` advanced, **no settings request**
- [ ] Retry log read; pacing visible; throttling reported either way
- [ ] Raw files present for every response of both runs

---

### Step 10: Live verification — widen, then the timer

Verification, not discovery: the fourteen leagues not seen before Step 1 were
probed read-only beforehand, so their shapes are known.
**Commands**:
```bash
tilasto collect --all
tilasto collect --all
systemctl --user enable --now tilastokeskus-collect.timer
```

**Validation**:
- [ ] Fifteen leagues, 150 teams, exactly one owned per league
- [ ] All fifteen teams payloads parse without a raise, as the probe predicts
- [ ] Re-run: identical counts, 16 requests
- [ ] `collector_runs`: one row per run, statuses and counts accurate
- [ ] Timer re-enabled only after all of the above; first timer run checked in
      the journal the next day

---

## Testing Requirements

### Contract Tests
- `as_flag` / `as_int` refuse everything outside the observed forms
- Every parser raises `UnexpectedPayload` with field, value and league
- `RawArchive.write` refuses bad keys and never overwrites
- The orchestrator's three statuses are distinguishable, and `collector_runs` is
  written on every path (D-22, D-44)
- `requests_issued` counts what reached the session, retries included
- The unimplemented client methods still raise with a reason and a pointer (D-38)

### Unit Tests
Fixtures come from `scripts/redact_payload.py` over the raw archive — never
hand-written dicts.

- `test_parse_teams_absent_owned_flag_is_false`
- `test_parse_settings_string_flag_zero_is_false` — the `bool('0')` trap
- `test_parse_league_meta_coerces_string_numerics`
- `test_parse_reordered_metadata` — no positional access survives
- `test_upsert_is_idempotent`, `test_meta_upsert_preserves_settings`,
  `test_upsert_preserves_tier`

### Integration Tests
- Steps 9 and 10, in order. Expensive by design; nothing in CI

---

## Integration Test Plan

### Prerequisites
```bash
source .venv/bin/activate
tilasto apicheck --no-post
systemctl --user list-timers --all | grep tilastokeskus-collect   # inactive
```

### Test Steps
| Step | Action | Expected Result | Pass? |
|------|--------|-----------------|-------|
| 1 | `tilasto collect --all --dry-run` | 1 request, 15 keys, plan printed, nothing written | ☐ |
| 2 | `tilasto collect --league {key}` | 1 + 10 rows, 3 requests, status success | ☐ |
| 3 | Compare with Yahoo's league page | names, owned team, draft positions match | ☐ |
| 4 | Re-run step 2 | same counts, 2 requests, `updated_at` advanced | ☐ |
| 5 | `tilasto collect --all` | 15 + 150 rows, ≤ 31 requests | ☐ |
| 6 | Re-run step 5 | same counts, 16 requests | ☐ |
| 7 | `collector_runs` | one row per run, accurate | ☐ |

### Error Scenarios
| Scenario | How to Trigger | Expected Behavior | Pass? |
|----------|----------------|-------------------|-------|
| Revoked credentials | corrupt `YAHOO_REFRESH_TOKEN` in a copy of `.env` | failed run, one auth error, no retries (D-29) | ☐ |
| Unknown league | `--league 470.l.1` | exit 2 after discovery, nothing written | ☐ |
| Database down | wrong `PGPORT` in the environment | loud failure naming the target, no password | ☐ |
| Unimplemented path | `YahooClient.roster(...)` | raises with a reason and a pointer (D-38) | ☐ |

---

## Error Handling

### Expected Errors
| Error | Cause | Handling |
|-------|-------|----------|
| 401/403 | revoked or unbound token | abort the run as failed, never retry (D-29) |
| Throttle | rate limit | backoff on `(status, payload)`; logged and printed |
| `UnexpectedPayload` | unobserved value or shape | that league fails; run is partial |
| `UniqueViolation` on the owned index | two owned teams slipped past the parser | that league fails; run is partial |
| `DatabaseUnavailable` | Postgres down | run fails before any request; journal only — it cannot record itself |

### Edge Cases
- A league whose teams payload differs from the one observed: strict parsing
  raises for that league alone
- `is_finished` appearing: parsed as a flag; unobserved until a season ends
- **Unobserved cases raise rather than branch** — auction, FAAB and points
  scoring are absent from all fifteen leagues' `/settings` (2026-10-08). A
  league added later is re-checked by the same parser on its first collection,
  because its settings are unfetched

### Silence Check
A run that succeeds and writes nothing is prevented four ways:
1. Zero rows written turns success into **failed**
2. Discovery returning a `count` that disagrees with its entries raises
3. A teams payload whose team count differs from `num_teams` raises
4. A league with no owned team raises — the case the unique index cannot catch

And the run is always visible: `collector_runs` is written on every path that
can reach the database, and the run summary prints requests issued and the
retry log, empty or not.

---

## Request Volume Impact

| Component | Change | Per run | Per day |
|-----------|--------|---------|---------|
| Discovery | +1 | 1 | 1 |
| Settings | first collection of each league only | 15, then 0 | 0 |
| Teams | +1 per league | 15 | 15 |
| **Total** | | **31 first, 16 after** | **16** |

Below the fifteen-leagues-once-daily steady state. Verification adds roughly
40 more across Steps 5, 9 and 10, spread over the escalation.

---

## Open Questions

All answered 2026-10-08.

- [x] **1. Drop `/league/{key}`?** → **Yes.** Its metadata is in every teams
      payload, verified against the archive; saves fifteen calls a day
- [x] **2. One-call discovery.** → **As planned**: one live call in Step 5, with
      the two-call fallback. The pre-step probe already saw the `seasons` filter
      work for 2025
- [x] **3. What does `--dry-run` issue?** → **Exactly one request**, discovery.
      The init's success criterion said zero; corrected. `CLAUDE.md` and
      `docs/PLANNING.md` are updated in Step 8, when the behaviour lands. The
      request counter exists because the retry log cannot verify a count
- [x] **4. Exit code for a partial run.** → **1.** A timer run that left a league
      uncollected reads as failed in systemd
- [x] **5. Database-backed tests.** → **Throwaway schema, fail rather than skip.**
      A skipped idempotency test is silence
- [x] **6. Teams observed in one league.** → **Probed all fifteen first**,
      read-only. Uniform; deviations listed under Pre-step probe
- [x] **7. `is_finished`.** → **Observed** in the same probe: `1` on all six 2025
      leagues, absent on all fifteen 2026 leagues

## Rollback Plan

1. `git revert` the step's commit — each leaves the package working
2. Schema: none changed
3. Data: `tilasto purge --data --confirm` empties both tables, `collector_runs`
   and `raw/`; `schema_migrations` stays. Narrower: `TRUNCATE teams, leagues
   CASCADE` as `tilasto_app`
4. Timer: `systemctl --user disable --now tilastokeskus-collect.timer`
5. Verify: row counts zero; `tilasto collect --all --dry-run` still plans

Both tables are re-fetchable (D-17), so discarding and re-collecting is cheaper
than repairing in place.

---

## Confidence Scores

| Dimension | Score (1-10) | Notes |
|-----------|--------------|-------|
| Clarity | 9 | Every open question answered; the dry-run contradiction resolved |
| Feasibility | 9 | Every piece fits the existing transport, db and CLI; no schema change |
| Completeness | 9 | All P0 and P1 covered; P2 `--refresh-settings` deliberately out |
| Alignment | 8 | No ADR contradicted. The `--dry-run` convention in `CLAUDE.md` / PLANNING changes, by decision, in Step 8; Step 0 keeps D-21a intact |
| Payload confidence | 9 | Discovery, settings and teams observed across all fifteen; `is_finished` and the `seasons` filter observed on 2025. Unseen: the 2026 `seasons` discovery, checked in Step 5 |
| **Average** | **8.8** | |

---

## Notes

- The init's request estimates (46 / 31) assumed `/league/{key}` and `/game/nfl`;
  this plan is 31 / 16
- The pre-step probe's sixteen requests are not part of any run
- The superflex/IDP league affects `players.position` in `init-02`, not this PRP
- `collector_runs` has no per-league outcome column. Failed league keys go in
  `error`; no schema change, and `collector_runs` is not a Yahoo-shaped table
- `uses_faab` is checked when settings are parsed but not stored — there is no
  column, and settings are fetched once per season. A league that switches to
  FAAB mid-season is caught only by `--refresh-settings`, which is in Known Issues
