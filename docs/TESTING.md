# Tilastokeskus - Testing Standards

## The Lesson That Shaped This File

In September 2026 the rate limiter shipped with 37 passing tests, ruff clean. A
review by execution rather than by reading then found **five defects, and the
tests had missed every one**.

The tests were thorough about the arithmetic they set out to verify — exact delay
sequences, cap flattening, jitter band edges, cumulative budget bounds. They were
blind to the contract around it. All five defects were about what the function
*returned* or *accepted* at its boundary, not what it computed inside:

| Defect | Boundary |
|---|---|
| A 401 and a 200 returned the same value — the caller could not tell them apart | return |
| `request_interval` was defined, documented, and never read | config → behaviour |
| `max_attempts=0` reached a line labelled `assert unreachable` | input validation |
| `max_delay` was not a maximum — jitter applied after the cap | input → output |
| `jitter > 1.0` produced a negative delay, which `time.sleep` rejects | input validation |

Worse: one test, `test_auth_failure_is_not_retried`, asserted `== "unauthorized"`.
It checked the retry behaviour correctly and **enshrined the broken return
contract while appearing to prove correctness**.

So the rule for this project:

> **Test the contract, not only the arithmetic.** For every function, ask what it
> returns on each path, what it accepts, and what it does with input it should
> refuse — before testing what it computes.

(D-44)

## Testing Pyramid

```
        /\
       /  \     Verification against Yahoo's own displayed values (rare, manual)
      /----\
     /      \   Integration against the live API (few — rate limited, costly)
    /--------\
   /          \ Unit, against fixtures from the raw archive (many)
  --------------
```

The pyramid is unusually bottom-heavy here on purpose. The live API is rate
limited, undocumented, and accessed under a personal-use agreement, so
integration tests are expensive in a way they normally are not.

## When to Test What

| Test Type | When | Tools |
|-----------|------|-------|
| Unit | Every code change | pytest, fixtures from `raw/` |
| Contract | Every public function, before its arithmetic | pytest |
| Integration | Once per collector, deliberately | live API, one league, few weeks |
| Idempotency | After any collector change | run twice, compare row counts |
| Verification | First live week, and after scoring changes | Yahoo's own displayed totals |

## Automated Tests

```bash
source .venv/bin/activate
pytest
pytest --cov=tilastokeskus --cov-report=term-missing
ruff check tilastokeskus tests
```

**Minimum Coverage**: 80%

Coverage is necessary and not sufficient. The rate limiter was well above 80%
with all five defects present.

## Contract Tests — The Checklist

For every public function, before writing a single arithmetic assertion:

- [ ] **Return distinguishability** — can the caller tell every outcome apart?
      If a success and a failure return the same shape, that is a defect
- [ ] **Every config field is read** — assert the behaviour the field claims to
      control, not that the field exists
- [ ] **Invalid input is refused at construction**, with a clear error, not an
      assertion or a crash deeper in
- [ ] **Boundary values**: 0, negative, above 1.0, empty, None
- [ ] **The named guarantee holds** — if a field is called `max_delay`, assert
      nothing ever exceeds it
- [ ] **No unreachable branch is reachable** — there is a test that greps the
      package for `assert`/`raise` lines labelled unreachable and fails if any
      exist
- [ ] **Refusal paths**, not only happy paths

## Project-Specific Test Requirements

### Loud failure (D-38)
Stubs and unimplemented paths **raise**. Assert the raise and the message, not an
empty return. A test that accepts `[]` from an unimplemented collector cannot
distinguish it from a league with no data.

### Idempotency (D-19)
Every collector gets a test that runs it twice and asserts identical row counts.
Verify directly against the database after a real collection too — the unit test
proves the SQL, the live run proves the keys.

### Week resolution (D-24a)
- Explicit `--weeks` bypasses `current_week` entirely
- Two leagues with different `current_week` resolve independently — a single
  "what week is it" applied to all fifteen is wrong
- A missing `current_week` raises rather than falling back to a date

A wrong week number writes cleanly, because upserts are keyed on week. It
corrupts silently rather than failing, so this is high-value test surface.

### Rate limiting (D-21a)
- Backoff tested against **simulated** 999 and 429 responses, with injectable
  `sleep` and `jitter_source`, asserting the exact delay sequence
- Pacing tested for elapsed-awareness: a slow request or a backoff delay counts
  toward the interval rather than stacking on top of it
- Both ceilings tested: attempt count and cumulative delay
- Retry-After honoured but never shortening the computed delay — a
  `Retry-After: 0` during throttling would become a hot loop

**Nothing throttling in a live run is a data point, not a pass.** It means the
limiter is untested, not that it works.

### Parsing (D-33)
Every parser test uses a fixture captured from the raw archive, not a
hand-written dict. Hand-written fixtures encode the shape you expected, which is
exactly the thing the spike proved wrong. Cover specifically:

- A field absent rather than `0` (`is_owned_by_current_login`)
- The same key holding a list in one record and an object in the next
- A numeric field arriving as a string
- Fields in a different order than last time

### Purge (D-50)
Test the **refusals and the verification**, not the deletion:
- No flags → refuses, exit 2
- `--data` without `--confirm` → dry run, deletes nothing
- Verification fails loudly when deletion silently didn't happen (make `unlink`
  a no-op and assert `PurgeVerificationFailed`)
- `--credentials` rewrites `.env` line by line: the three Yahoo keys blanked,
  every other line byte-identical
- Credential tests run against synthetic files, never the real `.env` — and note
  that `env_file_paths()` reaches `~/.config/tilastokeskus/` too, so "safe
  because I'm in a scratch directory" is false

## Integration Testing Against the Live API

Required after any change to transport, auth, or a collector. Expensive — plan it.

### Setup
1. `tilasto apicheck` — confirm auth is live before anything else
2. Run against **one league, two or three weeks**, never a full sweep
3. `--dry-run` first to confirm the plan

### Checklist
- [ ] `collector_runs` recorded the run, with the right status
- [ ] Retry log: were requests actually paced? Was any throttling seen and handled?
- [ ] Raw archive written **before** parsing, gzipped and dated
- [ ] Row counts match what Yahoo displays for those weeks
- [ ] Re-run changes no row counts
- [ ] A deliberately broken credential produces a loud auth failure, not a retry storm

### Known Failure Modes to Watch For

| Failure | How to Detect | Pattern |
|---|---|---|
| Silent empty result | Collector "succeeds", table has no rows | Stub returned `[]` instead of raising (D-38) |
| Wrong week written | Rows look plausible, disagree with Yahoo | Week computed from a date instead of the league resource (D-24a) |
| Throttle parsed as data | 200-shaped response, garbage rows | Retry keyed off exceptions instead of `(status, payload)` |
| Float drift in sums | `0.30000000000000004` in an aggregate | `FLOAT` instead of `NUMERIC` (D-13) |
| Series breaks mid-season | Rank graph shows two half-length lines | Display name used as a Prometheus label (D-08a) |
| Grafana panel blank after a migration | Only new tables affected | `ALTER DEFAULT PRIVILEGES` not applied before the migration (D-41) |
| Collector appears dead | No runs for days | Host booted into another OS — expected, not a defect (D-40) |

## Verification Against Yahoo

The one check that catches scoring misinterpretation. No amount of unit testing
substitutes for it.

After the first live week collects, pick one league and compare, by hand:
- Each team's weekly points total against Yahoo's displayed total
- Each team's record and rank against the standings page
- One roster's start/sit against the matchup page

A systematic offset here means the scoring settings were misread, which silently
invalidates every derived number in the dashboard.

## Bug Report Template

```markdown
### Bug
[Brief description]

### Context
- Command: [exact tilasto invocation]
- League key / week: [470.l.xxxxx, week N]
- collector_runs id: [if the run was logged]
- Raw archive path: [raw/{date}/... — the payload is reproducible without the API]

### Expected
[What should happen]

### Actual
[What actually happened]

### Output
```
[Traceback, or journalctl --user -u tilastokeskus-collect -n 50]
```

### Reproduction
[Can it be reproduced from the archived payload alone? If yes, no API call needed.]
```

## Test Data Management

- **Fixtures come from the raw archive**, captured during the spike or a real
  run, redacted of manager names before being committed
- **No fixture file is committed un-redacted.** Raw archives contain
  league-member information and are gitignored
- **The empty-table state is a fixture too** — a fresh database is the cheapest
  way to test the migration and the purge
- **Prior seasons are available** as complete, static, real test data if ever
  needed (D-17), subject to the collection scope decisions
- **Unobserved cases have no fixtures.** Points scoring, auction drafts and
  FAAB are absent from all fifteen leagues' `/settings` (2026-10-08), and the
  code raises on them rather than branching speculatively. Test the raise, with
  a fixture edited from an archived payload — not the branch that isn't there

### Cleanup
```bash
tilasto purge --data --confirm     # empties every table and the raw archive
tilasto migrate                    # schema_migrations is never purged
```

### AI tool handling (D-49)
Payloads pasted into an AI tool are governed by the agreement: delete per the
retention requirement recorded in `AGREEMENT.md`, and record the date. Prefer redacted shapes over raw values — no names, IDs or
point totals.

## Debugging Workflow

1. **Reproduce** — from the raw archive first. A parsing bug needs no API call
2. **Isolate** — one league, one week, `--dry-run`
3. **Diagnose**
   - `journalctl --user -u tilastokeskus-collect -n 50`
   - `collector_runs` — status, error text, rows written
   - `raw/{date}/` — the payload as received, before parsing
   - `tilasto apicheck` — is this auth, or is it the collector?
4. **Fix** — minimal change
5. **Verify** — re-run, confirm row counts
6. **Test** — add the regression test, and ask whether it belongs in the contract
   checklist above rather than only as a one-off

## Lessons Learned

### Rate limiter (2026-09)

| Bug | Root Cause | Prevention |
|-----|------------|------------|
| 401 indistinguishable from 200 | Returned payload only, no status | Every exit path is a 2xx payload or an exception — no status for a caller to forget |
| `request_interval` never read | Config field added, behaviour never implemented; reported as done | Assert the behaviour a config field claims to control |
| `max_attempts=0` hit "unreachable" | No input validation | `__post_init__` validation; `while True` so termination is only return or raise |
| `max_delay` exceeded by 20% | Jitter applied after the cap | Assert the guarantee the name makes |
| Negative delay from `jitter > 1.0` | No input validation | Boundary values in every validation test |
| A test enshrined the broken contract | Test written against observed behaviour | Rewrite the bad test; never supplement it |

### Transport (2026-09)

| Bug | Root Cause | Prevention |
|-----|------------|------------|
| Retry layer would never have fired | Yahoo's 999 throttle arrives as a non-error status, and `raise_for_status()` only raises for 400–599 | Key retry logic off `(status, payload)`, never exceptions |
| `yahoofantasy` login broken | Library calls `ssl.wrap_socket`, removed in Python 3.12 | Verify third-party libraries import *and run* on the target interpreter |
| No request timeout; one response took 135s (2026-10) | `requests` waits forever by default, and the transport never passed `timeout=`. `apicheck` had one; the transport was written separately and nobody compared them | Assert the timeout on the wire, not that a constant exists. A rule the probe follows is a rule the collector needs |
| One API-call gap of 0.62s against a 1.0s interval (2026-10) | The pacer marked the start of an attempt, and a token refresh inside the attempt then ate into the gap. The existing pacing test never refreshed a token mid-run | Pace immediately before the wire call; test gaps between the calls themselves, with a fake clock that the refresh advances |

### Schema / Postgres (2026-09)

| Bug | Root Cause | Prevention |
|-----|------------|------------|
| `tilasto_ro` would lose access on the next migration | `DROP DATABASE` takes `pg_default_acl` with it | Re-apply `ALTER DEFAULT PRIVILEGES` before the migration, then prove it against a table created afterwards |
| Default privileges appeared to work | A one-off `GRANT SELECT ON ALL TABLES` looks identical today | Probe test: create a table, confirm `tilasto_ro` can read it, drop it |

### Spike (2026-10)

| Bug | Root Cause | Prevention |
|-----|------------|------------|
| Schema assumed JSON and fixed field positions | Designed from documentation, not observed payloads | Fixtures come from the raw archive; parse by name |
| `461` recorded as the 2026 game key | A real key attached to the wrong season | Never hardcode a game key; read it from the league resource |

### Collector (2026-10)

| Bug | Root Cause | Prevention |
|-----|------------|------------|
| `client.settings(key)` called a `Settings` object | `YahooClient.__init__` stored the app config as `self.settings`, and an instance attribute shadows a method of the same name | A test per public method that calls it, not only checks it exists. The completeness test (every public method has a row) is what made the call happen |
| A dry run with `--weeks` planned a run that would be refused | The unbuilt-scope check lived only in `run()`; the dry run had its own path | One `require_buildable()` called by both paths. A dry run must refuse exactly what the run refuses |

### Documentation (2026-10)

The first entries whose root cause is a claim in prose rather than code — which
is why they slipped through. PRP scoring has a Payload confidence dimension;
nothing applied that standard to the documents the PRPs are generated from.

| Bug | Root Cause | Prevention |
|-----|------------|------------|
| Auction and FAAB documented as impossible in all fifteen leagues | One league's `/settings` generalized to fifteen, in `CLAUDE.md` and `docs/PLANNING.md`. Combined with "unobserved cases raise", a collector built to the docs would raise on real data — a bug introduced via documentation | Every claim about the data states its sample: "all fifteen" or "one league". A claim about leagues not fetched is a task, not a fact |
| An agreement deadline written into `docs/TESTING.md` | The D-52 rule was breached by the person writing the docs about the rule; vigilance was the only control | `tests/test_agreement_terms.py`, asserting expected match counts per file. A plain grep was tried first and rejected: its known hits were the rule's own wording, and a check that always cries wolf gets skimmed |

---

## Pre-Commit Checklist

- [ ] All tests pass
- [ ] Coverage meets minimum (80%)
- [ ] `ruff check` clean
- [ ] Contract checklist applied to every new public function
- [ ] No secrets, token file, raw archive, or agreement text in the diff
- [ ] `tests/test_agreement_terms.py` passes (D-52). It counts agreement-term
      matches per file and fails when a count changes, rather than reporting hits:
      the known matches are the rule's own statements, and a check that always
      reports them gets skimmed (D-23). A deadline from the agreement was once
      written into this very file
- [ ] No host name or address in a committed file
- [ ] New decisions recorded in `docs/DECISIONS.md`
- [ ] `docs/TASK.md` updated

## Pre-Collection Checklist

Before the first sustained run of any collector:

- [ ] `--dry-run` output reviewed
- [ ] Rate limiter unit-tested against simulated 999 and 429
- [ ] Scope: one league, few weeks — never a full sweep first
- [ ] Retry log readable, with response code and applied delay per retry
- [ ] `collector_runs` wrapper in place, so the run is observable
- [ ] Raw archiving in place, so a parse bug costs a re-parse not a re-fetch
