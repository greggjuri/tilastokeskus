# init-02: Players and draft picks

**Created**: 2026-10-08
**Priority**: High
**Phase**: Phase 4 — Collector
**Depends On**: init-01-collector (complete)

---

## Problem Statement

Fifteen drafts happened and nothing recorded them. `players` and `draft_picks`
are empty, and the draft is the one event in a redraft season that is finished,
static and never changes — the cheapest real content this project can hold.

## Goal

Every drafted player and every pick, across fifteen leagues, in the database and
verifiable against Yahoo's own draft recap pages. Written by the same collection
machinery `init-01` proved, extended to handle a foreign-key dependency that
`leagues` → `teams` did not have.

## Scope

**`players` and `draft_picks` only.** Both are static: fetched once per season,
never refetched.

`rosters` moves to `init-03`. It is week-keyed, which drags in week resolution,
`--weeks` and backfill semantics — a different problem that pairs naturally with
phase 5 rather than with a one-shot draft import.

## Requirements

### Must Have (P0)
1. Collect `draft_picks` for all fifteen leagues
2. Collect the `players` those picks reference, before the picks are written
3. One-shot per season: a second run issues no draft requests
4. Idempotent upserts; re-running changes no row counts (D-19)
5. Raise on any position, pick shape or payload form not observed in the probe

### Should Have (P1)
1. `--league KEY` restricts the run, as in `init-01`
2. `--dry-run` reports the plan and the request count

### Nice to Have (P2)
1. Nothing. Keep this one small.

## Behaviour

```
tilasto collect --all              # draft picks and players, where unfetched
tilasto collect --league 470.l.X   # one league
tilasto collect --all --dry-run    # the plan
```

A successful run leaves behind: one row per drafted player in `players`, one row
per pick in `draft_picks`, gzipped payloads under `raw/{date}/`, and a
`collector_runs` row. A second run writes nothing new and issues no draft
requests.

## Technical Considerations

### Fetch order is not write order

This is the shape of the whole spec, and it inverts between the two phases.

**Draft picks carry only `pick`, `round`, `team_key` and `player_key` — no
names.** So the picks are what tell you *which* players to fetch:

```
fetch:  draft results  →  extract player keys  →  fetch those players
write:  players        →  draft picks                     (foreign key)
```

Fetching draft results first is what makes the player fetch targeted rather than
a crawl of the league's whole eligible pool. See Open Question 1 — this is the
decision that determines whether this spec is sixteen requests or several
hundred.

### Data Changes

None expected. `players` and `draft_picks` exist as revised 2026-10-08.

Writing rows ends the free-edit window for both. Unlike `leagues` and `teams`,
**neither table's payload has ever been observed** (Open Question 4), so that
window closes on a schema designed from documentation. The probe exists to fix
that before it does.

`players.position` is the column most at risk: one league uses superflex
(`Q/W/R/T`) and an individual defensive player slot (`D`), so its drafted
players will carry defensive positions nothing in this project has parsed.

### Yahoo API Usage

| Resource | Path | Calls | Notes |
|---|---|---|---|
| Draft results | `/league/{league_key}/draftresults` | 15 | one-shot per season |
| Players | `/players;player_keys=k1,k2,…` | unknown | batched; collection endpoint, batch size unobserved |

**Request volume**: unknown until the probe. Roughly 150 drafted players per
league (ten teams, fifteen-ish rounds) — 2,250 across fifteen. Batched at even
25 keys per call that is ~90 requests; unbatched it is 2,250. The probe settles
both the batch limit and whether `/players;player_keys=` behaves as the docs
suggest.

A one-shot import may legitimately cost more than a daily run. It still
escalates: one league, then widen (D-21a).

### Payload Shapes

**Neither resource has ever been archived.** This is the gap the probe closes,
and until it runs, Payload confidence on any PRP generated from this spec should
score low.

Expected hazards, based on every payload observed so far:

- Fields looked up by name, never position
- Numerics arriving as strings — `pick` and `round` are likely candidates
- `cost` present only in auction leagues, and all fifteen are snake — so it is
  probably absent rather than null or zero
- `eligible_positions` as a list including slot names (`W/R/T`, `Q/W/R/T`, `D`)
  that are not real positions, confirmed in `init-01`
- Defensive positions in the IDP league with no precedent in this codebase

### CLI Surface
- `tilasto collect --all | --league KEY`, both with `--dry-run`
- No new subcommand. Draft collection is gated the way settings is — a
  per-league "already fetched" marker, not a separate command

### Integration Points
- `parse.py` — new parsers, same `flatten` / `as_int` / `as_flag` primitives
- `store.py` — new upserts
- `collect.py` — a second phase inside the per-league loop
- `purge.py` — already covers both tables (D-50)

## Failure Behaviour

- **An unobserved position raises.** The IDP league guarantees positions this
  codebase has never seen; the probe names them, and anything outside that set
  is a bug report rather than a branch (D-38)
- **A player key in a pick with no corresponding player row raises** before the
  pick is written. A pick pointing at nothing is worse than no pick
- **Pick count is asserted** against what the league's roster size implies. A
  draft that returns forty picks for a ten-team league is a parse failure, not
  a short draft
- **A partial run is recorded as partial**, per `init-01`'s model
- **A successful run that writes nothing is a failure** — fifteen drafts are
  known to have happened

## Constraints

- Request volume is the budget, and this spec is the first that might
  meaningfully exceed sixteen a day. Escalate: one league, read the retry log,
  then widen (D-21a)
- Public repository — no host, address or agreement term in a committed file
- 500-line limit per source file

## Success Criteria

- [ ] `tilasto collect --league {one key}` writes that league's picks and their
      players, verified by query
- [ ] The picks match Yahoo's draft recap page for that league — order, round,
      team, player
- [ ] Re-running changes no row counts and issues no draft requests
- [ ] All fifteen leagues collected; pick counts consistent with roster size
- [ ] Every `draft_picks.player_key` resolves to a `players` row
- [ ] The IDP league's defensive positions are stored, not raised on — because
      the probe observed them first
- [ ] `collector_runs` accurate for every run
- [ ] Raw payloads written before parsing
- [ ] Contract checklist applied; coverage ≥ 80%; ruff clean

## Out of Scope

- `rosters` — `init-03`, together with week semantics
- `standings`, `matchups`, `player_weekly_stats` — phase 7
- `transactions`
- **Undrafted players.** Only players appearing in a draft pick are collected.
  Waiver pickups enter the player table when rosters or transactions do
- Backfilling prior seasons' drafts
- Grafana panels — phase 6

## Open Questions

Answer before `/generate-prp`. The first two are the ones that shape the spec.

- [x] **Answered 2026-10-08: drafted only, deduplicated.** The probe then found players
      arrive nested in `/league/{key}/draftresults/players`, so no separate fetch is needed.
      **1. Drafted players only, or the league's eligible pool?**
      Recommended: **drafted only**, via `/players;player_keys=` using the keys
      the draft results hand you. The eligible pool is every NFL player in the
      league's universe, paginated, refetched per league — orders of magnitude
      more requests for data nothing in this spec needs. The probe measures both
      so the choice is made on numbers rather than assumption
- [x] **Answered by the probe: 25, silently truncated** — asking for 100 returns 25 with no
      error. Moot: players come nested with the draft. **2. Batch size for `/players;player_keys=`?** Unobserved. The probe finds
      the limit. If batching is not supported on this path, request volume rises
      by roughly 25× and the scope needs revisiting before implementation
- [x] **Answered 2026-10-08: gate on existing `draft_picks` rows.**
      **3. How is "already drafted" marked?** `leagues.settings_fetched_at` has
      a precedent, but there is no `draft_fetched_at` column. Options: add one
      (a real `002` now that `leagues` has rows), or gate on the existence of
      `draft_picks` rows for that league. Recommended: **gate on existing rows**
      — no migration, and it is self-healing if a league's picks are purged
- [x] **Done 2026-10-08**: eight requests, three leagues including the IDP one; findings in
      PRP-02. **4. Probe before implementation.** Required. Three leagues including the
      superflex/IDP one: `/league/{key}/draftresults`, and `/players` both ways
      to compare. Read-only, archived, nothing written. Until it runs, this
      spec rests on documentation — the exact mistake that produced ten
      corrections in one spike (D-33)

## Notes

Relevant decisions: D-11 (keys verbatim), D-12 (`player_key` is season-scoped,
`player_id` is cross-season identity), D-19, D-20, D-21a, D-22, D-38, D-50.

`init-01`'s collection machinery — archive, `collector_runs`, per-league
transaction, strict parsing, escalating verification — is reused unchanged. The
genuinely new thing here is a cross-table foreign-key dependency where the fetch
order and the write order disagree.

The superflex/IDP league was found in the settings probe and has been waiting
for this spec ever since. It is the reason the player probe needs to include it
specifically rather than sampling three leagues at random.
