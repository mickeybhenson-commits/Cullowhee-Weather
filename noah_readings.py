#!/usr/bin/env python3
"""
noah_readings.py — the simplest sensor backend that can possibly deploy.

The gateway (or the Notehub → Firestore exporter, or a person with a USB stick during a
field test) writes ONE file, feed/noah/readings.json:

    {
      "written_utc": "2026-10-03T14:05:00Z",
      "readings": [
        {"basin": "CC-SPD-1830", "quantity": "stage_ft",          "value": 2.31, "ts": "2026-10-03T14:00:00Z", "source": "NOAH SPD-01 radar"},
        {"basin": "CC-SPD-1830", "quantity": "soil_moisture_pct", "value": 61.0, "ts": "2026-10-03T13:45:00Z", "source": "NOAH SPD-01 TEROS 20 cm"},
        {"basin": "CC-SPD-1830", "quantity": "rain_1h",           "value": 0.12, "ts": "2026-10-03T14:00:00Z", "source": "NOAH SPD-01 tipping bucket"}
      ]
    }

That is the whole deployment contract for the readiness chain. sources.resolve() gates
every row for freshness and range, so a stale row falls through to the model and the
readout says why. Quantities are sources.Q_* names. Timestamps are ISO-8601 UTC.

FileBackend is chained in front of Firestore by readiness.install_backends(); when the
Firestore ingest is live the file becomes redundant and can simply stop being written.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import sources

DEFAULT_PATH = Path("feed/noah/readings.json")


def _ts(s) -> Optional[datetime]:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class FileBackend(sources.SensorBackend):
    """Latest reading per (quantity, basin) from feed/noah/readings.json. Returns None
    (falls through the chain) when the file is absent, unreadable, or has no such row."""

    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = Path(path)
        self._cache = None
        self._mtime = None

    def _load(self):
        try:
            m = self.path.stat().st_mtime
        except OSError:
            self._cache = None
            return None
        if self._cache is None or m != self._mtime:
            try:
                self._cache = json.loads(self.path.read_text(encoding="utf-8"))
                self._mtime = m
            except (OSError, ValueError):
                self._cache = None
        return self._cache

    def latest(self, quantity: str, basin_id: str) -> Optional[sources.Reading]:
        data = self._load()
        if not data:
            return None
        best = None
        for r in data.get("readings", []):
            if r.get("basin") != basin_id or r.get("quantity") != quantity:
                continue
            if r.get(sources.TEST_FLAG) or str(r.get("basin", "")).upper().startswith("BENCH"):
                continue          # bench / test packet: never MEASURED (sources.TEST_FLAG)
            ts = _ts(r.get("ts"))
            if best is None or (ts and best[0] and ts > best[0]) or (ts and not best[0]):
                best = (ts, r)
        if best is None:
            return None
        ts, r = best
        try:
            val = float(r.get("value"))
        except (TypeError, ValueError):
            val = None
        return sources.Reading(val, sources.MEASURED, str(r.get("source") or "NOAH"), ts, quantity)


if __name__ == "__main__":
    be = FileBackend()
    for q in (sources.Q_STAGE, sources.Q_SOIL, sources.Q_RAIN_1H):
        for bid in ("CC-SPD-1830", "CC-WCU-2260", "CC-COX-097"):
            r = be.latest(q, bid)
            print(f"{q:18s} {bid:13s} -> " + (f"{r.value} {r.source} {r.ts}" if r else "none"))
