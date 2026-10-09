# PRP-02: Players and draft picks

**Created**: 2026-10-08
**Initial**: `initials/init-02-draft.md`
**Phase**: Phase 4 — Collector
**Status**: Complete (2026-10-09)

> Filename: `prps/prp-02-draft.md` — number and slug inherited from the init spec.

---

## Overview

### Problem Statement
Fifteen drafts happened and nothing recorded them. `players` and `draft_picks` are empty. The draft
is the one event in a redraft season that is finished and static — the cheapest real content this
project can hold.

### Proposed Solution
One request per league: `/league/{league_key}/draftresults/players` returns every pick **with its
player nested inside it** — observed in the probe, 190 of 190 picks. Parse both from that one
payload, then write players before picks (the foreign key), in a draft transaction of the league's
own, gated on the league having no `draft_picks` rows yet.

This dissolves the init's central problem. "Fetch order is not write order" assumed picks first,
then a targeted `/players` fetch; with the players nested, fetch is one call and only the write
order matters. It also removes the batch-size question entirely — which matters, because the probe
found that `/players;player_keys=` **silently truncates to 25** (Pre-step probe below).

### Success Criteria
Each verifiable by running something.

- [ ] `tilasto collect --league {one key}` writes that league's picks and their players, verified
      by query — 150 picks for a standard league, 190 for the superflex/IDP league
- [ ] The picks match Yahoo's draft recap page — order, round, team, player (checked by the owner,
      outside the AI tool, D-49)
- [ ] Re-running changes no row counts and issues **no draft request** — verified by the request
      counter
- [ ] All fifteen leagues: 2,290 picks (14 × 150 + 190); every `draft_picks.player_key` resolves
      to a `players` row
- [ ] The IDP league's defensive players are stored, not raised on
- [ ] A league whose draft fails leaves its teams refresh committed, and the run reads partial
- [ ] `collector_runs` accurate for every run; raw payloads written before parsing
- [ ] Contract checklist applied; coverage ≥ 80%; ruff clean; no source file over 500 lines

---

## Context

### Related Documentation
- `docs/DECISIONS.md` — D-11 (keys verbatim), D-12 (`player_key` season-scoped, `player_id`
  cross-season), D-17, D-19, D-20, D-21a, D-22, D-33, D-38, D-49, D-50, D-56 (per-league
  transactions, run status), D-57 (discovery), D-58 (timeouts)
- `docs/TESTING.md` — contract checklist; Lessons Learned (the 25-key truncation joins them)
- Archived payloads:
  - `raw/2026-10-07/spike/draftresults.json.gz` — plain draft results, one standard league
  - `raw/2026-10-08/<run>/draftresults/*.json.gz` — plain draft results, the IDP league and two
    more standard leagues
  - `raw/2026-10-08/<run>/draftresults-players/*.json.gz` — **the IDP league, picks with nested
    players, 190 of 190** — the payload this PRP's parser is designed against
  - `raw/2026-10-08/<run>/players/*.json.gz`, `league-players/*.json.gz` — the global and
    league-scoped player collections, for comparison

### Dependencies
- **Required**: PRP-01 (complete) — `leagues` and `teams` rows exist; picks reference `teams`
- **Write order within a league's draft**: `players` → `draft_picks`

### Files to Modify/Create
```
scripts/redact_payload.py      # player keys and ids rewritten consistently; draft fields kept
tests/fixtures/draft_idp.json  # NEW: redacted draftresults/players, IDP league
tilastokeskus/parse.py         # Player, Pick, parse_draft
tilastokeskus/store.py         # upsert_players, upsert_draft_picks, leagues_needing_draft
tilastokeskus/yahoo.py         # draft(league_key)
tilastokeskus/collect.py       # a draft phase after the league's teams, own transaction
tilastokeskus/cli.py           # dry run counts draft requests
tilastokeskus/migrations/002_players_nfl_positions.sql  # NEW: rename + column comments (Q1)
tests/test_parse.py, test_store.py, test_yahoo.py, test_collect.py, test_cli.py,
tests/test_infrastructure.py   # extended
CLAUDE.md, docs/PLANNING.md, docs/DECISIONS.md, docs/TESTING.md, docs/TASK.md
```
`parse.py` is 338 lines and gains roughly 110; if it would pass ~450, the draft parser moves to
`parse_draft.py` rather than crowding the limit.

---

## Technical Specification

### Pre-step probe — done 2026-10-08

Read-only, **eight requests**, all `200`, retry log empty, nothing written to the database. Three
leagues: the superflex/IDP league and two standard leagues not covered by the 2026-10-07 spike.

**Draft results**
- Exactly `pick`, `round`, `team_key`, `player_key` per pick, in all four drafts now observed; pick
  and round are ints
- Picks `1..n` contiguous; exactly ten picks in every round; no player drafted twice
- **150 picks in the standard leagues (15 rounds), 190 in the IDP league (19 rounds)** — both equal
  non-IR roster slots × ten teams, as `/settings` predicted
- No `cost` anywhere: all fifteen leagues are non-auction (D-33)

**`/league/{key}/draftresults/players` — picks with players nested**
- Each `draft_result` carries `"0": {"players": {count: 1, "0": {"player": [[metadata]]}}}`;
  190 of 190 picks; every nested `player_key` equals its pick's
- Player metadata includes `primary_position`, which the global `/players` collection omits

**`/players;player_keys=` — not used, and why**
- Asked for 100 keys, returned 25, `count=25`, **no error**. The limit is 25 and the excess is
  dropped silently — a request-shaped Silence Check failure waiting to happen. Recorded in Lessons
  Learned so no future collector batches on this path without asserting what came back

**Player fields: league-independent or not** — the same 50 players, fetched globally and nested
in the IDP league's draft, compared field by field:

| Identical in 50/50 | Differs |
|---|---|
| `name`, `display_position`, `editorial_team_abbr`, `bye_weeks`, `player_id`, `position_type`, `status`, `uniform_number` | `eligible_positions` (3/50): the league copy adds that league's slots — `('RB',)` globally is `('RB','W/R/T','Q/W/R/T')` in the IDP league. `is_undroppable` (31/50). `primary_position` exists only in the league copy |

`players` holds **one row per player across all leagues** (D-12). A league-dependent field stored
there would be overwritten by whichever league ran last — Open Question 1.

**Positions in the IDP league (190 players)**
- `primary_position`: `QB RB WR TE K DEF D` — nine individual defensive players carry `D`
- `display_position`: the same offensive set plus `LB`, `S`, `CB`, and comma-joined multi-position
  values `DT,DE`, `DL,DE`, `DB,CB`, `WR,CB` (a two-way player whose eligibility includes `D`)
- `position_type`: `O`, `K`, `DT` (team defense), `DP` (defensive player)

**Other hazards**
- `player_id` and `bye_weeks.week` are strings; `player_key`'s suffix equals `player_id`
- `uniform_number` is a string for players and the boolean `false` for team defenses
- Absent on most players: `status`, `status_full`, `injury_note` (58 of 190),
  `has_recent_player_notes`; `linked_player` on one. None is stored

**Request arithmetic, revised.** The four observed drafts name 206 distinct players across 640
picks — players repeat heavily between leagues. Irrelevant now that players arrive nested, but it
means the `players` table will hold a few hundred rows, not 2,290.

### Data Changes
```sql
-- 002_players_nfl_positions.sql — a real 002, not an edit to 001: 001 is applied and leagues,
-- teams and collector_runs hold rows, so 001 can no longer change without dropping the database,
-- and collector_runs is the one table backfill cannot rebuild.
ALTER TABLE players RENAME COLUMN eligible_positions TO nfl_positions;
COMMENT ON COLUMN players.nfl_positions IS
  'Real NFL positions from display_position, split on commas. League-independent. Not roster-slot '
  'eligibility (W/R/T, IR, ...), which is league data and belongs with rosters.';
COMMENT ON COLUMN players.position IS
  'display_position verbatim, e.g. LB or DT,DE. League-independent.';
```

The rename is not cosmetic. `eligible_positions` means "which roster slots can this player fill",
which is why `W/R/T` and `IR` appeared in it. Under this PRP the column holds "which real NFL
positions does this player play" — a different concept, and a name that reads as the old one is
the project's recurring failure (`tier`, `max_delay`, `is_owned_by_me`). Both tables are empty, so
renaming costs one statement now.

Mapping:

| Column | Source | Notes |
|---|---|---|
| `players.player_key` | `player_key` | verbatim, game key prefix checked (D-11) |
| `players.player_id` | `player_id` | `as_int`; must equal the key's suffix |
| `players.full_name` | `name.full` | |
| `players.position` | `display_position` | verbatim, e.g. `LB`, `DT,DE`; league-independent |
| `players.nfl_positions` | `display_position` split on `,` | real positions only — league-independent; renamed from `eligible_positions` by `002` |
| `players.nfl_team` | `editorial_team_abbr` | |
| `players.bye_week` | `bye_weeks.week` | `as_int` |
| `draft_picks.*` | `pick`, `round`, `team_key`, `player_key` | `cost` stays NULL; present → raise |

Schema checklist:
- [x] No points, no timestamps beyond `updated_at`
- [x] Neither table is week-scoped
- [x] Keys verbatim; `player_key` prefix = game key; `team_key` within the league
- [x] No new tables or indexes; a column rename keeps its table's grants, so `tilasto_ro` still
      reads both (D-41) — re-verified after `tilasto migrate`
- [x] `002` is applied by `tilasto migrate` before the first draft write; the test fixture applies
      every migration in order, so tests run against the renamed column
- [x] `tilasto purge --data` already truncates both and the archive (D-50)

### Yahoo API Usage

| Resource | Path | Calls | Notes |
|---|---|---|---|
| Draft with players | `/league/{league_key}/draftresults/players` | 15, once | only for leagues with no `draft_picks` rows |

**Request volume**: first run after this lands **31** (16 as today + 15 drafts); every run after
**16**, unchanged. No batching, no pagination.

### Payload Shapes
- **Draft with players** — designed against `draftresults-players/` (IDP league, 190 picks)
  - `draft_results` located by key; `count` = entries (existing `numbered`)
  - Each pick: by name, never position; `"0" → players → count 1 → "0" → player → [[metadata]]`
  - Nested `player_key` must equal the pick's — a mismatch raises
  - **Observed sets, anything else raises** (D-38): `primary_position` ∈ {QB, RB, WR, TE, K,
    DEF, D}; `position_type` ∈ {O, K, DT, DP}; each `display_position` token ∈ {QB, RB, WR, TE,
    K, DEF, LB, S, CB, DT, DE, DL, DB}; `cost` absent
  - **Structure**: picks `1..n`, rounds `1..R`, exactly `num_teams` picks per round, every
    `team_key` one of the league's, no player twice
  - Standard leagues' nested shape is unobserved (their plain draft results are); Step 9 runs a
    standard league first, so a deviation surfaces on one league

### CLI Surface
- No new flags. `collect --all | --league KEY` collects drafts where none are stored
- `--dry-run` adds "N drafts" to the planned request count (a read, no write)
- Exit codes unchanged: 0 success, 1 partial or failed, 2 usage

---

## Implementation Steps

### Step 0: Stop the collect timer
Once Step 6 lands, the next timer run would fetch all fifteen drafts unescalated (D-21a).
`systemctl --user disable --now tilastokeskus-collect.timer`; Known Issues; re-enable in Step 10.

### Step 1: Fixtures
**Files**: `scripts/redact_payload.py`, `tests/fixtures/draft_idp.json`,
`tests/test_infrastructure.py`

- Redactor: rewrite `{gk}.p.{id}` keys and `player_id` consistently to fake ids, so a fixture's
  key suffix still equals its `player_id`; keep `pick`, `round`, `primary_position`,
  `display_position`, `position_type`, `editorial_team_abbr`; redact names and everything else as
  today
- Generate `draft_idp.json` from the IDP `draftresults-players` payload; audit surviving strings
  as for PRP-01
- **Validation**: fixture tests — 190 picks, nine `D` players, a comma-joined `display_position`,
  a boolean `uniform_number`, key suffix = `player_id`; agreement-term count unchanged

### Step 2: Parser
**Files**: `tilastokeskus/parse.py` (or `parse_draft.py`), `tests/test_parse.py`

```python
@dataclass(frozen=True)
class Player: player_key, player_id, full_name, position, nfl_positions, nfl_team, bye_week
@dataclass(frozen=True)
class Pick: league_key, pick, round, team_key, player_key

def parse_draft(payload, league: LeagueMeta, game_key: str) -> tuple[list[Player], list[Pick]]
```
- **Validation** — contract first: every structural rule above raises with field and league;
  unobserved position, position type or display token raises; `cost` present raises; nested key
  mismatch raises; players deduplicated by key within a draft; reordered metadata parses
  identically; mutation-check the round and team checks

### Step 3: Upserts
**Files**: `tilastokeskus/store.py`, `tests/test_store.py`
- `upsert_players(conn, players)`, `upsert_draft_picks(conn, league_key, picks)`,
  `leagues_needing_draft(conn, keys)` — keys with no `draft_picks` rows
- **Validation**: twice → identical counts; a pick before its player → `ForeignKeyViolation`; the
  same player from two leagues → one row; empty or stray input refused

### Step 4: Client
**Files**: `tilastokeskus/yahoo.py`, `tests/test_yahoo.py`
- `draft(league_key)` → `league/{key}/draftresults/players`, archived as `draftresults`; leaves
  the stub table; `draft_results` stub removed
- **Validation**: exact path, archive before return; completeness test updated

### Step 5: Orchestration
**Files**: `tilastokeskus/collect.py`, `tests/test_collect.py`

Per league, after the league's metadata-and-teams transaction commits:
```
if league needs a draft:
    with conn.transaction():
        players, picks = parse_draft(client.draft(key), meta, game_key)
        upsert_players(...); upsert_draft_picks(...)
```
A draft failure is recorded against the league and the run reads partial, but the league's
teams stay committed (Open Question 3). Auth, throttle, timeout and lost-connection still abort
(D-56, D-58).
- **Validation**: draft written once, not on re-run; a draft that raises leaves teams committed
  and status partial; settings and drafts gated independently; rows counted

### Step 6: CLI
**Files**: `tilastokeskus/cli.py`, `tests/test_cli.py`
- Dry run plans `teams + settings + drafts`; summary unchanged
- **Validation**: `planned requests: 45 (15 teams, 15 settings, 15 drafts)` on an empty
  database; `15 (15 teams, 0 settings, 0 drafts)` after a full run

### Step 7: Migration 002 — moved before Step 2 in execution order
**Files**: `tilastokeskus/migrations/002_players_nfl_positions.sql`, `tests/test_store.py`
- The rename and the two column comments above. Executed first among the code steps, since the
  parser's dataclass and the upserts name the new column
- **Validation**: `tilasto migrate --dry-run` lists `002`; the throwaway-schema fixture shows
  `nfl_positions` and no `eligible_positions`; applied live with `tilasto migrate` in Step 9,
  before any draft write; `tilasto_ro` SELECT on `players` re-checked

### Step 8: Documentation
**Files**: `CLAUDE.md` (parsing rules: the 25-key truncation; league-dependent player fields;
`eligible_positions` → `nfl_positions` wherever it is described),
`docs/PLANNING.md` (data models, endpoints), `docs/DECISIONS.md` (D-33 probe findings; D-59 for
the draft design), `docs/TESTING.md` (Lessons Learned), `docs/TASK.md`

### Step 9: Live — one league, then the IDP league
```bash
tilasto migrate                                # applies 002 — before anything writes a player
tilasto collect --all --dry-run
tilasto collect --league {a standard league}   # nested shape in a standard league: first sight
tilasto collect --league {same}                # no draft request
tilasto collect --league {the IDP league}
```
- 150 picks, 150 players resolved; re-run issues 2 requests, writes no pick; IDP: 190 picks, nine
  `D` players; retry log read; owner checks one recap page

### Step 10: Live — widen, then the timer
```bash
tilasto collect --all && tilasto collect --all
systemctl --user enable --now tilastokeskus-collect.timer
```
- 2,290 picks; every pick resolves; second run 16 requests, no draft request

---

## Testing Requirements

### Contract Tests
- `parse_draft` raises `UnexpectedPayload` naming field and league on every structural rule and
  every unobserved value; returns players deduplicated
- `upsert_draft_picks` refuses picks for another league and an empty list
- `leagues_needing_draft` returns exactly the leagues with no picks
- A draft failure is distinguishable from a teams failure in `collector_runs.error`

### Unit Tests
Fixtures from the archive via `scripts/redact_payload.py`, never hand-written; hazards made by
editing a loaded fixture.

### Integration Tests
Steps 9 and 10, in order.

---

## Integration Test Plan

| Step | Action | Expected Result | Pass? |
|------|--------|-----------------|-------|
| 1 | `collect --all --dry-run` | 1 request; plans 15 teams + 0 settings + 15 drafts | ☐ |
| 2 | `collect --league {standard}` | 150 picks, ≤150 players, 3 requests | ☐ |
| 3 | Re-run step 2 | 2 requests, same counts | ☐ |
| 4 | `collect --league {IDP}` | 190 picks, nine `D` players | ☐ |
| 5 | Owner compares a recap page | order, round, team, player match | ☐ |
| 6 | `collect --all`, twice | 2,290 picks; second run 16 requests | ☐ |

| Scenario | How to Trigger | Expected | Pass? |
|---|---|---|---|
| Unobserved position | fixture edited to `primary_position: 'IDP'` | league's draft fails, teams kept, partial | ☐ |
| Pick with unknown team | fixture edited `team_key` | raises before any write | ☐ |

---

## Error Handling

| Error | Cause | Handling |
|---|---|---|
| `UnexpectedPayload` in a draft | unobserved shape or value | draft transaction rolls back; league's teams kept; run partial |
| `ForeignKeyViolation` | pick before its player | cannot happen by write order; tested |
| 401/403, throttle, timeout | as before | abort the run (D-29, D-56, D-58) |

### Silence Check
1. A draft with zero picks raises — fifteen drafts are known to have happened
2. Picks per round must equal `num_teams`, and Yahoo's `count` must equal the entries
3. Every pick's nested player must be present and match — a pick never points at nothing
4. The run-level rule stands: success that wrote nothing is failed (D-56)

---

## Request Volume Impact

| Component | Per run | Per day |
|---|---|---|
| Drafts | 15 on first collection, then 0 | 0 |
| **Total** | **31 first, 16 after** | **16** |

---

## Open Questions

All answered 2026-10-09.

- [x] **1. `players.position` and `eligible_positions`** → **`display_position` for both, and the
      list column renamed `nfl_positions`** by a `002`. `players` is one row per player across
      fifteen leagues, so a league-dependent value is last-writer-wins — `eligible_positions`
      differed in 47 of 50 players. The new meaning, real positions, gets a name that cannot be
      read as the old one, roster-slot eligibility. Slot eligibility belongs with `rosters`, init-03
- [x] **2. Pick count** → **internal consistency**: `num_teams` picks per round, picks and rounds
      contiguous, Yahoo's `count` equal to the entries. Probe-confirmed against roster size in all
      four drafts; storing roster size would be a `002` on a table with rows
- [x] **3. A draft failure keeps the league's teams** → **yes**, separate transaction. The draft is
      one-shot and teams daily; coupling them would let a draft parse bug silently stop a league's
      daily data
- [x] **4. Timer off during execution** → **yes** (Step 0). Otherwise the next timer run would
      fetch all fifteen drafts in one unescalated sweep

Answered before this PRP (2026-10-08): drafted players only; draft gated on existing
`draft_picks` rows; probe run first. The batch-size question is moot.

## Rollback Plan

1. `git revert` — commits are atomic
2. Schema: `ALTER TABLE players RENAME COLUMN nfl_positions TO eligible_positions` and delete
   the `002` row from `schema_migrations` — only while `players` is still empty
3. Data: `TRUNCATE draft_picks, players` as `tilasto_app` — the gate re-fetches each league's draft
   on the next run, self-healing as the init intended; or `tilasto purge --data --confirm`
4. Timer: disable during repair

---

## Confidence Scores

| Dimension | Score | Notes |
|---|---|---|
| Clarity | 9 | Every open question answered |
| Feasibility | 9 | One call per league, existing machinery, one-statement migration |
| Completeness | 9 | All P0 and P1; owner check of a recap page stays manual by design (D-49) |
| Alignment | 9 | D-12 respected. D-33 finding 6 described `eligible_positions` as slot eligibility; the rename retires that column rather than redefining it, and the docs change with it |
| Payload confidence | 8 | Nested draft payload observed in the IDP league (the hardest case); standard leagues' plain draft results observed, nested form not — Step 9 runs one first |
| **Average** | **8.8** | |

---

## Notes

- The init says neither resource had been archived. The 2026-10-07 spike archived one standard
  league's plain draft results, and its roster carried player metadata — corrected here
- The init's request estimate (90 to 2,250) assumed a separate `/players` fetch; with nested
  players it is 15, once
- `uniform_number`, `status`, `injury_note`, `is_undroppable` and the headshot fields are not
  stored. Injury status is weekly data; if it is ever wanted it belongs with rosters
