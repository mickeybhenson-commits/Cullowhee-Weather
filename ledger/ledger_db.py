#!/usr/bin/env python3
"""
ledger_db.py — shared SQLite plumbing for the QPF-bias ledger.

Single source of truth for: DB path resolution, schema application, and the
two insert helpers. Both fetchers and the backfill import this so the schema
cannot drift between writers.

DB path resolution (first hit wins):
  1. --db argument passed by the caller
  2. $QPF_LEDGER_DB environment variable (set in the systemd units)
  3. /var/lib/noah/qpf_ledger.db

Stdlib only.
"""

import os
import sqlite3

DEFAULT_DB = "/var/lib/noah/qpf_ledger.db"
_SCHEMA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "schema_ledger.sql")


def db_path(cli_arg=None):
    return cli_arg or os.environ.get("QPF_LEDGER_DB") or DEFAULT_DB


def connect(path=None):
    """Open (creating if needed) the ledger DB and apply the canonical schema.
    Schema statements are all IF NOT EXISTS / DROP-and-recreate-view, so this
    is idempotent and safe on every run."""
    p = db_path(path)
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    conn = sqlite3.connect(p, timeout=30.0)
    with open(_SCHEMA_FILE) as f:
        conn.executescript(f.read())
    conn.commit()
    return conn


def insert_forecasts(conn, rows):
    """rows: iterable of (basin_id, issued_utc, valid_utc, qpf_mm, source)."""
    conn.executemany(
        "INSERT OR REPLACE INTO forecasts "
        "(basin_id, issued_utc, valid_utc, qpf_mm, source) VALUES (?,?,?,?,?)",
        rows)
    conn.commit()


def insert_observations(conn, rows):
    """rows: iterable of (basin_id, valid_utc, qpe_mm, valid_frac, source)."""
    conn.executemany(
        "INSERT OR REPLACE INTO observations "
        "(basin_id, valid_utc, qpe_mm, valid_frac, source) VALUES (?,?,?,?,?)",
        rows)
    conn.commit()


def have_observation(conn, valid_utc, source="mrms-p2"):
    """True if ALL basins already have an observation for this hour."""
    n = conn.execute(
        "SELECT COUNT(DISTINCT basin_id) FROM observations "
        "WHERE valid_utc = ? AND source = ?", (valid_utc, source)).fetchone()[0]
    return n >= 8
