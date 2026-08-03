-- schema_ledger.sql — QPF-bias verification ledger (separate DB from ops ingest)
-- Units: millimetres everywhere (native to both Open-Meteo and MRMS);
-- convert to inches at analysis time only.
-- Hourly convention: a value stamped valid_utc covers the PRECEDING hour
-- (Open-Meteo precipitation and MRMS *_01H both follow this).

PRAGMA journal_mode = WAL;

-- Forecast atoms: one row per (basin, issuance, valid hour, source).
CREATE TABLE IF NOT EXISTS forecasts (
    basin_id   TEXT NOT NULL,
    issued_utc TEXT NOT NULL,   -- ISO8601, UTC; approximate for prev-runs backfill
    valid_utc  TEXT NOT NULL,   -- ISO8601, UTC; accumulation END hour
    qpf_mm     REAL NOT NULL,
    source     TEXT NOT NULL,   -- 'om-best' live | 'om-prev-runs' backfill
    PRIMARY KEY (basin_id, issued_utc, valid_utc, source)
) WITHOUT ROWID;

-- Observation atoms: one row per (basin, valid hour, source).
CREATE TABLE IF NOT EXISTS observations (
    basin_id   TEXT NOT NULL,
    valid_utc  TEXT NOT NULL,
    qpe_mm     REAL NOT NULL,
    valid_frac REAL NOT NULL DEFAULT 1.0,  -- weight fraction of non-missing MRMS cells
    source     TEXT NOT NULL DEFAULT 'mrms-p2',
    PRIMARY KEY (basin_id, valid_utc, source)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_fc_valid  ON forecasts (basin_id, valid_utc);
CREATE INDEX IF NOT EXISTS idx_obs_valid ON observations (valid_utc);

-- 6-hour verification windows aligned 00/06/12/18Z.
-- An hourly atom ending at valid_utc belongs to the window ENDING at the next
-- multiple of 6 h (window 06Z = valid hours 01..06Z). Windows are kept only
-- when all 6 hourly atoms are present.
DROP VIEW IF EXISTS fc_6h;
CREATE VIEW fc_6h AS
SELECT basin_id, issued_utc, source,
       strftime('%Y-%m-%dT%H:00:00',
         datetime(valid_utc,
           '+' || ((6 - (CAST(strftime('%H', valid_utc) AS INTEGER) % 6)) % 6)
               || ' hours')) AS wend_utc,
       SUM(qpf_mm) AS qpf_mm, COUNT(*) AS n
FROM forecasts
GROUP BY basin_id, issued_utc, source, wend_utc
HAVING n = 6;

DROP VIEW IF EXISTS obs_6h;
CREATE VIEW obs_6h AS
SELECT basin_id, source,
       strftime('%Y-%m-%dT%H:00:00',
         datetime(valid_utc,
           '+' || ((6 - (CAST(strftime('%H', valid_utc) AS INTEGER) % 6)) % 6)
               || ' hours')) AS wend_utc,
       SUM(qpe_mm) AS qpe_mm, MIN(valid_frac) AS min_valid_frac, COUNT(*) AS n
FROM observations
GROUP BY basin_id, source, wend_utc
HAVING n = 6;

-- Forecast/observation pairs with derived lead time (hours from issuance to
-- window END). Analysis filters: wet windows (qpe_mm >= ~12.7 mm / 0.5 in),
-- min_valid_frac >= 0.8, then bias = SUM(qpf)/SUM(qpe) per
-- (basin, lead bucket, season).
DROP VIEW IF EXISTS pairs_6h;
CREATE VIEW pairs_6h AS
SELECT f.basin_id, f.issued_utc, f.wend_utc,
       ROUND((julianday(f.wend_utc) - julianday(f.issued_utc)) * 24.0, 1)
           AS lead_hr,
       f.qpf_mm, o.qpe_mm, o.min_valid_frac,
       f.source AS fc_source, o.source AS obs_source
FROM fc_6h f
JOIN obs_6h o ON o.basin_id = f.basin_id AND o.wend_utc = f.wend_utc;


-- ===========================================================================
-- STAGE VERIFICATION — model output vs the one measured gage in the watershed
-- ---------------------------------------------------------------------------
-- The tables above verify the model's INPUT (Open-Meteo QPF vs MRMS QPE).
-- These verify its OUTPUT: what the creek actually did, at NCEM FIMAN gage
-- 25380 (Cullowhee Creek at Speedwell), 830 ft from the CC-SPD-1830 pour
-- point and the only measured stream stage in the watershed.
--
-- DATUM WARNING. The two stage columns are NOT on the same datum and their
-- difference is NOT a model error:
--     stage_obs.stage_ft    feet above GAGE DATUM 2125.0 ft NAVD88
--     stage_model.stage_ft  feet above CHANNEL BED (Manning rectangular)
-- basins.py has bed_ft = None for CC-SPD-1830: the bed has never been tied to
-- NAVD88. So obs - mod = (model error) + (unknown constant offset).
--
-- Three things ARE valid without the survey, and the views below give all three:
--   1. level agreement   FIMAN's own CONDITION_TXT vs the modeled level
--   2. rate of rise      d(stage)/dt cancels any constant offset exactly
--   3. the offset itself regress obs on mod across events - the intercept IS
--                        the datum offset, recovered empirically. Logging both
--                        columns is how the datum gets tied without a crew.
-- Units: feet here (native to both FIMAN and the ratings), unlike the mm above.
-- ===========================================================================

-- Measured stage. One row per gage observation, as reported by FIMAN.
CREATE TABLE IF NOT EXISTS stage_obs (
    basin_id   TEXT NOT NULL,          -- 'CC-SPD-1830'
    valid_utc  TEXT NOT NULL,          -- gage LAST_UPDATED converted to UTC
    stage_ft   REAL,                   -- above gage datum 2125.0 ft NAVD88
    condition  TEXT,                   -- FIMAN CONDITION_TXT, verbatim
    level      TEXT,                   -- fiman_source.CONDITION_LEVEL, NULL if unmapped
    trend      TEXT,
    age_min    REAL,                   -- at fetch time
    fresh      INTEGER NOT NULL,       -- 1 if inside the 75-min gate
    site_id    TEXT NOT NULL DEFAULT '25380',
    source     TEXT NOT NULL,          -- 'fiman-live' | 'fiman-csv'
    PRIMARY KEY (basin_id, valid_utc, source)
) WITHOUT ROWID;

-- Modeled stage/discharge. One row per (basin, model run, instant described).
CREATE TABLE IF NOT EXISTS stage_model (
    basin_id   TEXT NOT NULL,
    issued_utc TEXT NOT NULL,          -- when the model ran
    valid_utc  TEXT NOT NULL,          -- instant / window end it describes
    stage_ft   REAL,                   -- above channel bed - SEE DATUM WARNING
    q_cfs      REAL,                   -- calibrated peak discharge
    rp_yr      REAL,                   -- return period on the StreamStats curve
    level      TEXT,                   -- NORMAL | WATCH | WARNING | EMERGENCY
    wetness    REAL,                   -- w in [0,1] that produced the CN
    cn         REAL,
    source     TEXT NOT NULL,          -- 'noah-live'
    PRIMARY KEY (basin_id, issued_utc, valid_utc, source)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_sobs_valid ON stage_obs (basin_id, valid_utc);
CREATE INDEX IF NOT EXISTS idx_smod_valid ON stage_model (basin_id, valid_utc);

-- Peak-to-peak pairing on the SAME 6-hour windows as pairs_6h, because the
-- model produces an event PEAK, not an instantaneous stage. Comparing a
-- predicted peak against an hourly reading would be a category error.
DROP VIEW IF EXISTS stage_obs_6h;
CREATE VIEW stage_obs_6h AS
SELECT basin_id, source,
       strftime('%Y-%m-%dT%H:00:00',
         datetime(valid_utc,
           '+' || ((6 - (CAST(strftime('%H', valid_utc) AS INTEGER) % 6)) % 6)
               || ' hours')) AS wend_utc,
       MAX(stage_ft) AS obs_peak_ft, MIN(stage_ft) AS obs_min_ft,
       COUNT(*) AS n_obs, SUM(fresh) AS n_fresh
FROM stage_obs
GROUP BY basin_id, source, wend_utc;

DROP VIEW IF EXISTS stage_model_6h;
CREATE VIEW stage_model_6h AS
SELECT basin_id, issued_utc, source,
       strftime('%Y-%m-%dT%H:00:00',
         datetime(valid_utc,
           '+' || ((6 - (CAST(strftime('%H', valid_utc) AS INTEGER) % 6)) % 6)
               || ' hours')) AS wend_utc,
       MAX(stage_ft) AS mod_peak_ft, MAX(q_cfs) AS mod_peak_cfs,
       MAX(rp_yr) AS mod_rp_yr, COUNT(*) AS n
FROM stage_model
GROUP BY basin_id, issued_utc, source, wend_utc;

-- The comparison table. raw_diff_ft is deliberately NOT called "error".
DROP VIEW IF EXISTS stage_pairs_6h;
CREATE VIEW stage_pairs_6h AS
SELECT o.basin_id, m.issued_utc, o.wend_utc,
       ROUND((julianday(o.wend_utc) - julianday(m.issued_utc)) * 24.0, 1) AS lead_hr,
       o.obs_peak_ft, m.mod_peak_ft,
       o.obs_peak_ft - m.mod_peak_ft AS raw_diff_ft,   -- model error + datum offset
       m.mod_peak_cfs, m.mod_rp_yr,
       o.n_obs, o.n_fresh, o.source AS obs_source, m.source AS mod_source
FROM stage_obs_6h o
JOIN stage_model_6h m
  ON m.basin_id = o.basin_id AND m.wend_utc = o.wend_utc;

-- Rate of rise: the one absolute comparison that is valid TODAY, because a
-- constant datum offset differentiates away. Requires SQLite >= 3.25.
DROP VIEW IF EXISTS stage_rate_6h;
CREATE VIEW stage_rate_6h AS
SELECT basin_id, issued_utc, wend_utc, lead_hr,
       obs_peak_ft - LAG(obs_peak_ft) OVER w AS d_obs_ft,
       mod_peak_ft - LAG(mod_peak_ft) OVER w AS d_mod_ft
FROM stage_pairs_6h
WINDOW w AS (PARTITION BY basin_id ORDER BY wend_utc);
-- Partitioned by basin only, NOT by issuance: fetch_stage.py writes one model
-- row per run, so partitioning by issued_utc would leave every window alone in
-- its partition and LAG would always be NULL.

-- Categorical agreement, which needs no datum at all.
DROP VIEW IF EXISTS stage_level_agreement;
CREATE VIEW stage_level_agreement AS
SELECT o.basin_id, o.valid_utc, o.condition, o.level AS obs_level,
       m.level AS mod_level, (o.level = m.level) AS agree,
       o.stage_ft AS obs_stage_ft, m.stage_ft AS mod_stage_ft
FROM stage_obs o
JOIN stage_model m
  ON m.basin_id = o.basin_id AND m.valid_utc = o.valid_utc
WHERE o.fresh = 1 AND o.level IS NOT NULL;
