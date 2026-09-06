#!/usr/bin/env python3
"""
starlink_overhead.py — which Starlink satellites can serve the FRESHET gateway, and when.

The dish never tells you which satellite it is linked to (the terminal API exposes
latency, obstruction and the ground PoP, never the satellite). The best that can be
done honestly is the candidate set: every operational Starlink satellite above the
user-terminal elevation mask from the gateway site, refreshed on the terminal's
15-second re-plan cadence. satellite-tracker.html draws that set and ranks it.

This module does the heavy part server-side so the page stays light:
  1. pull the Starlink group from CelesTrak (~8,000 TLEs);
  2. keep the ones in an operational shell (altitude from mean motion, 440–620 km) —
     satellites still raising orbit or de-orbiting cannot carry user traffic;
  3. propagate the survivors (sgp4) over the next WINDOW_MIN minutes at STEP_S and keep
     any that reach >= MASK_DEG elevation from the site, with the rise/set of that pass so
     the page only propagates satellites that are in (or about to be in) view;
  4. write feed/starlink_overhead.json with their TLEs, so the browser propagates a few
     hundred objects instead of eight thousand.

    python starlink_overhead.py            -> writes feed/starlink_overhead.json
    python starlink_overhead.py --print    -> lists what is in view right now

Never raises; on failure the file carries status="unavailable (...)" and the page falls
back to its baked sample and says so. GOV/none — orbital data are public catalog
elements; "in view" is geometry, "likely link" is the page's educated guess.
"""
from __future__ import annotations

import json
import math
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

SOURCE = "https://celestrak.org/NORAD/elements/gp.php?GROUP=starlink&FORMAT=tle"
SITE = dict(name="FRESHET gateway (Cullowhee, NC)", lat=35.3137, lon=-83.1765, alt_km=0.64)
MASK_DEG = 25.0            # Starlink user-terminal elevation mask (FCC waiver, 2021)
SHELL_KM = (440.0, 620.0)  # operational shells (mean-motion altitude; the 480 km Gen2 shell reads ~463); outside = raising or decaying
WINDOW_MIN = 45            # one 30-min feed cycle with margin
STEP_S = 60                # coarse pass search; the page refines at 15 s
MU = 398600.4418
RE = 6378.137


def _fetch_tle(timeout: int = 30) -> list[tuple[str, str, str]]:
    with urllib.request.urlopen(SOURCE, timeout=timeout) as r:
        lines = [ln.rstrip() for ln in r.read().decode("utf-8", "replace").splitlines() if ln.strip()]
    out = []
    for i in range(0, len(lines) - 2, 3):
        n, l1, l2 = lines[i], lines[i + 1], lines[i + 2]
        if l1.startswith("1 ") and l2.startswith("2 "):
            out.append((n.strip(), l1, l2))
    return out


def altitude_km(line2: str) -> Optional[float]:
    """Mean altitude from the TLE mean motion (rev/day)."""
    try:
        n_rev = float(line2[52:63])
    except ValueError:
        return None
    if n_rev <= 0:
        return None
    n = n_rev * 2 * math.pi / 86400.0
    a = (MU / n / n) ** (1.0 / 3.0)
    return a - RE


def operational(line2: str) -> bool:
    alt = altitude_km(line2)
    return alt is not None and SHELL_KM[0] <= alt <= SHELL_KM[1]


def _site_ecef(lat, lon, h_km):
    la, lo = math.radians(lat), math.radians(lon)
    f = 1 / 298.257223563
    e2 = f * (2 - f)
    N = RE / math.sqrt(1 - e2 * math.sin(la) ** 2)
    return ((N + h_km) * math.cos(la) * math.cos(lo),
            (N + h_km) * math.cos(la) * math.sin(lo),
            (N * (1 - e2) + h_km) * math.sin(la))


def _gmst(dt: datetime) -> float:
    jd = dt.timestamp() / 86400.0 + 2440587.5
    t = (jd - 2451545.0) / 36525.0
    g = 280.46061837 + 360.98564736629 * (jd - 2451545.0) + 0.000387933 * t * t - t ** 3 / 38710000.0
    return math.radians(g % 360.0)


def elevation_deg(r_eci, dt: datetime, site_ecef, lat, lon) -> float:
    g = _gmst(dt)
    cg, sg = math.cos(g), math.sin(g)
    x = r_eci[0] * cg + r_eci[1] * sg
    y = -r_eci[0] * sg + r_eci[1] * cg
    z = r_eci[2]
    dx, dy, dz = x - site_ecef[0], y - site_ecef[1], z - site_ecef[2]
    la, lo = math.radians(lat), math.radians(lon)
    # ENU up-vector dot range vector
    up = (math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la))
    rng = math.sqrt(dx * dx + dy * dy + dz * dz)
    return math.degrees(math.asin((dx * up[0] + dy * up[1] + dz * up[2]) / rng))


def select(tles, now: datetime, window_min=WINDOW_MIN, step_s=STEP_S, mask=MASK_DEG) -> list[dict]:
    """Operational satellites that reach >= mask elevation from SITE inside the window."""
    from sgp4.api import Satrec, jday
    site = _site_ecef(SITE["lat"], SITE["lon"], SITE["alt_km"])
    times = [now + timedelta(seconds=k * step_s) for k in range(int(window_min * 60 / step_s) + 1)]
    jds = [jday(t.year, t.month, t.day, t.hour, t.minute, t.second + t.microsecond / 1e6) for t in times]
    keep = []
    for name, l1, l2 in tles:
        if not operational(l2):
            continue
        try:
            sat = Satrec.twoline2rv(l1, l2)
        except Exception:                             # noqa: BLE001
            continue
        best, t_best, rise, sett = -90.0, None, None, None
        for t, (jd, fr) in zip(times, jds):
            e, r, _v = sat.sgp4(jd, fr)
            if e != 0:
                break
            el = elevation_deg(r, t, site, SITE["lat"], SITE["lon"])
            if el > best:
                best, t_best = el, t
            if el >= mask and rise is None:
                rise = t - timedelta(seconds=step_s)          # widened by one step each side
            if el < mask and rise is not None and sett is None and t > rise + timedelta(seconds=step_s):
                sett = t
        if best >= mask:
            fmt = "%Y-%m-%dT%H:%M:%SZ"
            keep.append(dict(name=name, norad=int(l1[2:7]), cospar=l1[9:17].strip(),
                             alt_km=round(altitude_km(l2), 1), max_elev=round(best, 1),
                             max_at=t_best.strftime(fmt), rise=rise.strftime(fmt),
                             set=(sett or times[-1] + timedelta(seconds=step_s)).strftime(fmt),
                             tle1=l1, tle2=l2))
    keep.sort(key=lambda s: -s["max_elev"])
    return keep


def build(now: Optional[datetime] = None, tles=None) -> dict:
    now = now or datetime.now(timezone.utc)
    out = dict(fetched_utc=now.strftime("%Y-%m-%dT%H:%M:%SZ"), status="ok", source="CelesTrak GP (Starlink group)",
               site=SITE, mask_deg=MASK_DEG, shell_km=list(SHELL_KM), window_min=WINDOW_MIN,
               replan_s=15, fov_half_deg=50, n_catalog=0, n_operational=0, sats=[],
               note="candidate set only — the terminal never reports its serving satellite; "
                    "the page ranks these by elevation, boresight offset and time left in view")
    try:
        if tles is None:
            tles = _fetch_tle()
        out["n_catalog"] = len(tles)
        out["n_operational"] = sum(1 for _n, _l1, l2 in tles if operational(l2))
        out["sats"] = select(tles, now)
    except Exception as e:                            # noqa: BLE001
        out["status"] = f"unavailable ({type(e).__name__})"
    return out


def publish(outdir: Path, now: Optional[datetime] = None) -> dict:
    out = build(now)
    p = Path(outdir) / "starlink_overhead.json"
    p.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"starlink overhead: {out['status']} · {len(out['sats'])} of {out['n_operational']} operational "
          f"reach >= {MASK_DEG:.0f}° in the next {WINDOW_MIN} min")
    return out


if __name__ == "__main__":
    out = publish(Path("feed"))
    if "--print" in sys.argv:
        for s in out["sats"][:30]:
            print(f"  {s['name']:16s} {s['alt_km']:6.1f} km  max elev {s['max_elev']:5.1f}° at {s['max_at']}")
    sys.exit(0 if out["status"] == "ok" else 1)
