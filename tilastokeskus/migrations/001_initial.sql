-- 001_initial — core schema.
--
-- Design notes live in DECISIONS.md; the short version:
--   * Yahoo keys are the primary keys, stored verbatim including the season-specific
--     game_key prefix, which is what makes them season-unique.                  (D-11)
--     The game_key integers in the comments below are ILLUSTRATIVE — no real response
--     has been seen yet. Never hardcode one; read it from the league resource.  (D-11, D-33)
--   * player_key is season-scoped; player_id is the cross-season identity.        (D-12)
--   * NUMERIC for points, never FLOAT.                                           (D-13)
--   * TIMESTAMPTZ everywhere; Yahoo's epoch seconds are converted on insert.     (D-14)
--   * Week belongs in the key. Every table here is re-fetchable by week.         (D-15, D-17)
--
-- This schema is provisional until validated against real API payloads.          (D-33)

CREATE TABLE leagues (
    league_key      TEXT PRIMARY KEY,          -- e.g. '461.l.123456' (game_key illustrative)
    season          INT  NOT NULL,
    name            TEXT NOT NULL,
    num_teams       INT,
    scoring_type    TEXT,                      -- 'head' | 'points'
    draft_type      TEXT,                      -- 'live' | 'auction' | 'autopick'
    current_week    INT,
    start_week      INT,
    end_week        INT,
    playoff_start_week INT,
    is_finished     BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX leagues_season_idx ON leagues (season);

CREATE TABLE teams (
    team_key        TEXT PRIMARY KEY,          -- e.g. '461.l.123456.t.4' (game_key illustrative)
    league_key      TEXT NOT NULL REFERENCES leagues(league_key),
    team_id         INT  NOT NULL,
    name            TEXT NOT NULL,
    manager_name    TEXT,
    is_owned_by_me  BOOLEAN NOT NULL DEFAULT FALSE,  -- drives dashboard filtering
    logo_url        TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX teams_league_idx ON teams (league_key);

CREATE TABLE players (
    player_key      TEXT PRIMARY KEY,          -- e.g. '461.p.31002' — season-scoped, game_key illustrative
    player_id       INT,                       -- stable across seasons; join on this
    full_name       TEXT NOT NULL,
    position        TEXT,                      -- 'QB','RB','WR','TE','K','DEF'
    eligible_positions TEXT[],
    nfl_team        TEXT,
    bye_week        INT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX players_player_id_idx ON players (player_id);
CREATE INDEX players_name_idx ON players (full_name);

-- Standings are a point-in-time value, not a running total. Storing them per week is what
-- allows rank over time to be graphed at all; a single mutable row cannot express history.
CREATE TABLE standings (
    league_key      TEXT NOT NULL REFERENCES leagues(league_key),
    team_key        TEXT NOT NULL REFERENCES teams(team_key),
    week            INT  NOT NULL,
    rank            INT,
    wins            INT  NOT NULL DEFAULT 0,
    losses          INT  NOT NULL DEFAULT 0,
    ties            INT  NOT NULL DEFAULT 0,
    points_for      NUMERIC(8,2),
    points_against  NUMERIC(8,2),
    streak          TEXT,
    waiver_priority INT,
    faab_balance    INT,
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (team_key, week)
);
CREATE INDEX standings_league_week_idx ON standings (league_key, week);

CREATE TABLE matchups (
    league_key      TEXT NOT NULL REFERENCES leagues(league_key),
    week            INT  NOT NULL,
    team_key        TEXT NOT NULL REFERENCES teams(team_key),
    opponent_key    TEXT REFERENCES teams(team_key),
    points          NUMERIC(8,2),
    projected_points NUMERIC(8,2),
    result          TEXT,                      -- 'W' | 'L' | 'T' | NULL if unplayed
    is_playoffs     BOOLEAN NOT NULL DEFAULT FALSE,
    is_consolation  BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (team_key, week)
);
CREATE INDEX matchups_league_week_idx ON matchups (league_key, week);

-- Re-fetchable for any completed week. The week is a query parameter, not a capture window.
CREATE TABLE rosters (
    league_key      TEXT NOT NULL REFERENCES leagues(league_key),
    team_key        TEXT NOT NULL REFERENCES teams(team_key),
    week            INT  NOT NULL,
    player_key      TEXT NOT NULL REFERENCES players(player_key),
    selected_position TEXT,                    -- 'QB','BN','IR', etc.
    is_starting     BOOLEAN,                   -- selected_position NOT IN ('BN','IR')
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (team_key, week, player_key)
);
CREATE INDEX rosters_league_week_idx ON rosters (league_key, week);
CREATE INDEX rosters_player_idx ON rosters (player_key);

CREATE TABLE player_weekly_stats (
    league_key      TEXT NOT NULL REFERENCES leagues(league_key),
    player_key      TEXT NOT NULL REFERENCES players(player_key),
    week            INT  NOT NULL,
    points          NUMERIC(8,2),
    projected_points NUMERIC(8,2),
    stats           JSONB,                     -- raw stat map; shape varies by position
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (league_key, player_key, week)
);

CREATE TABLE draft_picks (
    league_key      TEXT NOT NULL REFERENCES leagues(league_key),
    pick            INT  NOT NULL,
    round           INT  NOT NULL,
    team_key        TEXT NOT NULL REFERENCES teams(team_key),
    player_key      TEXT NOT NULL REFERENCES players(player_key),
    cost            INT,                       -- auction leagues only
    PRIMARY KEY (league_key, pick)
);
CREATE INDEX draft_picks_team_idx ON draft_picks (team_key);
CREATE INDEX draft_picks_player_idx ON draft_picks (player_key);

CREATE TABLE transactions (
    transaction_key TEXT PRIMARY KEY,
    league_key      TEXT NOT NULL REFERENCES leagues(league_key),
    type            TEXT,                      -- 'add','drop','trade','commish'
    status          TEXT,
    timestamp       TIMESTAMPTZ,
    faab_bid        INT,
    payload         JSONB,                     -- full detail; shape varies a lot
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX transactions_league_time_idx ON transactions (league_key, timestamp DESC);

-- Observability. The Prometheus exporter reads this, and it is what reveals a broken
-- pipeline before a stale dashboard does.
CREATE TABLE collector_runs (
    id              BIGSERIAL PRIMARY KEY,
    started_at      TIMESTAMPTZ NOT NULL,
    finished_at     TIMESTAMPTZ,
    status          TEXT NOT NULL,             -- 'success','partial','failed'
    leagues_synced  INT,
    rows_written    INT,
    error           TEXT
);
CREATE INDEX collector_runs_started_idx ON collector_runs (started_at DESC);
