#!/usr/bin/env python3
"""
wpc_ero.py — WPC Excessive Rainfall Outlook (ERO), days 1-5, for the Cullowhee watershed.

The ERO is the Weather Prediction Center's own five-day answer to "where will rainfall
exceed flash-flood guidance?" It covers every rain type that hurts this watershed — stalled
fronts, training storms, upslope events, cut-off lows, predecessor rain, tropical remnants
— not only named systems. That makes it the broadest alarm the readiness mode listens to.

Source: NOAA map services, hazards/wpc_precip_hazards, layers 0..4 = Day 1..5.
Risk in field `dn`: 1 Marginal (>=5 %), 2 Slight (>=15 %), 3 Moderate (>=40 %), 4 High (>=70 %).
The query intersects the polygons with the watershed envelope, so a category that touches any
part of the eight sub-basins counts.

    python wpc_ero.py            -> prints the five-day ladder for the watershed

fetch(now) never raises; on failure it returns status="unavailable (...)" so the caller can
say why. GOV_ESTIMATE tier.
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Optional

SERVICE = "https://mapservices.weather.noaa.gov/vector/rest/services/hazards/wpc_precip_hazards/MapServer"
# Cullowhee Creek watershed envelope (all eight sub-basins), WGS84
ENVELOPE = dict(xmin=-83.30, ymin=35.19, xmax=-83.10, ymax=35.39)
LABEL = {0: "None", 1: "Marginal", 2: "Slight", 3: "Moderate", 4: "High"}
PCT = {1: 5, 2: 15, 3: 40, 4: 70}


def _query(layer: int, timeout: int = 20) -> dict:
    params = dict(where="1=1", geometry=json.dumps(ENVELOPE), geometryType="esriGeometryEnvelope",
                  inSR=4326, spatialRel="esriSpatialRelIntersects",
                  outFields="outlook,dn,issue_time,start_time,end_time,valid_time",
                  returnGeometry="false", f="json")
    url = f"{SERVICE}/{layer}/query?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch(now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    out = dict(fetched_utc=now.strftime("%Y-%m-%dT%H:%M:%SZ"), status="ok", tier="gov_estimate",
               source="WPC Excessive Rainfall Outlook (NOAA map services)", days=[], max_dn=0, max_day=None)
    try:
        for day in range(1, 6):
            fc = _query(day - 1)
            feats = fc.get("features") or []
            dn, rec = 0, None
            for f in feats:
                a = f.get("attributes") or {}
                try:
                    d = int(a.get("dn") or 0)
                except (TypeError, ValueError):
                    d = 0
                if d > dn:
                    dn, rec = d, a
            row = dict(day=day, dn=dn, label=LABEL.get(dn, str(dn)), pct=PCT.get(dn),
                       outlook=(rec or {}).get("outlook"), issue_time=(rec or {}).get("issue_time"),
                       start_time=(rec or {}).get("start_time"), end_time=(rec or {}).get("end_time"))
            out["days"].append(row)
            if dn > out["max_dn"]:
                out["max_dn"], out["max_day"] = dn, day
    except Exception as e:                            # noqa: BLE001 — say why, never raise
        out["status"] = f"unavailable ({type(e).__name__})"
    return out


if __name__ == "__main__":
    r = fetch()
    print(r["status"], "·", r["source"])
    for d in r.get("days", []):
        print(f"  day {d['day']}: {d['label']:9s}" + (f" (>= {d['pct']} %)  {d['start_time']} → {d['end_time']}" if d["dn"] else ""))
    sys.exit(0 if r["status"] == "ok" else 1)
