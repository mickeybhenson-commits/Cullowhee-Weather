"""
nws_qpf.py — NWS gridded QPF (api.weather.gov) as a second forecast source.
===========================================================================
The operational QPF input today is Open-Meteo. This adds the National
Weather Service's own gridded forecast (the GSP forecaster-adjusted grids,
2.5 km) as an INDEPENDENT second opinion:

  * ledger/fetch_nws_qpf.py archives it beside om-best so the bias analysis
    can answer "does the NWS grid under-call our orographic rain less than
    the global models do?" — with a human forecaster in the loop at GSP,
    the answer is plausibly yes, and that would make it the better
    operational input.
  * feed_runner adds a small nws_qpf block to outlook.json so the UI can
    cross-check the model QPF against the official forecast.

PROVENANCE: GOV_ESTIMATE (official model product, feeds.py contract).
Source tag 'nws-ndfd'.

API SHAPE (why the parsing below looks the way it does)
  api.weather.gov/points/{lat},{lon} -> resolves to a gridpoint URL
  api.weather.gov/gridpoints/{wfo}/{x},{y} -> properties
    .quantitativePrecipitation.values = [
        {"validTime": "2026-08-10T18:00:00+00:00/PT6H", "value": 2.54}, ...]
  value is mm accumulated over the ISO-8601 DURATION suffix (PT1H..PT6H,
  occasionally longer). Atoms are split UNIFORMLY into hourly values (same
  caveat as the WeatherNext archiver: hourly atoms are an artifact; 6-h
  window sums are what the ledger verifies).

  The gridpoint mapping for a fixed site never changes, so the /points
  lookups are done ONCE per process and cached; the 8 basin points collapse
  onto only a couple of unique 2.5-km cells, and unique cells are fetched
  once and shared.

Politeness: api.weather.gov requires a descriptive User-Agent with contact
info and will 403 without one. Unauthenticated, free, no key.

Run `python nws_qpf.py --selftest` for the offline parsing self-test;
running with no args does a live fetch (needs network).
Standard library only.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import urllib.request

MM_TO_IN = 1.0 / 25.4
UA = ("Cullowhee-Weather flood early-warning (WCU research; "
      "mickey.b.henson@gmail.com)")
API = "https://api.weather.gov"

# Keep identical to live_rainfall.BASIN_POINTS / weathernext_source.
BASIN_POINTS = {
    "CC-UP-503":     (35.241, -83.185),
    "CC-MS-1100":    (35.265, -83.190),
    "CC-TIL-705":    (35.268, -83.205),
    "CC-SPD-1830":   (35.270, -83.190),
    "CC-COX-097":    (35.302, -83.178),
    "CC-LB-171":     (35.305, -83.195),
    "CC-WCU-2260":   (35.290, -83.185),
    "CC-MOUTH-2340": (35.300, -83.185),
}

SOURCE = "nws-ndfd"
_grid_cache: dict = {}          # (lat,lon) -> gridpoint URL
_DUR = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?)?$")


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/geo+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def duration_hours(iso_dur):
    """'PT6H' -> 6, 'P1D' -> 24, 'P1DT6H' -> 30. Unparseable -> None
    (callers drop the atom rather than guess)."""
    m = _DUR.match(iso_dur or "")
    if not m or (m.group(1) is None and m.group(2) is None):
        return None
    return int(m.group(1) or 0) * 24 + int(m.group(2) or 0)


def parse_qpf_values(values):
    """NWS quantitativePrecipitation values -> hourly atoms.
    Returns [(end_hour_utc_iso, mm)] with each duration split uniformly;
    the stamped hour is the accumulation END (ledger convention)."""
    atoms = []
    for v in values or []:
        vt = (v.get("validTime") or "").split("/")
        if len(vt) != 2 or v.get("value") is None:
            continue
        hrs = duration_hours(vt[1])
        if not hrs:
            continue
        try:
            start = dt.datetime.fromisoformat(vt[0]).astimezone(dt.timezone.utc)
        except ValueError:
            continue
        mm_h = float(v["value"]) / hrs
        for h in range(1, hrs + 1):
            end = start + dt.timedelta(hours=h)
            atoms.append((end.strftime("%Y-%m-%dT%H:00:00"), round(mm_h, 4)))
    return atoms


def gridpoint_url(lat, lon):
    key = (round(lat, 4), round(lon, 4))
    if key not in _grid_cache:
        p = _get(f"{API}/points/{lat:.4f},{lon:.4f}")["properties"]
        _grid_cache[key] = (f"{API}/gridpoints/{p['gridId']}"
                            f"/{p['gridX']},{p['gridY']}")
    return _grid_cache[key]


def fetch_atoms(points=BASIN_POINTS):
    """Hourly QPF atoms per basin, fetching each unique 2.5-km cell once.
    Returns ({bid: [(end_iso, mm)]}, n_unique_cells)."""
    urls = {bid: gridpoint_url(lat, lon) for bid, (lat, lon) in points.items()}
    by_url = {}
    for url in set(urls.values()):
        vals = (_get(url)["properties"]
                .get("quantitativePrecipitation", {}).get("values"))
        by_url[url] = parse_qpf_values(vals)
    return {bid: by_url[urls[bid]] for bid in points}, len(by_url)


def qpf24_by_basin(atoms_by_bid, now=None):
    """Next-24-h QPF total (inches) per basin from hourly atoms."""
    now = now or dt.datetime.now(dt.timezone.utc)
    lo = now.strftime("%Y-%m-%dT%H:00:00")
    hi = (now + dt.timedelta(hours=24)).strftime("%Y-%m-%dT%H:00:00")
    return {bid: round(sum(mm for end, mm in atoms if lo < end <= hi) * MM_TO_IN, 2)
            for bid, atoms in atoms_by_bid.items()}


# ---------------------------------------------------------------------------
# self-test (offline)
# ---------------------------------------------------------------------------
def _selftest():
    assert duration_hours("PT6H") == 6
    assert duration_hours("PT1H") == 1
    assert duration_hours("P1D") == 24
    assert duration_hours("P1DT6H") == 30
    assert duration_hours("junk") is None
    assert duration_hours("P") is None

    values = [
        {"validTime": "2026-08-10T18:00:00+00:00/PT6H", "value": 6.0},   # 1 mm/h
        {"validTime": "2026-08-11T00:00:00+00:00/PT2H", "value": 5.08},  # .1"/h
        {"validTime": "2026-08-11T02:00:00+00:00/PT1H", "value": None},  # dropped
        {"validTime": "bad/PT1H", "value": 1.0},                          # dropped
    ]
    atoms = parse_qpf_values(values)
    assert len(atoms) == 8, atoms
    assert atoms[0] == ("2026-08-10T19:00:00", 1.0)
    assert atoms[5] == ("2026-08-11T00:00:00", 1.0)
    assert abs(atoms[6][1] - 2.54) < 1e-9
    # conservation: hourly atoms re-sum to the original accumulations
    assert abs(sum(a[1] for a in atoms) - (6.0 + 5.08)) < 1e-6

    now = dt.datetime(2026, 8, 10, 18, 0, tzinfo=dt.timezone.utc)
    q24 = qpf24_by_basin({"CC-WCU-2260": atoms}, now=now)
    assert abs(q24["CC-WCU-2260"] - round((6.0 + 5.08) * MM_TO_IN, 2)) < 1e-9
    print("q24 sample:", q24)
    print("all nws_qpf self-tests passed (offline parsing path)")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    else:
        atoms, ncells = fetch_atoms()
        print(f"fetched {ncells} unique NWS grid cells")
        for bid, q in qpf24_by_basin(atoms).items():
            print(f"  {bid:14s} next-24h QPF {q:.2f}\"")
