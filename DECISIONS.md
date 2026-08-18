# Decisions

A record of what was decided and why. Entries are append-only: when something is reversed, the
original stays and is marked superseded, because the reasoning that was wrong is usually more
useful later than the conclusion that replaced it.

Status values: **Active** · **Superseded** · **Open** (decided in principle, not yet validated
against real data).

Last updated 2026-08-17.

---

## Scope

### D-01 — Eight Yahoo NFL redraft leagues, 2026 season · Active

Single user, read-only, private, non-commercial. Nothing is redistributed, resold, or exposed to
third parties.

Four of the eight leagues have drafted as of 2026-08-17; four have not. All eight exist as leagues
with teams, so league and team collection works today regardless.

**Amended 2026-08-17 — prior seasons are in scope as test data only.** Phase 5 backfills a
completed prior season because 2026 cannot populate `matchups`, `standings`, or
`player_weekly_stats` until games are played, and writing those collectors against no data at all
means writing them blind. That is a testing need, not a scope expansion:

- Prior-season rows are collected, stored, and used to validate the collectors.
- They are **not surfaced in Grafana.** Dashboard queries filter on the current season — a
  `season` template variable defaulting to the current one, not an unfiltered `SELECT`.
- No feature is built to compare across seasons. D-02 still holds: no cross-season chains are
  modeled, and D-12's `player_id` join exists to make such a query *possible*, not to imply one
  is planned.

Without this amendment D-01 and phase 5 contradict each other outright.

### D-02 — Redraft only; no keeper or dynasty modeling · Active

Rosters reset each season, so player retention across years is not modeled. This removes a whole
category of schema complexity (keeper chains, contract years, retained salary).

### D-03 — Multi-season by design, but one season at a time in practice · Active

Season is a column, never a constant. `leagues.season` carries it and every other table reaches it
through `league_key`. Nothing in the collector, CLI, queries, or dashboards hardcodes a year.

Rationale: it costs nothing now, and it means next August is a new row rather than a schema
migration. It also makes prior-season backfill possible, which turns out to matter for testing
(see D-17).

### D-04 — Not exposed to the public internet · Active

The database, Grafana, and the exporter are reachable only on the local network. The GitHub
repository is public, but it holds code and documentation, never data or configuration.

---

## Platform

### D-05 — Python 3.13 in a virtualenv; never `--break-system-packages` · Active

Debian trixie marks the system Python as externally managed under PEP 668. All work happens in
`.venv`. Overriding that flag to install into system Python risks the OS package manager's own
dependencies.

Verified importing cleanly on 3.13.5: `yahoo-fantasy-api`, `yahoofantasy`, `yahoo_oauth`.

### D-06 — PostgreSQL 17, native install, not containerized · Active

Docker is present on the host but unused for this project. A single long-lived database with no
scaling story does not benefit from containerization, and a native install keeps backups, the
service lifecycle, and `psql` access conventional.

### D-07 — Postgres holds the data; Prometheus holds only collector health · Active

Prometheus is built for numeric time series with low-cardinality labels. Fantasy data is dense with
entities and strings — player names, matchups, transaction payloads — which would both explode label
cardinality and discard everything non-numeric.

Postgres handles tables, joins, and text properly. Prometheus keeps a narrow role: a handful of
numeric trends and the collector health signal. Grafana reads Postgres natively, no plugin required.

### D-08 — No player names in Prometheus labels · Active

A direct consequence of D-07, called out separately because it is the specific mistake most likely
to be made by accident. Hundreds of players across eight leagues, churning weekly, is a cardinality
explosion. Player-level data lives in Postgres and is queried directly by Grafana.

### D-08a — Prometheus labels are keys, never display names · Active

Metrics are labelled `{league_key, team_key}`, never `{league, team}`. Grafana joins the display
name from Postgres for presentation.

This is a different failure from D-08 and is not implied by it. D-08 is about cardinality — too
many series at once. This is about **churn**: fantasy managers rename teams constantly mid-season,
and a label value is part of a series' identity. Renaming "Team Gronk" to "Justice for Tua" does
not update a series; it orphans the old one and starts a new one. A rank-over-time graph then
breaks in the middle of the season, showing two half-length lines with no indication that they are
the same team.

Yahoo keys are stable for a season by construction (D-11), which is exactly the property a label
needs. The display name is presentation, and presentation belongs in the query layer.

The same reasoning applies to any label that a human can edit: league names are renameable too.

### D-09 — systemd timers, not cron · Active

Timers log to journald, express calendar schedules more clearly, and make the unit's success or
failure a first-class queryable thing rather than a mail message nobody reads.

### D-10 — Two Postgres roles, separated by privilege · Active

`tilasto_app` is read/write and used by the collector. `tilasto_ro` is read-only and used by
Grafana. Grafana never holds write credentials to this database.

---

## Schema

### D-11 — Yahoo keys are the primary keys · Active

A league is `{game_key}.l.{league_id}`, a team `{game_key}.l.{league_id}.t.{team_id}`, a player
`{game_key}.p.{player_id}`. They are stable and globally unique, so surrogate integers would add an
indirection with no benefit and make debugging against raw API responses harder.

**Store keys exactly as Yahoo returns them.** The leading `game_key` is a season-specific integer.
That prefix is what makes keys season-unique, so normalizing it to a friendlier `nfl.` form would
silently collide 2026 with 2027 on the primary key. Documentation that writes `nfl.l.123456` is
using shorthand, not the real key format.

**The specific integers in these documents are illustrative and unverified.** `461` for 2026 and
`449` for 2025 are plausible-looking examples, not confirmed values — nobody has yet seen a real
response. Never hardcode a game key. Read it from the league resource, or derive league keys from
Yahoo's own game-key lookup. The phase 3 spike confirms the real values, and the examples here are
corrected then.

### D-12 — `player_key` is season-scoped; `player_id` is the cross-season identity · Active

The same person is `461.p.31002` one season and `449.p.31002` the next, so `players` holds one row
per player per season. That is correct for redraft (D-02) and needs no change.

The consequence: any cross-season question about a player joins on `player_id`, not `player_key`.
This is invisible until the second season exists, at which point a career-totals query silently
returns a single season and looks entirely plausible.

### D-13 — `NUMERIC` for points, never `FLOAT` · Active

Fantasy scoring is fractional (decimal PPR). Float drift surfaces in SQL aggregates as
`0.30000000000000004` and makes sums disagree with Yahoo's own displayed totals.

### D-14 — All timestamps `TIMESTAMPTZ`; convert Yahoo's epoch seconds on insert · Active

Storing naive timestamps invites a timezone bug during exactly one week of the year and is
unpleasant to correct retroactively.

### D-15 — Week belongs in the primary key · Active

Rosters, standings, and matchups are only meaningful with a week attached. Standings in particular
are stored per week rather than as one mutable row so that rank over time can be graphed at all —
a single updated row cannot express history.

Note that this rationale is about representing history, and is independent of whether the data can
be re-fetched (see D-16 and D-17).

---

## Collection

### D-16 — Weekly roster snapshots cannot be backfilled · **Superseded** (2026-08-17)

Original reasoning: Yahoo does not expose historical rosters retroactively, so a missed collection
run loses that week permanently, making the collector's own health the highest-stakes concern in
the project and creating hard time pressure to be running before week 1.

This was wrong, and was corrected in two steps. First: standings, matchups, and transactions are
retroactively fetchable, and roster *ownership* is derivable by replaying the transaction log
against draft results — leaving only start/sit lineup decisions genuinely unrecoverable. Then:
rosters themselves are a normal week-keyed fetch, so nothing is unrecoverable at all.

Superseded by D-17. Recorded because it shaped the original schema comments, the README's framing,
and the build order, and because the mistake was to assume an API limitation rather than confirm
one.

### D-17 — Every table is week-keyed and retroactively fetchable · Active

Rosters, standings, matchups, and transactions can all be requested for a completed week. Prior
seasons remain available for as long as Yahoo retains them for the account.

Consequences that follow from this and are not obvious individually:

- **No time pressure from approval latency.** Whenever the token lands, backfill every completed
  week, and prior seasons too.
- **A missed run is a re-fetch, not a hole.** The collector's health matters for dashboard
  freshness, not data preservation.
- **Prior seasons are the best available test fixture** — complete, static, and real. They exercise
  matchups and `player_weekly_stats`, which the 2026 season cannot populate until games are played.

### D-18 — Backfill is the same code path as a live run · Active

`tilasto collect --backfill --weeks 1-10` drives the same collectors over an explicit range rather
than invoking a separate importer. Because every fetch is week-keyed (D-15) and every write is an
idempotent upsert (D-19), a backfill is a live run with a different range.

The collector therefore takes a week range from the first commit. A collector written against
"current week" accumulates week-implicit assumptions that are painful to unpick later.

### D-19 — Every write is an idempotent upsert · Active

`INSERT ... ON CONFLICT ... DO UPDATE` throughout. Re-running any collection must be safe and must
never duplicate rows. This is what makes both backfill (D-18) and retry-on-failure trivial instead
of dangerous.

### D-20 — Store raw JSON responses before parsing · Active

Gzipped and dated, written to disk before any parsing occurs. Yahoo's response shapes are
inconsistent and deeply nested; keeping the raw payload means a parsing bug costs a re-parse rather
than a re-fetch, and makes the shape diffable when it changes.

Raw archives are gitignored — they contain manager names and other league-member information.

### D-21 — Back off exponentially on 999 and 429 · Active

Yahoo throttles aggressively and does not document the limit. Eight leagues across roughly 17 weeks
is not a large call volume, so this does not need to be elaborate — but it does need to exist.

### D-21a — First sustained use is staged, and the backoff is proven before it is needed · Active

The first serious API load is a prior-season backfill (phase 5), which is also the first time
D-21's rate limiting will ever have run. Doing that as a single full-season sweep means discovering
whether the backoff works by finding out that it doesn't — on a token granted for personal,
single-league use, obtained through an approval cycle that cannot be quickly repeated.

So the backfill escalates rather than starting wide:

1. **One league, two or three weeks.** Enough to exercise every collector, small enough that being
   wrong is cheap.
2. **Read the retry log before widening.** Confirm requests were actually paced and that any
   throttling response was seen and handled. If nothing throttled, that is a data point, not a
   pass — it means the limiter is untested, not that it works.
3. **One league, full season.** Then widen to the remaining leagues.

Supporting rules:

- **The backoff is unit-tested against simulated 999 and 429 responses before any of this**, using
  fakes rather than the live API. Retry logic is ordinary code and needs no token to test; waiting
  for a real throttle to exercise it is the expensive way to find a bug in a `sleep` calculation.
- **Every retry is logged**, with the response code and the delay applied. A backoff that silently
  works is indistinguishable from one that never triggered.
- **Pace conservatively at first.** A deliberate inter-request delay costs minutes on a backfill
  that has no deadline, and Yahoo does not document its limit (D-21), so the safe rate is unknown
  rather than merely unenforced. Tighten later, with evidence.
- **The raw archive earns its keep here** (D-20). Parsing bugs during early development are
  expected, and a stored payload means fixing one costs a re-parse rather than another pass over
  the API.

The point is not that a season of requests is objectively large. It is that the cost of being
wrong is asymmetric: unremarkable if it works, and disproportionate if it results in throttling or
review against access that was granted on the basis of modest personal use.

### D-22 — Log every run to `collector_runs`, success or failure · Active

This table is what the Prometheus exporter reads and what reveals a broken pipeline before a stale
dashboard does.

### D-23 — Alert on staleness, not only on error · Active

A collector that fails loudly is obvious; one that stops running is not, and a stale dashboard
looks much like a quiet week in the data. `tilasto_collector_last_success_timestamp` is the metric
that matters most.

Since D-17, the consequence of missing this is recoverable via backfill — but only once someone
notices, which is the entire point of the alert.

### D-24 — Two cadences · Active

Hourly during in-season game windows for matchups, standings, and live points. Daily for rosters,
transactions, and player metadata. Draft picks are one-shot per season: fetch once, skip if present.

---

## Auth and credentials

### D-24a — "Current week" comes from Yahoo, never from the calendar · Active

`leagues.current_week`, as returned on the league resource, is the single source of truth. Nothing
computes the current week from a date.

Computing it invites a class of bugs that are individually small and collectively miserable:
Yahoo's week boundary rolls over on Tuesday rather than at midnight Sunday, the rollover happens in
a timezone that is not necessarily the host's, and the boundary drifts around Thanksgiving and the
international games. Every one of those is a silent off-by-one that writes correct-looking rows
under the wrong week number — and because writes are idempotent upserts keyed on week (D-19), the
wrong row is written cleanly and overwrites nothing that would reveal the error.

Two consequences worth stating, because they are easy to get wrong:

- **Current week is per-league, not global.** Eight leagues can have different `start_week` and
  `current_week` values. A single "what week is it" resolved once per run and applied to all
  leagues is wrong. Resolve it per league, from that league's own resource.
- **It is read before collection, not cached across runs.** A collection run refreshes the league
  resource first, then uses the value it just read.

When a plan specifies no weeks (`CollectionPlan.weeks is None`), this is what "current week" means.
An explicit `--weeks` range bypasses it entirely, which is why backfill is unaffected by any of
this (D-18).

### D-25 — Fantasy API access is a reviewed application, not a permission checkbox · Active

Yahoo changed this. Third-party guides and library READMEs still describe a "Fantasy Sports"
checkbox on the app creation form; it no longer exists. The create-app form now offers only OpenID
Connect and TW Auction permissions, neither of which is what Fantasy uses.

Access is requested at <https://sports.yahoo.com/developer/access/> and reviewed by the Yahoo
Fantasy Sports team. Access is **read-only** — write access is not offered, so nothing is designed
around it.

### D-26 — Apply as an individual, not as a company · Active

The form is built for commercial applicants, but its own text covers cases "where access is limited
to personal or single league use." Yahoo closes incomplete or insufficiently detailed submissions
without further correspondence, so every field gets a real answer, and the use-case field names the
data required, the storage, the user count, and the fact that nothing is redistributed.

Keep a copy of the submission — resubmitting after a rejection is far easier from a saved copy than
from memory.

### D-27 — Confidential Client, redirect `https://localhost:8000` · Active

OpenID Connect and TW Auction permission boxes stay unchecked. The certificate warning during
`yahoofantasy login` is expected: Yahoo requires an HTTPS redirect and the local callback server
uses a self-signed cert.

### D-28 — Credentials in `.env` or `~/.config/tilastokeskus/`, gitignored before the token exists · Active

`yahoofantasy` writes its token file to the working directory by default, which makes this easy to
leak. The ignore rules were therefore committed before any token existed rather than after.

`~/.config/tilastokeskus/` sits outside the repository and needs no ignore rule — gitignore patterns
are repo-relative and perform no tilde expansion, so a rule naming it would match nothing and imply
a protection that does not exist.

### D-29 — The refresh must fail loudly · Active

Access tokens expire hourly; the refresh token is long-lived. If the refresh token is revoked, the
collector must fail visibly. A silent auth failure is indistinguishable from a quiet week in the
data.

---

## Repository

### D-30 — The GitHub repository is public; the brief is not committed · Active

A public repository was needed before applying for API access (D-25), because the application form
asks for a website or app URL.

`tilastokeskus-brief.md` is gitignored: it documents internal host addresses, and the repository is
public. The README is the public-facing document; the brief stays local.

Nothing committed names a host or an address. Committed documents refer to "the collection host"
and "the Grafana host" instead. Home network topology is not confidential in the way a credential
is, but it is free to omit and there is no reason to publish it.

### D-31 — Attribution is required and permanent · Active

"Fantasy data provided by Yahoo Fantasy," linking to Yahoo Fantasy, appears in the README and in
the Grafana dashboard description. Yahoo's API terms require it wherever the data surfaces. It is
not removed.

### D-32 — Naming conventions · Active

| Thing | Name |
|---|---|
| Repo / Python package | `tilastokeskus` |
| CLI entry point | `tilasto` |
| Postgres database | `tilastokeskus` |
| Postgres roles | `tilasto_app` (rw), `tilasto_ro` (read-only) |
| systemd units | `tilastokeskus-collect.service` / `.timer` |
| Prometheus job / metric prefix | `tilastokeskus` / `tilasto_` |
| Grafana dashboard uid | `tilastokeskus` |
| Config directory | `~/.config/tilastokeskus/` |

The CLI is deliberately shorter than the package: it gets typed constantly.

---

## Implementation

Decided while building the phase 2 scaffolding on 2026-08-17.

### D-34 — systemd **user** units, not system units · Active

`tilastokeskus-collect.{service,timer}` install to `~/.config/systemd/user/` and run under the
user's own session manager.

Rationale: the collector needs no privilege of any kind — it reads an API and writes to a database
as an unprivileged role. A user unit needs no root to install, enable, or inspect, which keeps the
whole scheduling path free of sudo. Paths use the `%h` specifier rather than a literal home
directory, so nothing in the committed unit names a user or a machine (D-30).

The one part that does need root is `loginctl enable-linger`, which lets user timers run without
an active login session. Without it the collector only runs while logged in — which would look
exactly like a silently broken pipeline.

`ProtectSystem=strict` makes the entire hierarchy read-only, so the project directory is granted
back explicitly via `ReadWritePaths`. It is where the raw JSON archive is written.

### D-40 — The collection host is a desktop, not a server · Active

The host is a triple-boot desktop — Debian, Fedora, Windows 11 — not an always-on machine. This
was never written down, and every scheduling decision so far quietly assumed otherwise.

What it actually means: a user timer with lingering enabled still only runs when the machine is
powered on **and booted into Debian**. An evening spent in Windows, a week in Fedora, a power cut
while travelling — each is a gap in collection. Availability is not a property of the timer, it is
a property of which operating system happens to be running.

Mitigations, in the order they matter:

- **`Persistent=true` on the timer.** A missed daily run fires at next boot rather than being
  skipped to the following day. This is the difference between a gap that closes itself and one
  that requires noticing.
- **Backfill closes the rest** (D-17). Gaps are recoverable by design, which is why this is a
  documented limitation rather than a defect.
- **Staleness alerting is calibrated for this** (D-23). A threshold tuned for a server would fire
  every time the desktop spent a day in another OS, and an alert that cries wolf is an alert that
  gets ignored. The threshold has to tolerate normal multi-day absence, which necessarily makes it
  slower to catch a genuine break.

The honest consequence is that in-season hourly collection during game windows (D-24) is
best-effort on this host. Live scoring is the one thing backfill cannot reconstruct at
hour-granularity — the final weekly numbers backfill fine, but the intra-game series does not.

**This is the argument for eventually moving the collector to the NAS.** Not now: the current setup
works, the data is recoverable, and moving it introduces its own problems. But if hourly in-season
collection turns out to matter, the fix is a host that is always on and always running one OS,
not more elaborate scheduling on a desktop.

### D-35 — Migrations live inside the package and are applied by `tilasto migrate` · Active

`.sql` files in `tilastokeskus/migrations/`, applied in filename order, each in its own
transaction, each recorded once in a `schema_migrations` table.

Keeping them inside the package rather than in a top-level `migrations/` directory means they
travel with an installed distribution and are locatable via `importlib.resources` rather than by
guessing a path relative to the working directory.

Indexes are named explicitly rather than left to Postgres' auto-naming. Anonymous indexes are
awkward to drop or replace in a later migration.

### D-41 — Postgres bootstrap is one privileged session, and grants precede the migration · Active

A fresh cluster has exactly one role, `postgres`. Everything up to and including database creation
therefore runs as `sudo -u postgres`: installing, creating both roles, and creating the database.
Splitting this — install as root, then "the rest" as the ordinary user — does not work, because
`createdb` and `CREATE ROLE` hit the same privilege wall one command later.

The database is created **owned by `tilasto_app`**, so the migration's tables are owned by the role
that runs migrations. Since Postgres 15 the `public` schema is owned by `pg_database_owner`, which
means database ownership is what grants `tilasto_app` the right to create tables at all.

**`ALTER DEFAULT PRIVILEGES` runs before the first migration, not after.** Default privileges apply
only to objects created *after* they are set, so running the migration first leaves all ten tables
invisible to `tilasto_ro` and requires a manual `GRANT SELECT ON ALL TABLES` to repair.

This is the step that gets skipped, and skipping it fails in the worst possible way: it works
today, because someone runs the repair grant once and Grafana comes to life — then breaks silently
the next time a migration adds a table, because that table gets no grant and only the panels using
it go blank.

Set as `FOR ROLE tilasto_app` explicitly rather than relying on the current session's role, so the
grant follows the role that will actually create the tables.

### D-36 — The collector takes a week range from the first commit · Active

`--weeks` accepts `3`, `1-4`, `1,3,5`, or `1-3,7`, validated against weeks 1–18, and is rejected
with a usage error rather than a traceback. `--backfill` without `--weeks` is a usage error too.

This is D-18 made concrete. A CLI that only understands "current week" accumulates week-implicit
assumptions in the collectors beneath it, and retrofitting a range afterwards means auditing every
one of them.

### D-37 — The default season is computed, never written down · Active

`default_season()` returns the calendar year, rolling over in March on the basis that an NFL season
labelled Y runs from September Y into February Y+1, and Yahoo publishes the game key for season Y
the preceding spring.

This is D-03 made enforceable: there is no literal year anywhere in the package, so next August
requires no code change. The rollover rule is unit-tested across the boundary.

### D-38 — Unimplemented paths fail loudly, with a reason and a pointer · Active

Every stub raises with a message naming what was attempted and where the blocker is documented —
`"listing league keys requires Yahoo Fantasy API access, which is still pending. See TASKS.md
phase 1."` Exit codes distinguish usage errors (2) from runtime failures (1).

This matters more than it looks. The pipeline's characteristic failure is silence (D-23, D-29), and
a stub that returns an empty list instead of raising is indistinguishable from a league with no
data.

### D-39 — psycopg 3, python-dotenv, and little else · Active

Dependencies are `psycopg[binary]`, `python-dotenv`, and `yahoofantasy`, with `pytest` and `ruff`
for development. No ORM: the schema is built on natural keys and upserts written as explicit SQL
(D-19), which an ORM would obscure rather than simplify.

---

## Open

### D-33 — Schema is unvalidated against real API responses · Open

The schema was designed from documentation and prior knowledge of Yahoo's data model, not from
observed payloads. The read-only spike (see `TASKS.md`) exists specifically to confirm shapes before
the migration is treated as settled.

Fields most likely to need revision: `transactions.payload` structure, the `stats` JSONB map in
`player_weekly_stats`, `draft_type` and `scoring_type` value sets, and whether `eligible_positions`
arrives in a form that maps cleanly onto `TEXT[]`.
