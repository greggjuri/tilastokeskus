# PRP Template

## PRP-{nn}: {Feature Name}

**Created**: {YYYY-MM-DD}
**Initial**: `initials/init-{nn}-{slug}.md`
**Phase**: {Phase N from docs/TASK.md}
**Status**: Draft / Ready / In Progress / Complete

> Filename: `prps/prp-{nn}-{slug}.md` — the number and slug are inherited from
> the init spec, never chosen independently. Numbers are assigned in the order
> work is taken up and are never reused.

---

## Overview

### Problem Statement
{What problem are we solving? Why does this feature matter? Adapt from the init.}

### Proposed Solution
{High-level description of what is being built and how.}

### Success Criteria
Each must be verifiable by running something, not by reading the code.

- [ ] {Criterion — testable}
- [ ] {Criterion — testable}
- [ ] Re-running changes no row counts — for anything that writes (D-19)

---

## Context

### Related Documentation
- `docs/PLANNING.md` — architecture, schema, endpoints consumed
- `docs/DECISIONS.md` — {list the specific D-nn entries this PRP relies on}
- `docs/TESTING.md` — contract checklist, known failure modes
- {Archived payloads: `raw/{date}/...` — the shapes this code must parse}

### Dependencies
- **Required**: {prp-nn-{slug}, or tables that must exist first. Note
  foreign-key order: `leagues` → `teams` → `players` → `draft_picks` →
  `rosters`}
- **Optional**: {enhances but not required}

### Files to Modify/Create
```
tilastokeskus/collect.py       # Description of changes
tilastokeskus/yahoo.py         # Description of changes
tests/test_collect.py          # NEW: purpose
```

Every file stays under 500 lines. If a change would push one past it, the PRP
says which module it splits into.

---

## Technical Specification

### Data Changes
```sql
-- New or modified tables/columns.
-- Is this an edit to 001_initial.sql, or a real 002?
-- Tables with rows in them mean 002. Tables still empty mean an edit.
```

Checklist for anything touching the schema:
- [ ] `NUMERIC` for points, never `FLOAT` (D-13)
- [ ] `TIMESTAMPTZ`, converted from Yahoo's epoch seconds (D-14)
- [ ] `week` in the primary key where the row is week-scoped (D-15)
- [ ] Yahoo keys stored verbatim, game_key prefix intact (D-11)
- [ ] Indexes named explicitly
- [ ] `tilasto_ro` can read any new table (default privileges — D-41)
- [ ] `tilasto purge` covers anything new that holds Yahoo data (D-50)

### Yahoo API Usage

| Resource | Path | Calls per run | Notes |
|---|---|---|---|
| {Resource} | `/league/{league_key}/...` | {n × leagues × weeks} | {second fetch? new shape?} |

**Total request volume per run**: {n}. Steady state is fifteen leagues once
daily; anything materially above that needs justifying here (D-21a).

### Payload Shapes

For each new resource parsed, name the archived payload it was designed against
and the shape hazards it carries:

- **{resource}** — `raw/{date}/{file}.json.gz`
  - Fields looked up by name, never position
  - {Any field absent rather than zero/false}
  - {Any key holding a list in one record and an object in another}
  - {Any numeric field arriving as a string}

A parser designed from documentation rather than an observed payload is the
mistake that produced ten corrections in one spike (D-33).

### CLI Surface
```
tilasto {subcommand} [--flags]
```
- Exit codes: 2 usage, 1 runtime
- `--dry-run` for anything that issues requests: prints the plan, issues nothing

---

## Implementation Steps

Each step ends with the project in a working state: installable, tests passing,
ruff clean. A step that cannot be left half-done is too big — split it.

### Step 1: {Title}
**Files**: `path/to/file.py`

{What to implement.}

```python
# Shape, not the whole implementation
```

**Validation**:
- [ ] Tests pass
- [ ] `ruff check` clean
- [ ] Contract checklist applied: return distinguishability, config fields
      actually read, invalid input refused at construction (`docs/TESTING.md`)
- [ ] {Specific validation — run a command, assert on its output}

---

### Step 2: {Title}
**Files**: `path/to/file.py`

{What to implement.}

**Validation**:
- [ ] Tests pass
- [ ] {Specific validation}

---

### Step N: Live verification
**Commands**:
```bash
tilasto {command} --dry-run          # confirm the plan first
tilasto {command} --league {one key} --weeks {2-3 weeks}
```

Escalate, never sweep: one league and a few weeks first, read the retry log,
inspect the rows, then widen (D-21a).

**Validation**:
- [ ] `collector_runs` recorded the run with the right status
- [ ] Raw archive written before parsing, gzipped and dated
- [ ] Retry log shows requests were paced
- [ ] Row counts match what Yahoo displays
- [ ] Re-run changes no row counts

---

## Testing Requirements

### Contract Tests
Before any arithmetic assertion, per `docs/TESTING.md`:
- `test_{feature}_distinguishes_outcomes`: a caller can tell every return path apart
- `test_{feature}_rejects_invalid_config`: refused at construction, with a clear error
- `test_{feature}_raises_when_unavailable`: raises rather than returning empty (D-38)

### Unit Tests
Fixtures come from the raw archive, redacted — never hand-written dicts, which
encode the shape you expected rather than the one Yahoo sends.

- `test_{feature}_parses_{resource}`: against the archived payload
- `test_{feature}_handles_absent_field`: absent is not `false`
- `test_{feature}_coerces_string_numerics`
- `test_{feature}_is_idempotent`: run twice, identical row counts

### Integration Tests
Expensive — rate limited, undocumented limit, personal-use agreement. Plan them.

- {Scenario}: {one league, few weeks, what to check}

---

## Integration Test Plan

Manual verification after implementation.

### Prerequisites
```bash
source .venv/bin/activate
tilasto apicheck          # confirm auth is live before anything else
```

### Test Steps
| Step | Action | Expected Result | Pass? |
|------|--------|-----------------|-------|
| 1 | `tilasto {cmd} --dry-run` | plan printed, no request issued | ☐ |
| 2 | {Action} | {Expected} | ☐ |
| 3 | Re-run step 2 | identical row counts | ☐ |
| 4 | Compare against Yahoo's displayed values | exact match | ☐ |

### Error Scenarios
| Scenario | How to Trigger | Expected Behavior | Pass? |
|----------|----------------|-------------------|-------|
| Revoked credentials | corrupt `YAHOO_REFRESH_TOKEN` | loud auth failure, no retry storm (D-29) | ☐ |
| Unimplemented path | {invoke it} | raises with a reason and a pointer (D-38) | ☐ |
| {Feature-specific} | {How} | {Expected} | ☐ |

---

## Error Handling

### Expected Errors
| Error | Cause | Handling |
|-------|-------|----------|
| 401/403 | revoked or unbound token | raise immediately, never retry (D-29) |
| Throttle | rate limit | backoff keyed on `(status, payload)`, not exceptions |
| {Feature-specific} | {Cause} | {Handling} |

### Edge Cases
- {Edge case}: {how handled}
- **Unobserved cases raise rather than branch** — once they are confirmed
  absent from real data. Points scoring: absent in all fifteen. Auction and
  FAAB: checked in one league only, so unverified rather than impossible. State
  the sample behind every "never happens". An unexpected value is a bug report,
  not a code path

### Silence Check
The characteristic failure here is a run that succeeds and writes nothing.
State what prevents that for this feature: what raises, what is asserted, what
`collector_runs` records.

---

## Request Volume Impact

| Component | Change | Per run | Per day |
|-----------|--------|---------|---------|
| {Resource} | {+n calls} | {n} | {n} |
| **Total** | | **{n}** | **{n}** |

Not billed, but rate-limited under an agreement granted for modest personal use.
The cost of being wrong is asymmetric. If this materially increases volume, say
how the escalation ladder applies (D-21a).

---

## Open Questions

Answer before implementation. An unanswered question becomes a guess in the code.

- [ ] {Question}
- [ ] {Question}

---

## Rollback Plan

1. `git revert {commit}` — commits are atomic and each leaves the package working
2. Schema: {if a migration ran, the down path, or an explicit note that there
   isn't one and why}
3. Data: {`tilasto purge --data --confirm` empties everything; a narrower
   rollback needs naming here}
4. Verify: {how to confirm the rollback succeeded — row counts, a dry run}

Because every table is week-keyed and retroactively fetchable, discarding bad
data and re-collecting is usually cheaper than repairing it in place (D-17).

---

## Confidence Scores

| Dimension | Score (1-10) | Notes |
|-----------|--------------|-------|
| Clarity | X | Are requirements unambiguous? |
| Feasibility | X | Achievable with the current architecture? |
| Completeness | X | Does the PRP cover all aspects? |
| Alignment | X | Consistent with `docs/DECISIONS.md`? Any ADR contradicted? |
| Payload confidence | X | Designed against observed payloads, or assumed? |
| **Average** | **X** | |

**Do not proceed below 7.** List the specific concerns and what would raise the
score — usually an answered open question or an archived payload to design
against.

---

## Notes

{Additional context, relevant D-nn entries, prior art in the codebase.}
