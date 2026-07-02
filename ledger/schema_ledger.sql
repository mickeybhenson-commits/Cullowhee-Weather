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
