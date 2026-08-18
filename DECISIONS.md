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

### D-09 — systemd timers, not cron · Active

Timers log to journald, express calendar schedules more clearly, and make the unit's success or
failure a first-class queryable thing rather than a mail message nobody reads.

### D-10 — Two Postgres roles, separated by privilege · Active

`tilasto_app` is read/write and used by the collector. `tilasto_ro` is read-only and used by
Grafana. Grafana never holds write credentials to this database.

---

## Schema

### D-11 — Yahoo keys are the primary keys · Active

A league is `461.l.123456`, a team `461.l.123456.t.4`, a player `461.p.31002`. They are stable and
globally unique, so surrogate integers would add an indirection with no benefit and make debugging
against raw API responses harder.

**Store keys exactly as Yahoo returns them.** The leading number is a season-specific game id. That
prefix is what makes keys season-unique, so normalizing it to a friendlier `nfl.` form would
silently collide 2026 with 2027 on the primary key. Documentation that writes `nfl.l.123456` is
using shorthand, not the real key format.

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

## Open

### D-33 — Schema is unvalidated against real API responses · Open

The schema was designed from documentation and prior knowledge of Yahoo's data model, not from
observed payloads. The read-only spike (see `TASKS.md`) exists specifically to confirm shapes before
the migration is treated as settled.

Fields most likely to need revision: `transactions.payload` structure, the `stats` JSONB map in
`player_weekly_stats`, `draft_type` and `scoring_type` value sets, and whether `eligible_positions`
arrives in a form that maps cleanly onto `TEXT[]`.
