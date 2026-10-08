# Generate PRP

Generate a comprehensive Project Requirement Plan (PRP) for a feature.

## Arguments
- `$ARGUMENTS` - Path to the init file (e.g., `initials/init-01-collector.md`)

## Instructions

You are generating a PRP for Tilastokeskus, a self-hosted fantasy football
statistics pipeline.

### Step 1: Gather Context

Read and internalize:
1. `CLAUDE.md` — conventions, parsing rules, DO NOT list
2. `docs/PLANNING.md` — architecture, schema, Yahoo endpoints consumed
3. `docs/DECISIONS.md` — every ADR. Do not contradict one without saying so
   explicitly and calling it out in the PRP
4. `docs/TASK.md` — current status, Spec Numbering registry, Known Issues
5. `docs/TESTING.md` — contract checklist, known failure modes, Lessons Learned

Check **Known Issues** in `docs/TASK.md` before planning. An open issue there
may block this feature or change its shape.

### Step 2: Read the Init File

Read the specification at `$ARGUMENTS`:
1. Understand the requirements
2. Note constraints, especially request volume and scope
3. Identify integration points with existing modules
4. **Check that every open question is answered.** An unanswered question
   becomes a guess in the implementation — stop and ask rather than assuming

### Step 3: Research

**Codebase:**
1. Search for related existing implementations
2. Identify the files that need modification
3. Check `examples/` for patterns to follow
4. Check whether any source file is near the 500-line limit (documents are exempt)

**Raw archive — do this before writing any parsing plan:**
1. If this feature parses a Yahoo resource, find the archived payload under
   `raw/{date}/` and read the actual shape
2. If no archived payload exists for a resource this feature must parse, **say
   so and score Payload confidence low.** The original schema was designed from
   documentation rather than observed responses and produced ten corrections in
   one spike (D-33)
3. Note the shape hazards present: fields absent rather than zero, a key holding
   a list in one record and an object in another, numerics arriving as strings,
   fields with no fixed position

### Step 4: Generate the PRP

**Numbering**: the init file is `init-{nn}-{slug}.md`. The PRP inherits the same
number and slug: `prps/prp-{nn}-{slug}.md`. Never choose a number independently
and never reuse one. If the init file is unnumbered, stop — the number is
claimed from **Spec Numbering** in `docs/TASK.md` when work is taken up.

Use `prps/template/prp-template.md` as the structure. Fill in all sections:

1. **Overview** — problem statement, proposed solution, testable success criteria
2. **Context** — related ADRs by number, dependencies in foreign-key order,
   files to create or modify
3. **Technical Specification** — data changes with the schema checklist, Yahoo
   API usage with calls per run, **payload shapes naming the archived file each
   parser was designed against**, CLI surface
4. **Implementation Steps** — ordered, atomic, each leaving the project
   installable with tests passing. A step that cannot be left half-done is too
   big; split it
5. **Testing Requirements** — contract tests before arithmetic tests; fixtures
   from the raw archive, never hand-written dicts
6. **Integration Test Plan** — escalating, never a sweep: one league and a few
   weeks first (D-21a)
7. **Error Handling** — expected errors, edge cases, and the **Silence Check**:
   what prevents a run that succeeds and writes nothing
8. **Request Volume Impact** — calls per run and per day, not dollars
9. **Open Questions**
10. **Rollback Plan** — re-collecting is usually cheaper than repairing in
    place, since every table is week-keyed and refetchable (D-17)

### Step 5: Score Confidence

Score 1–10 on each dimension:
- **Clarity** — are the requirements unambiguous?
- **Feasibility** — achievable with the current architecture?
- **Completeness** — does the PRP cover all aspects?
- **Alignment** — consistent with `docs/DECISIONS.md`? Any ADR contradicted?
- **Payload confidence** — was every parser designed against an observed
  payload, or is any of it assumed?

Overall confidence is the average.

**If overall confidence is below 7**: list the specific concerns, identify what
would raise the score (usually an answered open question or an archived payload
to design against), ask the clarifying questions, and do **not** proceed.

Score honestly. A PRP that scores itself 8 on assumed payload shapes is worse
than one that scores 5 and says why.

### Step 6: Output

1. Create the PRP at `prps/prp-{nn}-{slug}.md`
2. Report the file path
3. Display the confidence scores, including Payload confidence
4. List open questions and concerns

## Example Usage

```
/generate-prp initials/init-01-collector.md
```

This would:
1. Read all context files
2. Read `initials/init-01-collector.md`
3. Research the codebase and the archived payloads under `raw/`
4. Generate `prps/prp-01-collector.md`
5. Report confidence and concerns

## Quality Checklist

Before completing, verify:

- [ ] PRP filename inherits the init file's number and slug exactly
- [ ] Every implementation step has specific file paths
- [ ] Steps are atomic and individually validatable
- [ ] Each step leaves the project installable with tests passing
- [ ] Every parser names the archived payload it was designed against
- [ ] Test cases cover the contract, not only the arithmetic (D-44)
- [ ] Fixtures come from the raw archive, redacted — not hand-written
- [ ] Integration test plan escalates rather than sweeping (D-21a)
- [ ] Silence Check answered: what prevents a successful run writing nothing
- [ ] Request volume estimated in calls
- [ ] Schema checklist applied if any table changes — including whether this is
      an edit to `001_initial.sql` or a real `002`
- [ ] `tilasto purge` covers anything new that holds Yahoo data (D-50)
- [ ] No ADR contradicted without explicit discussion
- [ ] Rollback plan exists
- [ ] Nothing in the PRP names a host, an address, or any term of the API
      agreement (D-30, D-52)
