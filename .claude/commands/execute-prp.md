# Execute PRP

Execute a Project Requirement Plan step-by-step.

## Arguments
- `$ARGUMENTS` - Path to the PRP file (e.g., `prps/prp-01-collector.md`)

## Instructions

You are executing a PRP for Tilastokeskus.

### Step 0: Pre-flight Checks

Before starting:
1. Read `CLAUDE.md` for conventions, parsing rules, and the DO NOT list
2. Read the PRP at `$ARGUMENTS` completely
3. Read `docs/TESTING.md` — the contract checklist applies to every public
   function you write
4. Verify all dependencies are met, in foreign-key order where relevant
5. **Check the confidence score is ≥ 7.** If not, stop and report the concerns
6. **Check Known Issues in `docs/TASK.md`.** An open blocker there — a pending
   migration revision, an unrotated credential — may mean this PRP cannot run yet
7. Confirm the venv is active and the working tree is clean

```bash
source .venv/bin/activate
git status
```

### Step 1: Execute Implementation Steps

For each implementation step:

1. **Announce** — state which step you are starting
2. **Implement** — write the code
3. **Follow conventions** — match existing patterns; parse by name, coerce
   explicitly, raise rather than return empty
4. **Validate** — tests and lint after each step
5. **Commit** — after validation passes

```bash
pytest
ruff check tilastokeskus tests
git add .
git commit -m "{type}: {description}"
```

Each commit leaves the package installable and the suite passing. A step that
cannot be left half-done was too big — split it rather than committing a broken
intermediate state.

### Step 2: Handle Failures

If a step fails:
1. **Diagnose** — understand what went wrong before changing anything
2. **Fix** — minimal change
3. **Document** — note the issue and the fix in the commit message
4. **Continue** — only when the step validates

If unable to proceed:
1. Report the blocker clearly
2. Suggest potential solutions
3. Ask for guidance before continuing

**Do not work around a blocker by weakening a test or returning a placeholder.**
A stub that returns empty is indistinguishable from a league with no data (D-38).

### Step 3: Live Verification

There is no deployment step. Verification is against the live Yahoo API, and it
is rate limited under an agreement granted for modest personal use — so it
escalates rather than sweeping (D-21a).

```bash
tilasto apicheck                                    # auth live?
tilasto {command} --dry-run                         # plan only; one discovery request
tilasto {command} --league {one key} --weeks {2-3}  # smallest real run
```

Then, in order:
1. Read the retry log — were requests actually paced? Nothing throttling is a
   data point, not a pass
2. Inspect the written rows against what Yahoo displays for those weeks
3. Re-run the same command and confirm row counts are identical (D-19)
4. Only then widen scope

Work through the PRP's Integration Test Plan, recording pass or fail for each.
Fix failures before proceeding.

### Step 4: Final Validation

1. Run the full test suite
2. Verify every success criterion **by execution, not by reading the code.** A
   feature is not complete because it was reported complete — `request_interval`
   was marked done with only the config field present and no behaviour behind it
   (D-44)
3. Check for regressions
4. Confirm coverage ≥ 80% and `ruff check` clean
5. Confirm no source file exceeds 500 lines

### Step 5: Update Documentation

1. `docs/TASK.md`:
   - Move the task from "In Progress" to "Recently Completed"
   - Update the **Spec Numbering** row: status, init and PRP links
   - Add anything learned to "Architecture Decisions"
   - Add anything discovered but not fixed to "Known Issues"
2. `docs/DECISIONS.md` — add an ADR for any architectural decision made during
   execution. Entries are append-only: if this reverses an earlier decision,
   mark that one **Superseded** and keep its reasoning
3. `docs/TESTING.md` — if a bug was found, add it to Lessons Learned with root
   cause and prevention. That table is what compounds

### Step 6: Report Completion

```
## PRP Execution Complete

**PRP**: prps/prp-{nn}-{slug}.md
**Status**: Complete / Partial / Blocked

### Commits Made
- {hash}: {message}

### Tests
- Unit: X passing, Y% coverage
- Live verification: {scope run — leagues, weeks}

### Success Criteria
- [x] Criterion 1 — verified by: {command run, what it showed}
- [ ] Criterion 2 — incomplete because {reason}

### Request Volume
{calls issued during verification}

### Issues Encountered
{What went wrong and how it was resolved}

### Follow-up Items
{What should happen next; anything added to Known Issues}
```

State how each criterion was verified, not just that it was. "Tests pass" is not
verification of a behavioural claim.

## Example Usage

```
/execute-prp prps/prp-01-collector.md
```

## Commit Message Format

Conventional commits:
- `feat: collect league settings and draft type`
- `fix: parse transaction player detail as list or object`
- `refactor: extract week resolution from collect`
- `test: cover absent is_owned_by_current_login`
- `docs: record spike findings in DECISIONS`
- `chore: rotate Discord webhook`

## Quality Standards

- **No source file over 500 lines** — split if approaching; documents are exempt
- **Tests for new code** — 80% coverage minimum
- **Contract before arithmetic** — return distinguishability, config fields
  actually read, invalid input refused at construction
- **No lint errors**
- **Working commits** — each leaves the package installable and tests passing
- **Clear commit messages** — what and why

## Emergency Stop

**STOP** and report before proceeding if you encounter:

- A **security or credential exposure** — a secret, token, or webhook URL in a
  tracked file, a log, or terminal output
- **Data loss risk** — anything destructive outside `tilasto purge`, or a
  migration against tables that already hold rows
- **A contradicting ADR** — `docs/DECISIONS.md` says otherwise and the PRP did
  not flag it
- **Unclear requirements** — an open question that was never answered
- **Collection scope beyond what the PRP specifies** — this project collects
  under an agreement, and scope is a decision, not an implementation detail
- **Repeated throttling** from the API, or any response shape the parser was not
  designed against
- **Anything that would write to the database before a pending migration
  revision lands** — check Known Issues

## Notes

- Take your time — quality over speed
- Ask questions if anything is unclear
- Deviating from the PRP is fine when you find a better approach, but document
  why, and add an ADR if it is architectural
- The characteristic failure of this project is **silence**: a run that succeeds
  and writes nothing, a config field with no behaviour behind it, an alert that
  has never fired. Prefer loud failure everywhere
- Verify by execution. Reporting a thing done does not make it done
