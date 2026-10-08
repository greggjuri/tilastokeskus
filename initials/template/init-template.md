# Init Template - Feature Specification

## init-{nn}: {Feature Name}

**Created**: {YYYY-MM-DD}
**Priority**: {High/Medium/Low}
**Phase**: {Phase N from docs/TASK.md}
**Depends On**: {init-nn-{slug}, or "None"}

---

## Problem Statement

{What problem does this feature solve? Why is it needed? 1-3 sentences.}

## Goal

{What will be true when this feature is complete?}

## Requirements

### Must Have (P0)
1. {Requirement 1}
2. {Requirement 2}

### Should Have (P1)
1. {Requirement 3}

### Nice to Have (P2)
1. {Requirement 4}

## Behaviour

{What the feature does, from the outside. Commands run, data that appears,
panels that change. Says WHAT, not HOW.}

{For a collector: which Yahoo resource, which tables, what a successful run
leaves behind.}
{For a dashboard: which panels, which queries, what a reader learns at a glance.}
{For tooling: the command, its flags, its output, its exit codes.}

## Technical Considerations

### Data Changes
- {New tables or columns; changes to existing ones}
- {Does this need a migration? If tables already hold rows, it is a real `002`,
  not an edit to `001_initial.sql`}
- {Which table is the primary key on, and does `week` belong in it? (D-15)}

### Yahoo API Usage

This project consumes the Yahoo API and exposes none.

- **Endpoints**: {paths, e.g. `/league/{league_key}/standings`}
- **Request volume**: {per run — leagues × weeks × calls}
- **Second fetches**: {some fields only come from `/settings`, not the league
  resource — list any}
- **New payload shapes**: {anything not already covered by the spike findings
  in D-33. If this is the first time a resource is parsed, say so}

### CLI / Dashboard Surface
- {New subcommand, flag, or panel}
- {Changes to existing ones}
- {Does `--dry-run` apply? It should, for anything that issues requests}

### Integration Points
- {Which existing modules this touches: `yahoo.py`, `collect.py`, `db.py`, …}
- {Does it run under the systemd timer, or only by hand?}
- {Does `tilasto purge` need to know about anything new this writes? (D-50)}

## Failure Behaviour

The characteristic failure of this pipeline is **silence** — a stale dashboard
looks much like a quiet week in the data. State explicitly:

- **What this does when it cannot do its job.** Raises, with what message?
  Never an empty return where a raise belongs (D-38)
- **What is unobserved and therefore unhandled**, and how widely it was looked
  for. Points scoring, auction drafts and FAAB are absent from all fifteen
  leagues' `/settings`. A path that raises must be one real data cannot reach —
  state the sample behind it: "unobserved in one league" is not "absent from
  fifteen"
- **What a partial run leaves behind**, and whether re-running fixes it (it
  should — every write is an idempotent upsert, D-19)

## Constraints

- **Request volume** is the real budget, not money. Yahoo does not document its
  rate limit, so the safe rate is unknown rather than merely unenforced (D-21)
- **Public repository** — nothing committed names a host, an address, or any
  term of the API agreement (D-30, D-52)
- **Read-only** — Yahoo offers no write access, so nothing is designed around it
- **500-line file limit**
- {Feature-specific constraints}

## Success Criteria

Each must be verifiable by running something, not by reading the code.

- [ ] {Testable criterion}
- [ ] {Testable criterion}
- [ ] Re-running changes no row counts (idempotency proven, not assumed) — for
      anything that writes
- [ ] Contract checklist from `docs/TESTING.md` applied to every new public
      function
- [ ] Coverage ≥ 80% and `ruff check` clean

## Out of Scope

{Explicitly list what this feature does NOT include.}

- {Not included}
- {Not included}

## Open Questions

Answer these before generating the PRP. An unanswered question becomes a guess
in the implementation.

- [ ] {Question}
- [ ] {Question}

## Notes

{Additional context, references, relevant D-nn entries, links to raw archive
payloads that show the shape being parsed.}

---

## Template Usage

**Naming and numbering**

`initials/init-{nn}-{slug}.md`, e.g. `init-01-collector.md`.

- **Numbers are assigned in the order work is taken up**, not the order specs are
  written. An idea sitting in the backlog has a slug but no number
- **The PRP inherits the same number and slug**: `init-01-collector.md` →
  `prps/prp-01-collector.md`. They are always a matched pair
- **Numbers are never reused.** If a spec is abandoned, its number is burned —
  the gap is the record that something was tried and dropped
- `docs/TASK.md` holds the next free number. Take it, increment it there, then
  write the file

**When creating an init file:**

1. Claim the next number from `docs/TASK.md` and increment it
2. Copy this template to `initials/init-{nn}-{slug}.md`
3. Fill in all sections (delete "Notes" if empty)
4. Be specific in requirements — vague specs lead to scope creep
5. Answer all open questions before generating the PRP
6. Check the feature against `docs/DECISIONS.md` — if it contradicts an existing
   ADR, that is a conversation, not a silent override

**What makes a good init spec here:**

- **Clear problem statement**: one clear problem, not several
- **Verifiable success criteria**: provable by execution. A feature is not
  complete because it was reported complete — `request_interval` was marked done
  with only the config field present and no behaviour behind it (D-44)
- **Explicit scope boundaries**: what is in AND what is out
- **Failure behaviour stated**: silence is this project's characteristic failure
  mode, so loud failure is a requirement, not a nicety
- **Payload-shape awareness**: if a new Yahoo resource is being parsed, the spec
  says so and points at an archived payload. Schemas designed from documentation
  rather than observed responses produced ten corrections in one spike (D-33)
- **No implementation details**: says WHAT, not HOW

**After creating:**

1. In Claude Code: `/generate-prp initials/init-{nn}-{slug}.md`
2. Review the generated PRP — do not proceed below confidence 7
3. Execute: `/execute-prp prps/prp-{nn}-{slug}.md`
