"""
weathernext_feed.py — Google DeepMind WeatherNext 2 ensemble as an EXTENDED-RANGE
Outlook source for NOAH.                                        [MODELED]

WHAT THIS IS
    A day 3–15 probabilistic storm-total outlook for the Cullowhee Creek
    watershed, from the 64-member WeatherNext 2 ensemble. It produces the same
    dict shape `feeds.upwind_outlook()` produces, so it drops into
    `flood_network.tiered_posture()` through the SAME noisy-OR combine and the
    SAME WATCH ceiling. It can raise an OUTLOOK to WATCH days before HRRR/RRFS
    see the storm. It can never confirm anything.

WHAT THIS IS NOT
    - Not a forecast-spine input. 0.25° (~28 km pixel), 6-hourly accumulations,
      ~7.5 h dissemination latency. The whole 23 mi² watershed is a fraction of
      one pixel: every sub-basin gets ONE number. HRRR→RRFS, HREF, NBM and MRMS
      remain the 0–18 h spine; the per-basin engine never sees this.
    - Not a hyetograph. 6-h totals interpolated to hourly cannot feed
      `run_event`; only rolling multi-hour TOTALS are used here.
    - Not a Confirmation-tier input. Provenance is MODELED, full stop.

DATA PATH
    Open-Meteo ensemble API serves WeatherNext 2 with no key
    (models=google_weathernext2_ensemble), same vendor NOAH already uses for
    Open-Meteo QPF, so `requirements.txt` is unchanged (requests only). Member
    keys come back as `precipitation` (control / member 0) and
    `precipitation_memberNN`. Native step is 6 h; Open-Meteo interpolates to
    hourly — rolling-window totals are insensitive to that, sub-6h timing is
    NOT to be read off these series.

    For the archive (Helene reforecast, Sep 2024) use Earth Engine /
    BigQuery (`projects/gcp-public-data-weathernext/assets/weathernext_2_0_0`,
    band `total_precipitation_6hr`, metres) — not wired here; see
    noah_weathernext_assessment_2026-08-23.md.

CALIBRATION
    WN2 is ERA5-trained and smooth: expect a LOW bias on WNC upslope rain.
    `WN2_BIAS_FACTOR` multiplies the pixel total before thresholding. It is 1.0
    until the Helene reforecast check sets it; a run that needed a factor is
    returned in the dict so it is auditable, never silent (same rule as
    MRMS_BIAS_FACTOR in feeds.py). Member quantiles / exceedance fractions are
    used throughout — NEVER the ensemble mean for precipitation.

WIRING (three small edits, none to the engine):
    feeds.py            : `from weathernext_feed import weathernext_outlook`
                          and add it to the self-test table.
    streamlit_app.py    : in fetch_gov_bundle(), `"extended": weathernext_outlook()`
                          (cached, exception-guarded like the others); pass
                          `extended=bundle["extended"]` to tiered_posture().
    flood_network.py    : tiered_posture(rw, warning_id="belk", upwind=None,
                          extended=None) — after the upwind block:

        if extended and extended.get("risk"):
            x_risk = extended["risk"]
            primings.append(x_risk)
            eta_hr = (round(extended["lead_min"] / 60.0, 1)
                      if extended.get("lead_min") else None)
            tp.outlook_sites.append(("WeatherNext 2 ensemble",
                                     round(x_risk, 3), eta_hr, "extended-range"))

      and in the tier-note block, when kind == "extended-range" tripped it:
        parts.append(extended["note"])
      The WATCH ceiling assertion already covers it.

    feed_runner.publish_external(): add the dict under "extended" so the public
    pages can show "Outlook, 3–10 days" separately from the hourly posture.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone

try:
    import requests
except ImportError:                       # self-test must run anywhere
    requests = None

# ---------------------------------------------------------------------------
# Location & model
# ---------------------------------------------------------------------------
LAT, LON = 35.307, -83.183                # watershed centroid (one pixel covers all 8 basins)
OPEN_METEO_ENSEMBLE = "https://ensemble-api.open-meteo.com/v1/ensemble"
WN2_MODEL = "google_weathernext2_ensemble"
FORECAST_DAYS = 15
TIMEOUT_S = 30

# ---------------------------------------------------------------------------
# Tunables  (calibrate against the Helene reforecast before trusting)
# ---------------------------------------------------------------------------
WN2_BIAS_FACTOR = 1.0      # pixel→watershed multiplier; 1.0 until reforecast sets it
WINDOW_HR = 72             # rolling storm-total window scored for the Outlook
WATCH_TOTAL_IN = 4.0       # 72-h basin total that counts as a "watch-class" storm
                           # (~2-yr 24-h depth is 3.2 in; Helene was 7–8.4 in / 36 h)
REPORT_THRESHOLDS_IN = (2.0, 4.0, 6.0, 8.0)   # exceedance fractions published for display
MIN_LEAD_HR = 18           # below this HRRR/HREF own the forecast; WN2 adds nothing
WATCH_THRESHOLD = 0.45     # mirrors flood_network.WATCH_OUTLOOK_THRESHOLD
DISCOUNT_PER_DAY = 0.0     # optional confidence decay per day of lead (0 = none)

PROVENANCE = "MODELED"
SOURCE = "WeatherNext 2 (64-member ensemble, 0.25°, via Open-Meteo)"


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------
def fetch_members(lat=LAT, lon=LON, days=FORECAST_DAYS):
    """Return (times[list[str]], members[dict[name -> list[float|None]]] in inches/hour).
    Raises on network/schema failure; the caller decides how to degrade."""
    if requests is None:
        raise RuntimeError("requests not available")
    r = requests.get(OPEN_METEO_ENSEMBLE, params={
        "latitude": lat, "longitude": lon,
        "hourly": "precipitation",
        "models": WN2_MODEL,
        "forecast_days": days,
        "precipitation_unit": "inch",
        "timezone": "UTC",
    }, timeout=TIMEOUT_S)
    r.raise_for_status()
    d = r.json()
    h = d.get("hourly") or {}
    times = h.get("time") or []
    members = {k: v for k, v in h.items() if k.startswith("precipitation")}
    unit = (d.get("hourly_units") or {}).get("precipitation", "")
    if not times or not members:
        raise ValueError(f"no ensemble precipitation in response: keys={list(h)[:6]}")
    if unit and unit != "inch":
        raise ValueError(f"unexpected precipitation unit {unit!r}")
    return times, members


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def _rolling_max(series, window):
    """Max rolling-window sum and the index (end hour) at which it first reaches
    `thr`-independent peak; None values treated as 0 (missing → no rain, which is
    the SAFE direction here only because this source can only ADD risk)."""
    vals = [0.0 if v is None else float(v) for v in series]
    acc, best, best_end = 0.0, 0.0, None
    for i, v in enumerate(vals):
        acc += v
        if i >= window:
            acc -= vals[i - window]
        if acc > best:
            best, best_end = acc, i
    return best, best_end


def _first_crossing(series, window, thr):
    """Hour index at which the rolling `window`-h total first reaches `thr`, else None."""
    vals = [0.0 if v is None else float(v) for v in series]
    acc = 0.0
    for i, v in enumerate(vals):
        acc += v
        if i >= window:
            acc -= vals[i - window]
        if acc >= thr:
            return i
    return None


def score(times, members, bias=WN2_BIAS_FACTOR, window=WINDOW_HR,
          watch_total=WATCH_TOTAL_IN, min_lead_hr=MIN_LEAD_HR):
    """Turn ensemble series into the Outlook dict. Pure function; unit-testable."""
    n = len(members)
    peaks, crossings = [], []
    for name, ser in members.items():
        ser = [None if v is None else v * bias for v in ser]
        pk, _ = _rolling_max(ser, window)
        peaks.append(pk)
        c = _first_crossing(ser, window, watch_total)
        if c is not None and c >= min_lead_hr:
            crossings.append(c)
        elif c is not None:
            # storm already inside HRRR range: still counts toward probability,
            # but lead is reported as the short-range floor
            crossings.append(min_lead_hr)

    peaks_sorted = sorted(peaks)
    def q(p):
        if not peaks_sorted:
            return None
        k = max(0, min(len(peaks_sorted) - 1, int(round(p * (len(peaks_sorted) - 1)))))
        return round(peaks_sorted[k], 2)

    exceed = {f"p_ge_{t:g}in_{window}h": round(sum(1 for p in peaks if p >= t) / n, 3)
              for t in REPORT_THRESHOLDS_IN}
    p_watch = round(sum(1 for p in peaks if p >= watch_total) / n, 3)

    lead_hr = None
    if crossings:
        cs = sorted(crossings)
        lead_hr = cs[len(cs) // 2]           # median crossing hour among members that cross
    risk = p_watch
    if DISCOUNT_PER_DAY and lead_hr:
        risk = round(risk * max(0.0, 1.0 - DISCOUNT_PER_DAY * lead_hr / 24.0), 3)

    level = "WATCH" if risk >= WATCH_THRESHOLD else "NORMAL"
    issued = times[0] if times else None
    if risk > 0 and lead_hr is not None:
        note = (f"MODELED: {int(p_watch*100)}% of {n} WeatherNext 2 members put a "
                f"{window}-h total ≥ {watch_total:g} in on the watershed, median onset "
                f"~{lead_hr/24:.1f} days out (p50 {q(0.5)} in, p90 {q(0.9)} in). "
                f"Extended-range outlook, one pixel for all basins; not a confirmation.")
    else:
        note = (f"MODELED: no WeatherNext 2 member reaches a {window}-h total of "
                f"{watch_total:g} in within {FORECAST_DAYS} days (p90 {q(0.9)} in).")

    return {
        # --- the fields flood_network.tiered_posture() reads -------------
        "risk": risk,
        "level": level,
        "lead_min": int(lead_hr * 60) if lead_hr is not None else None,
        "contributors": [{"area": "WeatherNext 2 ensemble", "dir": "—",
                          "score": risk, "upwind": False,
                          "eta_min": int(lead_hr * 60) if lead_hr is not None else None}],
        "note": note,
        # --- extended fields for display / ledger -------------------------
        "provenance": PROVENANCE,
        "source": SOURCE,
        "members": n,
        "window_hr": window,
        "watch_total_in": watch_total,
        "bias_factor": bias,
        "exceedance": exceed,
        "quantiles_in": {"p10": q(0.1), "p50": q(0.5), "p90": q(0.9), "max": q(1.0)},
        "issued_utc": issued,
        "fetched_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
    }


def weathernext_outlook(lat=LAT, lon=LON):
    """Live call. Degrades to risk 0 with an error note — an absent source must
    never read as reassurance, so the note says it is ABSENT, not calm."""
    try:
        times, members = fetch_members(lat, lon)
    except Exception as e:                       # network, schema, no requests
        return {"risk": 0.0, "level": "NORMAL", "lead_min": None, "contributors": [],
                "note": f"WeatherNext 2 outlook UNAVAILABLE ({type(e).__name__}: {e}). "
                        f"No extended-range information — not 'no rain'.",
                "provenance": PROVENANCE, "source": SOURCE, "error": str(e),
                "fetched_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")}
    return score(times, members)


# ---------------------------------------------------------------------------
# Self-test (offline) — synthetic ensembles with known answers
# ---------------------------------------------------------------------------
def _synthetic(n_members, storm_in, onset_hr, dur_hr, wet_fraction, hours=FORECAST_DAYS * 24):
    """`wet_fraction` of members carry a `storm_in` total spread evenly over
    `dur_hr` starting at `onset_hr`; the rest are dry."""
    times = [f"2026-01-01T{h % 24:02d}:00" for h in range(hours)]
    members = {}
    for m in range(n_members):
        ser = [0.0] * hours
        if m < int(round(wet_fraction * n_members)):
            for h in range(onset_hr, min(hours, onset_hr + dur_hr)):
                ser[h] = storm_in / dur_hr
        members["precipitation" if m == 0 else f"precipitation_member{m:02d}"] = ser
    return times, members


def _selftest():
    ok = True
    def check(cond, msg):
        nonlocal ok
        print(("  PASS " if cond else "  FAIL ") + msg)
        ok = ok and cond

    # 1. Helene-class: 48/64 members, 7 in over 36 h, onset day 5
    t, m = _synthetic(64, 7.0, 120, 36, 0.75)
    r = score(t, m)
    check(abs(r["risk"] - 0.75) < 1e-9, f"risk equals member fraction (got {r['risk']})")
    check(r["level"] == "WATCH", "Helene-class ensemble trips WATCH")
    check(r["lead_min"] is not None and 120 * 60 <= r["lead_min"] <= 156 * 60,
          f"lead lands inside the storm window (got {r['lead_min']} min)")
    check(r["exceedance"]["p_ge_6in_72h"] == 0.75 and r["exceedance"]["p_ge_8in_72h"] == 0.0,
          "exceedance ladder correct")
    check(r["level"] in ("NORMAL", "WATCH"), "never exceeds WATCH")

    # 2. Quiet: all dry
    t, m = _synthetic(64, 0.0, 0, 1, 0.0)
    r = score(t, m)
    check(r["risk"] == 0.0 and r["level"] == "NORMAL" and r["lead_min"] is None, "dry ensemble → NORMAL, no lead")

    # 3. Below threshold: 64/64 members, 3 in — big fraction, small storm
    t, m = _synthetic(64, 3.0, 72, 24, 1.0)
    r = score(t, m)
    check(r["risk"] == 0.0 and r["exceedance"]["p_ge_2in_72h"] == 1.0,
          "3-in storm is not watch-class but shows in the 2-in exceedance")

    # 4. Bias factor is applied and reported
    r = score(t, m, bias=1.5)
    check(r["risk"] == 1.0 and r["bias_factor"] == 1.5, "bias 1.5 lifts 3 in to 4.5 in and is reported")

    # 5. Monotonic: more rain never less risk
    t1, m1 = _synthetic(64, 4.0, 96, 24, 0.5); t2, m2 = _synthetic(64, 5.0, 96, 24, 0.6)
    check(score(t2, m2)["risk"] >= score(t1, m1)["risk"], "monotone in storm size and member fraction")

    # 6. Missing values are not rain and do not crash
    t, m = _synthetic(8, 5.0, 80, 24, 1.0)
    m["precipitation"][85] = None
    check(score(t, m)["risk"] == 1.0, "None inside a wet member tolerated")

    # 7. Storm inside short range: still scored, lead floored at MIN_LEAD_HR
    t, m = _synthetic(64, 6.0, 2, 12, 1.0)
    r = score(t, m)
    check(r["risk"] == 1.0 and r["lead_min"] == MIN_LEAD_HR * 60,
          f"near-term storm counts, lead floored to {MIN_LEAD_HR} h")

    # 8. Unavailable source never reads as calm
    global requests
    saved, requests = requests, None
    r = weathernext_outlook()
    requests = saved
    check(r["risk"] == 0.0 and "UNAVAILABLE" in r["note"] and "error" in r, "absent source is labelled ABSENT")

    print("\nSELFTEST", "OK" if ok else "FAILED")
    return ok


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    out = weathernext_outlook()
    print(json.dumps(out, indent=2))
