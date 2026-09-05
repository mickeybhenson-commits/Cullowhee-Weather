#!/usr/bin/env python3
"""
corridor_analogs.py — climatological ANALOG rung for the Cullowhee corridor,
plus the SEQUENCE flag. Built from NOAA HURDAT2 best tracks.

    python corridor_analogs.py data/hurdat_se.json -o feed/corridor_analogs.json

What it answers
  "Of the historical storms that were HERE, moving THIS way, at tropical-storm
   strength or better, what fraction went on to cross the Cullowhee corridor
   gate?"  That fraction, for the cell a live storm is in right now, is the
   ANALOG signal. It is available days before an NHC forecast track commits to
   the corridor, and it is weaker evidence than a forecast track — so it sits
   BELOW ELEVATED on the readiness ladder and, like everything forecast-based,
   can never raise more than a WATCH.

Ladder (readiness floor, lowest to highest)
  NONE → ANALOG → ELEVATED (forecast gate crossing ≤ 72 h) → WATCH_PENDING (≤ 48 h)

Gate  (identical to storm_watch.html CORRIDOR and synoptic_watch.py)
  crossing = a best-track position with lon < -83.2 and 33.0 <= lat <= 36.5,
  by a storm that was >= TS at some point before the crossing. Intensity AT the
  gate is deliberately not required: Frances, Ivan and Fred were depressions by
  the time they were inside the box, and they are the storms this system exists
  for. Rain does not care about the wind category.

Analog cells
  2.5° x 2.5° lat/lon cells, 8 heading sectors of 45°. A storm is counted once
  per (cell, sector) it occupied while >= TS. "hit" = the storm crossed the gate
  within ANALOG_HORIZON_H hours after that position. The lookup pools the 8
  neighbouring cells (same sector, then adjacent sectors) until n >= MIN_N, and
  reports which pooling level it used, so the consumer can see how thin the
  evidence is.

SEQUENCE flag
  sequence_flag(events, now) is True when the corridor was crossed by a named
  storm within SEQUENCE_WINDOW_DAYS. The deadly WNC events came in pairs (1916
  was two storms; Frances then Ivan nine days later; Helene's own predecessor
  rain). The second storm hits ground that is already wet. The flag is meant
  to lower rain-to-trip thresholds via the wetness term; it raises no posture
  by itself.

Input
  hurdat_se.json — a subset of HURDAT2 produced in the browser (see NOTES):
  [[storm_id, name, [[yyyymmdd, hhmm, status, lat, lon, wind_kt], ...]], ...]
  Statuses are HURDAT2's: TD TS HU EX SD SS LO WV DB.

Output (feed/corridor_analogs.json)
  {
    "built": iso, "source": "HURDAT2 ...", "gate": {...}, "params": {...},
    "corridor_storms": [ {id, name, year, peak_status, peak_wind,
                          first_gate: {date, time, status, wind, lat, lon},
                          track: [[lat, lon, hours, status, wind, date, time], ...]} ],
    "grid": { "lat0,lon0,sector": [n, hits], ... },
    "lookup_params": {cell_deg, sectors, min_n, horizon_h}
  }
"""
import argparse, json, math, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

GATE = dict(gateLon=-83.2, latMin=33.0, latMax=36.5, tauMax=72, tauPend=48)
CELL_DEG = 2.5
SECTORS = 8
MIN_N = 8
ANALOG_HORIZON_H = 120          # a crossing counts if it happens within 5 days of the position
ANALOG_THRESHOLD = 0.25         # fraction at/above which the ANALOG rung lights
SEQUENCE_WINDOW_DAYS = 14
TS_STATUSES = {"TS", "HU", "SS"}          # >= tropical-storm strength (SS = subtropical storm)
STATUS_RANK = {"TD": 0, "SD": 0, "LO": 0, "WV": 0, "DB": 0, "EX": 0, "TS": 1, "SS": 1, "HU": 2}


def _dt(date, hhmm):
    return datetime(int(date[:4]), int(date[4:6]), int(date[6:8]), int(hhmm[:2]), int(hhmm[2:]), tzinfo=timezone.utc)


def in_gate(lat, lon):
    return lon < GATE["gateLon"] and GATE["latMin"] <= lat <= GATE["latMax"]


def heading_deg(lat1, lon1, lat2, lon2):
    """Initial bearing, degrees clockwise from north."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def sector_of(bearing):
    return int(((bearing + 22.5) % 360) // 45.0)


def cell_of(lat, lon):
    return (math.floor(lat / CELL_DEG) * CELL_DEG, math.floor(lon / CELL_DEG) * CELL_DEG)


def cell_key(lat0, lon0, sector):
    return "%g,%g,%d" % (lat0, lon0, sector)


def load(path):
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    storms = []
    for sid, name, pts in raw:
        track = []
        for date, hhmm, status, lat, lon, wind in pts:
            track.append(dict(t=_dt(date, hhmm), date=date, time=hhmm, status=status,
                              lat=float(lat), lon=float(lon), wind=int(wind) if wind not in (None, "", -99) else None))
        track.sort(key=lambda p: p["t"])
        storms.append(dict(id=sid, name=name, year=int(sid[4:]), track=track))
    return storms


def segment_crosses_gate(p, q, steps=12):
    """True if the straight segment p->q passes through the gate box.
    Tested at sub-steps because best-track (6 h) and NHC forecast points (12-24 h)
    can straddle the box: Helene's 12Z point sat on -83.2 exactly and its 18Z point
    was already north of 36.5 N — a point-in-box test never sees that crossing."""
    for k in range(steps + 1):
        f = k / steps
        if in_gate(p["lat"] + f * (q["lat"] - p["lat"]), p["lon"] + f * (q["lon"] - p["lon"])):
            return True
    return False


def first_gate_crossing(storm):
    """First track point at/after which the track enters the gate box (segment test),
    provided the storm was >= TS at some point before the crossing."""
    was_ts = False
    tr = storm["track"]
    for i, p in enumerate(tr):
        if p["status"] in TS_STATUSES:
            was_ts = True
        if not was_ts:
            continue
        if in_gate(p["lat"], p["lon"]):
            return p
        if i + 1 < len(tr) and segment_crosses_gate(p, tr[i + 1]):
            return p
    return None


def build(storms):
    grid = defaultdict(lambda: [0, 0])      # key -> [n, hits]
    corridor = []
    for s in storms:
        gate_pt = first_gate_crossing(s)
        if gate_pt is not None:
            peak = max((p for p in s["track"] if p["wind"] is not None), key=lambda p: p["wind"], default=None)
            t0 = s["track"][0]["t"]
            corridor.append(dict(
                id=s["id"], name=s["name"], year=s["year"],
                peak_status=max(s["track"], key=lambda p: STATUS_RANK.get(p["status"], 0))["status"],
                peak_wind=peak["wind"] if peak else None,
                first_gate=dict(date=gate_pt["date"], time=gate_pt["time"], status=gate_pt["status"],
                                wind=gate_pt["wind"], lat=gate_pt["lat"], lon=gate_pt["lon"]),
                track=[[p["lat"], p["lon"], round((p["t"] - t0).total_seconds() / 3600.0, 1),
                        p["status"], p["wind"], p["date"], p["time"]] for p in s["track"]],
            ))
        # analog counting: one count per (cell, sector) per storm, while >= TS
        seen = set()
        tr = s["track"]
        for i in range(len(tr) - 1):
            p, q = tr[i], tr[i + 1]
            if p["status"] not in TS_STATUSES:
                continue
            b = heading_deg(p["lat"], p["lon"], q["lat"], q["lon"])
            key = cell_key(*cell_of(p["lat"], p["lon"]), sector_of(b))
            if key in seen:
                continue
            seen.add(key)
            hit = gate_pt is not None and 0 <= (gate_pt["t"] - p["t"]).total_seconds() / 3600.0 <= ANALOG_HORIZON_H
            grid[key][0] += 1
            grid[key][1] += 1 if hit else 0
    corridor.sort(key=lambda c: c["first_gate"]["date"])
    return corridor, dict(grid)


def analog_lookup(grid, lat, lon, bearing):
    """Pooled analog fraction for a live position. Returns dict(n, hits, frac, pool)."""
    lat0, lon0 = cell_of(lat, lon)
    sec = sector_of(bearing)
    levels = [
        ("cell", [(lat0, lon0)], [sec]),
        ("ring", [(lat0 + i * CELL_DEG, lon0 + j * CELL_DEG) for i in (-1, 0, 1) for j in (-1, 0, 1)], [sec]),
        ("ring+sectors", [(lat0 + i * CELL_DEG, lon0 + j * CELL_DEG) for i in (-1, 0, 1) for j in (-1, 0, 1)],
         [(sec - 1) % SECTORS, sec, (sec + 1) % SECTORS]),
    ]
    for name, cells, secs in levels:
        n = h = 0
        for (a, b) in cells:
            for sc in secs:
                v = grid.get(cell_key(a, b, sc))
                if v:
                    n += v[0]; h += v[1]
        if n >= MIN_N:
            return dict(n=n, hits=h, frac=(h / n if n else 0.0), pool=name)
    return dict(n=n, hits=h, frac=(h / n if n else 0.0), pool="insufficient")


def analog_floor(lookup):
    """ANALOG rung lights when the pooled fraction clears the threshold on enough storms."""
    return "ANALOG" if (lookup["n"] >= MIN_N and lookup["frac"] >= ANALOG_THRESHOLD) else "NONE"


def sequence_flag(events, now=None, window_days=SEQUENCE_WINDOW_DAYS):
    """events: iterable of datetimes (UTC) of corridor gate crossings. True if one is within the window."""
    now = now or datetime.now(timezone.utc)
    lo = now - timedelta(days=window_days)
    return any(lo <= e <= now for e in events)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("hurdat_se_json")
    ap.add_argument("-o", "--out", default="feed/corridor_analogs.json")
    args = ap.parse_args()
    storms = load(args.hurdat_se_json)
    corridor, grid = build(storms)
    out = dict(
        built=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        source="NOAA/NHC HURDAT2 Atlantic best tracks (1851-2025 release 2026-02-27), storms since 1900 with a position in the Southeast box",
        gate=GATE,
        params=dict(cell_deg=CELL_DEG, sectors=SECTORS, min_n=MIN_N, horizon_h=ANALOG_HORIZON_H,
                    threshold=ANALOG_THRESHOLD, sequence_window_days=SEQUENCE_WINDOW_DAYS,
                    storms_scanned=len(storms), corridor_storms=len(corridor)),
        ladder=["NONE", "ANALOG", "ELEVATED", "WATCH_PENDING"],
        corridor_storms=corridor,
        grid=grid,
    )
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    # ---- self-report ----
    print("scanned %d storms; %d crossed the corridor gate" % (len(storms), len(corridor)))
    for c in corridor:
        if c["year"] >= 1990 or c["year"] == 1916:
            g = c["first_gate"]
            print("  %s %-10s %s  peak %s %s kt  at gate: %s %s kt" % (c["year"], c["name"], g["date"], c["peak_status"], c["peak_wind"], g["status"], g["wind"]))
    # spot checks: a storm in the Gulf headed NNE, and one off Florida's east coast headed NW
    for lbl, lat, lon, brg in [("Gulf, 26N 87W, heading NNE", 26.0, -87.0, 20.0),
                               ("E of Florida, 27N 79W, heading NW", 27.0, -79.0, 315.0),
                               ("Bahamas, 24N 74W, heading NNW", 24.0, -74.0, 340.0),
                               ("W Gulf, 25N 94W, heading N", 25.0, -94.0, 0.0)]:
        r = analog_lookup(grid, lat, lon, brg)
        print("  analog %-36s n=%3d hits=%3d frac=%.2f pool=%s -> %s" % (lbl, r["n"], r["hits"], r["frac"], r["pool"], analog_floor(r)))
    # sequence self-test
    ev = [datetime(2004, 9, 8, tzinfo=timezone.utc)]
    assert sequence_flag(ev, datetime(2004, 9, 17, tzinfo=timezone.utc)) is True     # Ivan, 9 days after Frances
    assert sequence_flag(ev, datetime(2004, 10, 1, tzinfo=timezone.utc)) is False
    print("  sequence_flag self-test OK (Frances -> Ivan = True; +23 d = False)")
    print("wrote", args.out)


if __name__ == "__main__":
    main()
