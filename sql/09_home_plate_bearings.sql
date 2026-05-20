-- ────────────────────────────────────────────────────────────────
-- Migration: add home_plate_bearing column to teams.
--
-- Stores the compass bearing (in degrees, 0-359) from home plate
-- toward CF for each stadium. 0 = due north, 90 = due east, etc.
--
-- This bearing is used to rotate raw weather wind direction (which
-- is in absolute compass terms) into a field-relative angle, so
-- analytics and the WindGauge component can answer "is wind blowing
-- OUT to CF" vs "IN from CF" etc. for each ballpark independently.
--
-- Single source of truth: previously this lived as a hardcoded dict
-- in pull_weather.py AND in WindGauge.vue. Both should now read this
-- column from the teams table instead.
-- ────────────────────────────────────────────────────────────────

ALTER TABLE teams
  ADD COLUMN IF NOT EXISTS home_plate_bearing INTEGER;

COMMENT ON COLUMN teams.home_plate_bearing IS
  'Compass bearing in degrees (0-359) from home plate toward center field. '
  'Used for converting wind direction into field-relative angles. '
  'NULL if unknown — wind features should fall back to generic display.';

-- Populate from publicly known ballpark orientations.
-- Approximate; can be refined with measured values per stadium.

UPDATE teams SET home_plate_bearing = 23  WHERE abbrev = 'ARI';  -- Chase Field
UPDATE teams SET home_plate_bearing = 50  WHERE abbrev = 'ATL';  -- Truist Park
UPDATE teams SET home_plate_bearing = 60  WHERE abbrev = 'ATH';  -- Sutter Health Park (was Oakland Coliseum)
UPDATE teams SET home_plate_bearing = 38  WHERE abbrev = 'BAL';  -- Camden Yards
UPDATE teams SET home_plate_bearing = 45  WHERE abbrev = 'BOS';  -- Fenway Park
UPDATE teams SET home_plate_bearing = 30  WHERE abbrev = 'CHC';  -- Wrigley Field
UPDATE teams SET home_plate_bearing = 130 WHERE abbrev = 'CWS';  -- Rate Field (formerly Guaranteed Rate)
UPDATE teams SET home_plate_bearing = 35  WHERE abbrev = 'CIN';  -- Great American Ball Park
UPDATE teams SET home_plate_bearing = 17  WHERE abbrev = 'CLE';  -- Progressive Field
UPDATE teams SET home_plate_bearing = 25  WHERE abbrev = 'COL';  -- Coors Field
UPDATE teams SET home_plate_bearing = 145 WHERE abbrev = 'DET';  -- Comerica Park
UPDATE teams SET home_plate_bearing = 348 WHERE abbrev = 'HOU';  -- Daikin Park (formerly Minute Maid)
UPDATE teams SET home_plate_bearing = 45  WHERE abbrev = 'KC';   -- Kauffman Stadium
UPDATE teams SET home_plate_bearing = 60  WHERE abbrev = 'LAA';  -- Angel Stadium
UPDATE teams SET home_plate_bearing = 25  WHERE abbrev = 'LAD';  -- Dodger Stadium
UPDATE teams SET home_plate_bearing = 40  WHERE abbrev = 'MIA';  -- loanDepot park
UPDATE teams SET home_plate_bearing = 135 WHERE abbrev = 'MIL';  -- American Family Field
UPDATE teams SET home_plate_bearing = 90  WHERE abbrev = 'MIN';  -- Target Field
UPDATE teams SET home_plate_bearing = 25  WHERE abbrev = 'NYM';  -- Citi Field
UPDATE teams SET home_plate_bearing = 75  WHERE abbrev = 'NYY';  -- Yankee Stadium
UPDATE teams SET home_plate_bearing = 15  WHERE abbrev = 'PHI';  -- Citizens Bank Park
UPDATE teams SET home_plate_bearing = 117 WHERE abbrev = 'PIT';  -- PNC Park
UPDATE teams SET home_plate_bearing = 22  WHERE abbrev = 'SD';   -- Petco Park
UPDATE teams SET home_plate_bearing = 90  WHERE abbrev = 'SF';   -- Oracle Park
UPDATE teams SET home_plate_bearing = 45  WHERE abbrev = 'SEA';  -- T-Mobile Park
UPDATE teams SET home_plate_bearing = 60  WHERE abbrev = 'STL';  -- Busch Stadium
UPDATE teams SET home_plate_bearing = 45  WHERE abbrev = 'TB';   -- Tropicana Field
UPDATE teams SET home_plate_bearing = 24  WHERE abbrev = 'TEX';  -- Globe Life Field
UPDATE teams SET home_plate_bearing = 12  WHERE abbrev = 'TOR';  -- Rogers Centre
UPDATE teams SET home_plate_bearing = 30  WHERE abbrev = 'WSH';  -- Nationals Park

-- Sanity check — should be 30 with non-null after the updates above.
-- SELECT abbrev, home_plate_bearing FROM teams ORDER BY abbrev;
