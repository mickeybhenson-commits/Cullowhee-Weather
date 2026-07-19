"""
gov_sources.py  —  bridge the measured government-gauge arc (gov_gauges.py) into
the basin-posture engine (sources.py / live_rainfall.compute_from_response).

There are TWO ways a gov gauge can touch a basin posture, and they are NOT
interchangeable. Picking the wrong one can SUPPRESS a warning, so read this.

  (A) UPWARD-ONLY QPF-BIAS CORRECTION   <- use this for the DISTANT arc gauges
      (Franklin, Highlands, ...). A gauge 30 km away is a leading indicator, not
      the basin's rain. So we never substitute its value; instead, when it shows
      the forecast UNDER-calling on a basin's inflow direction, we scale that
      basin's MODELED storm rain UP toward reality — and never down. It can raise
      a posture (the whole point) but can never hide a storm.  -> storm_correction_map()

  (B) resolve() SENSOR SUBSTITUTION     <- use this ONLY for a TRUE IN-BASIN gauge
      (a SKYE/Ambient/Tempest unit physically in the sub-basin), where observed
      rain IS the basin's rain. Then it legitimately out-ranks the model as a
      MEASURED/GOV_ESTIMATE reading.  -> GovGaugeBackend

WHY NOT substitute a distant gauge straight into resolve() as the storm rain:
  1. Semantic mismatch — Q_RAIN_STORM is a FORECAST of the worst upcoming 24 h;
     a gauge reports OBSERVED PAST rain. Before the storm arrives the gauge reads
     ~0, which (fresh + in range) would out-rank a high forecast and DELETE the
     storm from the outlook.
  2. Orographic under-catch — a valley gauge reads low for the ridges above the
     watershed, again biasing toward under-warning.
  Correction (A) is immune to both: it is monotonic upward and keyed off the
  bias ratio, not a raw substitution.

Deps: sources.py, gov_gauges.py (both stdlib-only). No network at import.
"""

from datetime import timezone, datetime

import sources
import gov_gauges as gov

# --------------------------------------------------------------------------
# (A) UPWARD-ONLY QPF-BIAS CORRECTION  — for the distant approach-arc gauges
# --------------------------------------------------------------------------
# Which compass direction each sub-basin's storms predominantly arrive from.
# This decides which arc gauge's bias applies to which basin. EXAMPLE / opt-in:
# left empty so nothing changes until you deliberately set it. Prevailing WNC
# track is SW/S; tune per basin against the Helene backtest.
SUGGESTED_BASIN_INFLOW = {
    # "CC-UP-503":   "SW",   # upper Mountain basin — Franklin approach
    # "CC-TIL-705":  "S",    # Tilley — Highlands escarpment
    # "CC-MS-1100":  "SW",
    # "CC-SPD-1830": "SW",
}

CORRECTION_CAP = 2.5   # never scale a basin's storm rain up by more than this [tunable]


def storm_correction_map(basin_inflow, gauge_rows, modeled_upwind_rows,
                         window="h24", cap=CORRECTION_CAP):
    """UPWARD-ONLY per-basin multiplier for modeled storm rain.

    basin_inflow:        {basin_id: compass_dir} — a basin's dominant inflow dir.
    gauge_rows:          gov_gauges.gauge_rows() output (measured arc).
    modeled_upwind_rows: live_rainfall.upwind_compute() output (model, per dir).

    For each basin, look up the measured-vs-modeled QPF bias on its inflow
    direction; if the gauge shows the forecast under-calling (ratio > 1), return
    a factor = clamp(ratio, 1.0, cap). Otherwise 1.0. NEVER < 1.0 — a gauge that
    reads low (valley under-catch, or storm not yet arrived) can only leave the
    forecast unchanged, never suppress it. Returns {basin_id: factor}."""
    if not basin_inflow:
        return {}
    model_by_dir = {r["dir"]: r.get(window) for r in (modeled_upwind_rows or [])}
    bias = gov.qpf_bias(gauge_rows or [], model_by_dir, window=window)
    out = {}
    for bid, dirn in basin_inflow.items():
        bd = bias.get(dirn)
        ratio = bd["ratio"] if bd else None
        out[bid] = round(min(cap, max(1.0, ratio)), 2) if ratio else 1.0
    return out


# --------------------------------------------------------------------------
# (B) resolve() SENSOR SEAM  — ONLY for a gauge physically in/adjacent the basin
# --------------------------------------------------------------------------
def _parse_utc(iso):
    """gov_gauges latest_iso (naive UTC) -> tz-aware UTC datetime for gating."""
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


class GovGaugeBackend(sources.SensorBackend):
    """A sources backend that serves a gov gauge as a basin reading — intended
    ONLY for a gauge sited IN or immediately adjacent to the sub-basin, where its
    observed rain is a faithful stand-in for the basin's rain. Returns a
    GOV_ESTIMATE reading (ranks below a true in-basin MEASURED sensor, above the
    model). Gated by sources.resolve() for freshness + range like any sensor.

    For DISTANT arc gauges (Franklin/Highlands relative to a headwater basin), do
    NOT use this — use storm_correction_map() instead (see module docstring).

    basin_gauge_map: {basin_id: station_id}  (station_id as in gov_gauges.GAUGES)
    quantities:      which quantities to serve (default: trailing observed rain)
    window:          which gov-gauge trailing total feeds the value ('h1'/'h24')
    scale:           optional {basin_id: factor} orographic uplift (default 1.0)
    ttl_s:           cache the arc fetch this long to avoid refetching per basin
    fetcher:         override for tests (defaults to gov_gauges.gauge_rows)
    """
    def __init__(self, basin_gauge_map, quantities=(sources.Q_RAIN_STORM,),
                 window="h24", scale=None, ttl_s=300, fetcher=None,
                 token=None, tier=sources.GOV_ESTIMATE, now_fn=sources._utcnow):
        self.map = dict(basin_gauge_map or {})
        self.quantities = set(quantities)
        self.window = window
        self.scale = dict(scale or {})
        self.ttl_s = ttl_s
        self._fetcher = fetcher or gov.gauge_rows
        self.token = token
        self.tier = tier
        self._now_fn = now_fn
        self._cache = None          # (fetched_at, {stid: row})

    def _rows(self):
        now = self._now_fn()
        if self._cache is not None:
            fetched_at, rows = self._cache
            if (now - fetched_at).total_seconds() < self.ttl_s:
                return rows
        try:
            rows, _err = self._fetcher(token=self.token)
            by_stid = {r["station"]: r for r in rows}
        except Exception:
            by_stid = {}            # network/parse failure -> serve nothing (-> model)
        self._cache = (now, by_stid)
        return by_stid

    def latest(self, quantity, basin_id):
        if quantity not in self.quantities:
            return None
        stid = self.map.get(basin_id)
        if stid is None:
            return None
        row = self._rows().get(stid)
        if row is None or row.get("qc") != "ok":
            return None
        val = row.get(self.window)
        if val is None:
            return None
        val = round(val * self.scale.get(basin_id, 1.0), 2)
        return sources.Reading(
            val, self.tier, f'{row["area"]} ({stid}) gauge-proxy',
            _parse_utc(row.get("latest_iso")), quantity,
            note="in-basin gov-gauge proxy")


def install(basin_gauge_map=None, scale=None, firestore=None,
            quantities=(sources.Q_RAIN_STORM,), token=None):
    """Convenience: set the active sources backend to a priority chain of
    (real in-basin sensors) > (in-basin gov-gauge proxy) > (model). Pass a
    FirestoreBackend as `firestore` when your SKYE/NOAH ingest is live. With an
    empty map this is a no-op chain (everything stays MODELED)."""
    chain = []
    if firestore is not None:
        chain.append(firestore)
    if basin_gauge_map:
        chain.append(GovGaugeBackend(basin_gauge_map, quantities=quantities,
                                     scale=scale, token=token))
    sources.set_backend(sources.ChainBackend(chain) if chain else sources.NullBackend())
    return sources.current_backend()


if __name__ == "__main__":
    # Demo (A): upward-only correction. Franklin (SW) measures 2x the forecast;
    # a SW-fed basin gets its modeled storm rain scaled up 2x. A basin whose
    # gauge reads LOW is left unchanged (never suppressed).
    gauge_rows = [
        {"area": "Franklin", "station": "351205083213545", "dir": "SW",
         "dist_km": 30, "qc": "ok", "h24": 2.0},
        {"area": "Highlands", "station": "HDSN7", "dir": "S",
         "dist_km": 25, "qc": "ok", "h24": 0.3},   # reads LOW (valley/under-catch)
    ]
    modeled = [{"dir": "SW", "h24": 1.0}, {"dir": "S", "h24": 1.0}]
    inflow = {"CC-UP-503": "SW", "CC-TIL-705": "S"}
    corr = storm_correction_map(inflow, gauge_rows, modeled)
    print("upward-only storm correction:", corr)
    assert corr["CC-UP-503"] == 2.0          # forecast under-called SW -> scale up 2x
    assert corr["CC-TIL-705"] == 1.0          # gauge low -> NEVER scale down
    print("  CC-UP-503 x2.0 (SW under-called), CC-TIL-705 x1.0 (never suppressed)  OK")
