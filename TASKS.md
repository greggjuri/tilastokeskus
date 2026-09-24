# Tasks

Chronological. Phases 0 through 2 are done: repository groundwork, the access application and
agreement, and everything buildable without a token. Phase 3 onward needs the token, which now
exists.

Rationale for anything non-obvious lives in `DECISIONS.md`, referenced as D-nn.

Last updated 2026-09-24.

**Now:** access provisioned, Confidential Client app created with read-only Fantasy Sports scope,
token obtained. **Phase 2 is complete** — database live, schema migrated, read-only role verified,
timers enabled with lingering. **Phase 3 is the active phase**, and it is the first work in this
project that touches the real API.

**Unblocked 2026-09-24 (D-47, D-48).** Two questions about the API agreement were put to Yahoo on
2026-09-01 and **neither was answered.** Collection proceeds regardless, on the reading that the
approved use case implies local persistence — as designed, at full scope, with no reduced schema or
shortened retention offered in mitigation. Nothing below is blocked any longer.

This is a decision taken without the answer, not an answer received. It is recorded that way in
`AGREEMENT.md` (not committed, D-52), which keeps the original reasoning intact and states the
accepted risk. The practical consequence for this file: **`tilasto purge` (D-50) gets built early
rather than eventually** — it is the mechanism if the decision is ever reversed, and it is the one
item here whose priority went up when collection was unblocked.

First run discipline, unchanged by any of this: the transport and limiter are verified only against
fakes, so phase 3 starts with calls that persist nothing, and phase 5 escalates one league at a time
(D-21a).

---

## Phase 0 — Repository groundwork · Complete

- [x] Create the GitHub repository `greggjuri/tilastokeskus`, public (D-30)
- [x] Write the README, including attribution and disclaimer (D-31)
- [x] Write `.gitignore` covering credentials, token files, certs, raw archives, and DB dumps (D-28)
- [x] Add `.env.example` documenting required configuration without real values
- [x] Gitignore the project brief — internal host addresses, public repo (D-30)
- [x] Correct the data availability model across the README and brief (D-16 → D-17)

Committed and pushed. Remote holds `README.md`, `.gitignore`, `.env.example`.

---

## Phase 1 — Yahoo API access · Complete for access purposes; the clarification was never answered

Approved 2026-08-28; agreement signed 2026-09-01; access provisioned and token obtained 2026-09-24.
Countersignature still outstanding, which changes nothing (D-46).

This gated every task from phase 3 onward. It gates nothing now.

- [x] Sign the API Access and Use Agreement; record its constraints in `AGREEMENT.md`, keep it
      uncommitted, and leave stubs in `DECISIONS.md` (D-46, D-52)
- [x] Draft the use-case text: data required, storage, user count, no redistribution (D-26)
- [x] Submit at <https://sports.yahoo.com/developer/access/>, answering as an individual (D-26)
- [x] Wait. Nothing was lost by waiting — the backfill recovers it all (D-17)
- [x] Register the YDN application: **a new Confidential Client app**, Fantasy Sports · Read,
      redirect `https://localhost:8000`, OpenID Connect and TW Auction both unchecked (D-27)
- [ ] **Record `client_id` / `client_secret` into `.env`** from `.env.example` (D-28). `.env`
      currently has `YAHOO_CLIENT_ID` and `YAHOO_CLIENT_SECRET` **empty** — the new app's
      credentials are not on this host yet, and nothing in phase 3 can run until they are
- [ ] **Write the deletion procedure — `tilasto purge`, or a documented manual one (D-50).** Moved
      up: it is the mechanism if the D-47 decision is reversed, and its scope grows the moment
      collection starts. Build it before the backfill, not after

**Get an answer to D-47 and D-48 — asked 2026-09-01, never answered.** Deliberately not a checkbox
in either state. It was not done, and it is not outstanding work either: collection was unblocked on
2026-09-24 by decision rather than by reply, on the reading that the approved use case implies local
persistence. The reasoning and the accepted risk are kept in full in `AGREEMENT.md`. Any reply from
Yahoo that addresses it reopens this and everything downstream of it.

---

## Phase 2 — Work that needed no token · Complete

Ordered so that each step is testable when the one before it is done. All of it was written against
fakes, which is why none of it waited on phase 1.

### Package scaffolding · Complete

- [x] `python3 -m venv .venv` (D-05)
- [x] `pyproject.toml` with the `tilasto` console entry point (D-32)
- [x] Package skeleton: `tilastokeskus/{__init__,cli,config,db,migrate,weeks,yahoo,collect}.py`
- [x] Config loading — `.env` and `~/.config/tilastokeskus/`, season computed rather than
      hardcoded, overridable by `--season` (D-03, D-37)
- [x] CLI: `leagues`, `collect`, `status`, `migrate`, parsing `--backfill --weeks` and `--season`
      from the outset (D-18, D-36)
- [x] Stubs fail loudly with a reason and a pointer, never silently (D-38)
- [x] Tests for week parsing and season rollover; `ruff` clean, 22 tests passing

Verified: argument handling, mutually exclusive `--all` / `--league`, week-range rejection,
and that the Postgres connection error names the target without leaking the password.

### Schema · Applied

- [x] `tilastokeskus/migrations/001_initial.sql` — all ten tables, indexes named explicitly
      (D-11 … D-15, D-35)
- [x] `tilasto migrate` — applies in filename order, tracks versions, one transaction each
- [x] Applied against the real database; every foreign key and index created cleanly
- [x] **Re-applied after the `leagues.tier` edit, 2026-09-24.** `001_initial.sql` had gained a
      column after being applied, and `schema_migrations` recorded `001` as done, so `tilasto
      migrate` would not pick it up. Dropped and recreated while the tables were still empty —
      the last moment this was free rather than a `002` (D-54)
- [x] **Read-only wiring re-applied before the migration, not after.** `DROP DATABASE` destroys
      the per-database catalog, `pg_default_acl` included, so the drop reintroduced the exact
      D-41 trap the original bootstrap had avoided. `GRANT CONNECT`, schema `USAGE`, and
      `ALTER DEFAULT PRIVILEGES` were re-run on the new database *before* `tilasto migrate`.
      The `CREATE ROLE` half of the README block was deliberately not re-run: roles are
      cluster-level and survived the drop
- [x] Verified against the pre-drop baseline: `leagues.tier` present, 11 tables / 23 indexes /
      15 foreign keys unchanged, all owned by `tilasto_app`, `tilasto_ro` holding SELECT on all
      11 and no INSERT / UPDATE / DELETE on any, `CONNECT` and schema `USAGE` yes and schema
      `CREATE` no. Default privileges re-proven against a table created *after* the migration —
      readable immediately, not writable — which is the check that distinguishes working default
      privileges from a one-off `GRANT SELECT ON ALL TABLES` (D-41)

Treat the migration as provisional until the spike confirms the payload shapes (D-33). Because
the tables are empty, revising `001_initial.sql` and recreating the database is still cheaper
than writing a `002` migration — that stops being true the moment real data lands, which on the
current plan is the phase 3 spike. **The `tier` recreate above was the last free one.**

### Scheduling units · Complete

- [x] `tilastokeskus-collect.service` — oneshot, user unit, no privilege (D-09, D-34)
- [x] `tilastokeskus-collect.timer` — daily, `Persistent=true`, randomized delay (D-53)
- [x] Installed to `~/.config/systemd/user/`, enabled, and test-fired with the collector still
      a stub — confirmed the timer schedules, the service runs the venv CLI, it fails loudly,
      and journald captures the reason

Getting the plumbing verified before there is anything to collect means the first real run tests
the collector alone.

- [ ] `loginctl enable-linger` so timers run without an active login — needs sudo

### Database · Complete

**One privileged session, not a handoff partway through** (D-41). A fresh cluster has only the
`postgres` role, so role creation and `createdb` need `sudo -u postgres` just as much as the
install does. The full command block is in the README under Setup → Database.

- [x] `sudo apt install postgresql` (D-06)
- [x] Create roles `tilasto_app` and `tilasto_ro` with real passwords (D-10)
- [x] `createdb -O tilasto_app tilastokeskus` — app role owns the database, which is what lets it
      create tables under Postgres 15+ schema rules (D-41)
- [x] `GRANT CONNECT` / `GRANT USAGE`, then **`ALTER DEFAULT PRIVILEGES FOR ROLE tilasto_app`
      before the first migration** (D-41)
- [x] `tilasto_app` password in `.env`; TCP connection confirmed
- [x] `sudo loginctl enable-linger` so user timers survive logout (D-34)

Then, with no further sudo:

- [x] `tilasto migrate` — `001_initial` applied; re-running reports "schema is up to date"
- [x] 10 schema tables plus `schema_migrations`, all owned by `tilasto_app`; 23 indexes,
      15 foreign keys, all created cleanly
- [x] `tilasto_ro` has SELECT on all 11 tables and no INSERT, UPDATE, or DELETE on any;
      `CONNECT` and schema `USAGE` yes, schema `CREATE` no
- [x] **Default privileges proven against a future table**, not just the existing ones: a table
      created by `tilasto_app` after the fact was readable by `tilasto_ro` immediately and not
      writable. This is the check that distinguishes working default privileges from a one-off
      `GRANT SELECT ON ALL TABLES` that would leave the next migration ungranted (D-41)

Still requiring root, not needed until Grafana connects in phase 6:

- [ ] `listen_addresses` and a `pg_hba.conf` entry scoped to the LAN, not `0.0.0.0/0` (D-04)

---

## Phase 3 — First contact with the API · **Active**

The token exists and D-47 no longer blocks the spike. Still ordered so the calls that persist
nothing come first: the transport and limiter have only ever run against fakes (D-44), so auth and
pacing get confirmed before anything is written to disk.

- [x] Put the new app's `client_id` / `client_secret` into `.env` (phase 1)
- [x] **Obtain a refresh token — but not via `yahoofantasy login`, which cannot run here.**
      `yahoofantasy` 1.4.9 calls `ssl.wrap_socket` to start its HTTPS callback server, and that
      was removed in Python 3.12; this venv is 3.13, so the command aborts with `AttributeError`
      every time. The browser half of the flow is unaffected — Yahoo still redirects to
      `https://localhost:8000/?code=...` — so the authorization code was read out of the address
      bar of the failed page and exchanged directly against the token endpoint. `YAHOO_REFRESH_TOKEN`
      is now in `.env`, which `resolve_refresh_token()` checks before the file (`transport.py:93`)
- [x] Confirmed the refresh token is **stable**: Yahoo returns the same value on each exchange, so
      the `.env` entry does not go stale. The transport keeps a rotated token in memory but never
      writes it back (`transport.py:164`) — correct as written, and moot while Yahoo does not rotate
- [ ] **BLOCKED — the application is not authorized for the Fantasy API (2026-09-24).** Auth is
      fine and the failure is Yahoo-side. Evidence: a deliberately invalid token returns `401
      token_rejected`, while our token returns `403 "This application is not authorized to perform
      this action."` on every path including `/fantasy/v2/game/nfl`, the simplest resource there is.
      A valid credential rejected at the application level means Fantasy access is not live for this
      client ID. **Both apps were tested and both fail identically** — see the note below
- [ ] Verify: list leagues for `nfl`, 2026 — fifteen league keys should return. If the 2026 game key
      is not live yet, try 2025 to confirm auth works, then return to 2026
- [ ] **Read-only spike** — dump one league's draft, one roster, and one transaction list to raw
      JSON. One league, not fifteen: this is a shape check, not a collection run
- [ ] Do not write parsing code yet, and prefer redacted shapes over real values when comparing
      payloads by hand — field names, types, nesting, and cardinality answer the schema question and
      player names do not (D-49). Where a real payload is pasted into an AI tool, note the date: the
      handling obligation in D-49 is unaffected by the D-47 decision and starts a clock

**On the 403 — both apps were tested, and the app-binding theory is wrong.**

The first hypothesis was that the 2026-08-28 approval was bound to the original Public Client app and
did not extend to the newly created Confidential Client. That was tested directly on 2026-09-24 and
**disproved**: the old public app returns the same `403 "This application is not authorized to
perform this action."` on the same paths, including `/fantasy/v2/game/nfl`.

Testing it required implementing PKCE, which is worth recording because it is a genuine finding about
Yahoo rather than a detour. Yahoo rejects a Public Client authorization with `invalid_request /
"invalid code challenge or method"` unless `code_challenge` and `code_challenge_method=S256` are
supplied — PKCE is the standard substitute for a client secret, and `yahoofantasy` does not implement
it. With PKCE supplied the flow completed cleanly and returned a working token **with no client
secret involved**, so a Public Client is a viable shape for this project if it is ever wanted. It was
then the Fantasy API, not the OAuth layer, that refused.

A third hypothesis was tested on the same day and also **disproved**: that the token was scopeless.
Yahoo's own OAuth documentation omits `scope` from the authorization request entirely, and neither
`yahoofantasy` nor the manual exchange had been sending one. Requesting `scope=fspt-r` explicitly
changes nothing — the API returns the same 403.

That test produced the strongest evidence in this whole sequence, and it points at Yahoo rather than
at us. Yahoo's authorization endpoint **validates scopes against the application**: it accepts
`fspt-r`, and rejects both `fspt-w` and an invented scope name with `invalid_scope`. So Yahoo's OAuth
layer agrees the app is registered for Fantasy Sports read access, issues a token carrying that
scope, and the Fantasy API then refuses it as an unauthorized application. Those two positions
contradict each other, and only Yahoo can reconcile them.

So the position is: **no application on this account can call the Fantasy API**, and the three
hypotheses available from this side — wrong app, wrong client type, missing scope — are each tested
and eliminated.

**Identified 2026-09-24: this is a platform-wide change, not an account problem.** Yahoo altered the
Fantasy API access model on or around **2026-07-22**, ending self-serve provisioning and revoking
access for applications created under the previous flow. Since then every endpoint returns this exact
error for correctly configured apps whose settings still show the Fantasy Sports permission checked.
The reported diagnostic signature is precisely what was observed here: the token endpoint returns 200
while `/fantasy/v2/...` returns 403 — tokens fine, application not approved. Other developers report
trying the same three fixes attempted above, with the same non-result:

- <https://github.com/uberfastman/yfpy/issues/84>
- <https://github.com/derekrbreese/fantasy-football-mcp-public/issues/18>

Three consequences worth having written down, because each one contradicts an assumption made earlier
in this file:

- **There is no Yahoo developer support address or forum.** The access request form is the only
  channel and Yahoo initiates contact. Any plan here that reads "send Yahoo a message" needs a
  specific human on an existing thread, not a support queue. The countersignature correspondence
  (D-46) is the live one; the address in `AGREEMENT.md` is for breach notification only and is the
  wrong channel for this.
- **Re-authorizing, re-saving the app, or creating another app does not help.** Confirmed here twice
  and corroborated externally. Do not spend another round on it.
- **The public reports are unresolved**, with no published turnaround and no confirmation from anyone
  who got access working after approval. There is no SLA to measure the wait against.

This project is nonetheless further along than the reports: access here is approved (2026-08-28) and
the agreement signed (2026-09-01), where those threads are still queued. "Approved but not yet
provisioned" is consistent with lag on a process Yahoo is visibly still building. The cheap move is
to retry daily for a week before treating it as stuck.

Two readings remain, and neither is distinguishable from here:

- **Provisioning has not actually taken effect**, whatever the account page indicates. This is the
  reading the evidence favours — the failure is identical across two apps of different types and
  survives an explicitly scoped token, which is what an account-level or grant-level gap looks like
  rather than a per-app misconfiguration.
- **Propagation delay.** Provisioning completed very recently, and Yahoo's Fantasy authorization may
  lag the account state by some unknown interval. Cheap to rule out: retry in a few hours.

What this does *not* cast doubt on, tested end to end against the real API: the OAuth flow in both
Confidential and Public form, scoped and unscoped, the credentials, the token refresh, the transport,
and the backoff. The 403 arrives from Yahoo with a valid, correctly scoped bearer token attached.
Everything this project controls works.

**What to put to Yahoo** — on the existing countersignature thread, since no support channel exists. Access was
approved 2026-08-28 and the agreement signed 2026-09-01, but every application on the account returns
`403 "This application is not authorized to perform this action."` on every Fantasy endpoint,
including `/fantasy/v2/game/nfl`. Both client IDs behave identically. `api.login.yahoo.com` accepts
`scope=fspt-r` for these apps while rejecting invalid scopes, so the permission is registered at the
authorization layer and refused at the API layer. Quote the error string and both client IDs; that
pair of facts is the checkable part.
- [ ] Compare observed payloads against the schema and record every discrepancy (D-33)
- [ ] Revise `001_initial.sql` before it is treated as settled — `transactions.payload`,
      `player_weekly_stats.stats`, `eligible_positions`, `draft_type`, `scoring_type`
- [ ] Confirm real key formats and that keys are stored verbatim, game_key prefix intact (D-11)
- [ ] **Record the real `game_key` integers** and correct the illustrative examples in
      `DECISIONS.md`, `001_initial.sql`, and the brief. `461`/`449` are guesses (D-11)
- [ ] Capture a real throttle response if one occurs — confirm it is status 999 and note whether
      Yahoo sends `Retry-After`. Both are assumed, not observed (D-42, D-43)
- [ ] Confirm `current_week` is present on the league resource and note whether it differs
      between the fifteen leagues (D-24a)

---

## Phase 4 — Collector

Build in dependency order; each table's foreign keys require the one before it.

**Unblocked 2026-09-24.** The checked items — the limiter, the transport, their tests — were never
blocked and stay done. What was blocked is the part that writes rows, and it now proceeds at full
scope, `rosters` included.

- [ ] Raw response archiving — gzipped, dated, written before parsing (D-20)
- [ ] `collector_runs` logging wrapper around every run, success or failure (D-22)
- [x] Rate limiting with bounded exponential backoff on 999 and 429, two ceilings, and jitter
      (D-21, D-21a, D-42)
- [x] Conservative inter-request pacing, applied by `Pacer` and elapsed-aware so a slow request
      counts toward the gap rather than adding to it (D-21a)
- [x] **Unit-tested against simulated 999 / 429 responses**, asserting the exact sleep durations
      rather than only the retry count — 71 tests, no token required (D-42)
- [x] Contract tests alongside the arithmetic: return values on every non-happy path, rejection
      of invalid policies, named bounds actually bounding (D-44)
- [x] `RetryLog` records every wait with its status and applied delay (D-21a)
- [x] Confirmed `yahoofantasy` 1.4.9 does no retrying of its own, so no layering conflict (D-43)
- [x] **Request path decided and built** — `YahooTransport` owns requests and token refresh;
      `yahoofantasy` is kept for the `login` browser flow only. Every request goes through
      `call_with_backoff` with pacing, so the limiter has a real call site (D-45)
- [x] Transport tested contract-first against an injected session: token reuse, rotation,
      early refresh, rejected refresh, 999 retried, 404 raised, pacing applied (D-44, D-45)
- [ ] Exercise the transport against Yahoo itself. Everything above is verified against fakes;
      nothing has yet proven correct against the real API
- [ ] Tune `request_interval` and the ceilings against observed behaviour; the shipped defaults
      are deliberately conservative guesses (D-21)
- [ ] Transparent token refresh that fails loudly on revocation (D-29)
- [ ] Collect `leagues` → `teams` → `players` → `draft_picks` → `rosters`, all idempotent upserts
      (D-19). Dependency order is a foreign-key requirement, not a preference
- [ ] `rosters` at full scope — every team's roster weekly, in all fifteen leagues. This is the
      scope D-48 asked about and never got an answer on; it is collected as designed, and it is the
      table most likely to be narrowed if a reply ever arrives
- [ ] Test each collector's **contract**, not only its parsing: what it returns on a malformed
      payload, a missing field, an empty list; what inputs it refuses. Parsing logic wrapped in a
      thin contract is exactly the shape that hid five defects in the backoff module (D-44)
- [ ] Flag `teams.is_owned_by_me` — it drives dashboard filtering
- [ ] Verify idempotency directly: run twice, confirm row counts are identical

---

## Phase 5 — Backfill · Unblocked, and the heaviest thing this project does

The whole phase writes Yahoo data to disk, which is what D-47 was about; it proceeds as designed.
What has not changed is that this is the first sustained load on the token and the first time the
limiter runs outside a unit test, so the escalation below is followed in order (D-21a).

Deliberately before the season starts, because prior seasons are the only complete test data
available (D-17).

**Prior seasons are test data, not scope.** They validate the collectors and are then left in the
database unread — no dashboard surfaces them and no cross-season feature is built on them (D-01,
amended).

This is the first sustained load ever placed on the token, and the first time the rate limiter
runs outside a unit test. **Escalate; do not open with a full season** (D-21a).

- [ ] `tilasto collect --backfill --weeks N-M` over the existing collectors (D-18)
- [ ] `--season` override for prior seasons
- [ ] Confirm the prior season is genuinely still retrievable before relying on it as the
      fixture — Yahoo's retention for an account is assumed, not verified

Then, in this order, stopping at each step to look at what happened:

- [ ] **One league, two or three weeks.** Exercises every collector at minimum cost
- [ ] Read the retry log: were requests paced, was any throttling seen and handled? Nothing
      throttling is a data point, not a pass — it means untested, not working (D-21a)
- [ ] Inspect the rows written; confirm they match what Yahoo displays for those weeks
- [ ] **One league, full season.** Confirms `matchups`, `standings`, and `player_weekly_stats` —
      the last at full league-wide scope (D-48, proceeding unanswered)
- [ ] Re-run that same backfill and confirm nothing duplicates (D-19)
- [ ] **Widen to the remaining leagues**, only once the above is clean

---

## Phase 6 — Grafana

- [ ] Add Postgres as a data source on the Grafana host, connecting as `tilasto_ro` (D-10)
- [ ] Dashboard uid `tilastokeskus` (D-32)
- [ ] A `season` template variable defaulting to the current season, and every panel query
      filtered by it — prior-season test data must not leak into dashboards (D-01, amended)
- [ ] Draft recap panels — real data, available today
- [ ] Attribution in the dashboard description (D-31)

---

## Phase 7 — In-season collection

Unreachable until week 1 has played, but validated ahead of time by the phase 5 backfill.

- [ ] Collect `matchups`, `standings`, `player_weekly_stats` — the last league-wide rather than
      limited to my own fifteen teams, which is the scope D-48 asked about and never got an answer
      on (proceeding unanswered)
- [ ] Standings and rank-over-time panels in Grafana
- [ ] Confirm the first live week against Yahoo's own displayed totals — the one check that
      catches scoring misinterpretation

---

## Phase 8 — Observability

- [ ] Prometheus exporter reading `collector_runs`, metrics limited to the agreed list (D-07)
- [ ] Confirm no player names appear in any label (D-08)
- [ ] Confirm labels are `league_key` / `team_key`, never display names — a mid-season rename
      would otherwise orphan the series and break rank graphs in half (D-08a)
- [ ] Grafana joins display names from Postgres for presentation (D-08a)
- [ ] Scrape config for job `tilastokeskus`
- [ ] Alert on `tilasto_collector_last_success_timestamp` staleness, not only on error (D-23)
- [ ] Collector health panel on the dashboard
- [ ] Verify the alert fires: stop the timer, wait out the threshold, confirm it triggers

An alert that has never fired is a hypothesis, not a safeguard.

---

## Deferred

Not scheduled, recorded so they are not rediscovered as surprises.

- Backup strategy for the database. Backfillable data lowers the stakes, but `collector_runs`
  history and any derived tables are not re-fetchable.
- Retention policy for raw JSON archives — they grow weekly and are never pruned as designed.
- Leagues draft at different times, so `--draft-only` needs a re-run per league rather than a
  single season-wide one-shot (D-53).
- Cross-season queries once a second season exists — joining on `player_id`, not `player_key`
  (D-12).
