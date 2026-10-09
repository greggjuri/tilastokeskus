-- 002_players_nfl_positions — rename players.eligible_positions to nfl_positions.     (PRP-02)
--
-- The column changes meaning, so it changes name. eligible_positions read as "which roster slots
-- can this player fill" — W/R/T, Q/W/R/T, IR — and that is league data: the same player is
-- (RB) globally and (RB, W/R/T, Q/W/R/T) in the superflex league, and differed in 47 of 50
-- players compared. players holds one row per player across every league (D-12), so a
-- league-dependent value there is last-writer-wins. The column now holds real NFL positions from
-- display_position, which was identical in 50 of 50. Slot eligibility belongs with rosters.
--
-- A real 002 rather than an edit to 001: 001 is applied and leagues, teams and collector_runs
-- hold rows. players was still empty when this ran.

ALTER TABLE players RENAME COLUMN eligible_positions TO nfl_positions;

COMMENT ON COLUMN players.nfl_positions IS
    'Real NFL positions, from display_position split on commas, e.g. {DT,DE}. League-independent. '
    'Not roster-slot eligibility (W/R/T, IR, ...), which is league data and belongs with rosters.';

COMMENT ON COLUMN players.position IS
    'display_position verbatim, e.g. LB or DT,DE. League-independent.';
