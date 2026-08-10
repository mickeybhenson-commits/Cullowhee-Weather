"""
weathernext_source.py — Google DeepMind WeatherNext 2 ensemble QPF connector.
=============================================================================
Feeds the OUTLOOK tier (and only the Outlook tier) with a real 64-member
ensemble, replacing the synthetic +-25% QPF perturbation in flood_ensemble
with physically-consistent members.

WHY THIS EXISTS (and its hard limits — read before extending)
  WeatherNext 2 is a GLOBAL, MEDIUM-RANGE model: 0.25 deg grid (~28 km),
  6-hour total precipitation, 4 runs/day, disseminated 6-8 h after init.
  One grid cell covers the whole watershed plus the surrounding ridges, so:
    * it CANNOT resolve orographic enhancement — the exact failure mode this
      system's shadow-mode caveat documents. Members must be bias-corrected
      by the ledger's learned per-cell multiplier before the engine sees them.
    * its precipitation targets ERA5 (known biases; Google's own docs note
      precip is "often excluded from main evaluations").
    * its latency makes it USELESS for the 0-6 h confirmation path. Nothing
      in this module may ever feed flash.html / the Confirmation tier.
  What it IS good for: calibrated 2-15 day ensemble SPREAD — probability of
  the big synoptic / tropical-remnant event, days before radar shows anything.
  Per flood_network's two-tier rule the result is capped at WATCH downstream.

PROVENANCE
  Everything here is MODELED (an AI model product; not a gauge, not NWM).
  Source tags: 'wn2-bq' (BigQuery real-time), 'wn2-fixture' (offline/test).

DATA CONTRACT (what latest() returns)
  {
    "status":     "ok" | "unavailable: <why>",
    "source":     "wn2-bq" | "wn2-fixture",
    "issued_utc": "2026-08-10T06:00:00Z",     # model init time
    "n_members":  64,
    "valid_utc":  [...],          # shared ordered 6-h window END times (UTC)
    "basins":     {bid: [[inches per window] per member]},
  }
  Amounts are INCHES per 6-h window (engine-native), converted from the
  dataset's native metres. Windows are lead-ordered from the init time.

ACCESS  [CONFIRM after Google data-request approval]
  Real-time WeatherNext 2 requires completing Google's data request form;
  historical (>48 h) is CC-BY-4.0 on Earth Engine / BigQuery. The BigQuery
  table and column names below are ENV-CONFIGURABLE because Google has not
  frozen them publicly — set WN_BQ_TABLE once your access email arrives:
      WN_BQ_TABLE   e.g. gcp-public-data-weathernext.weathernext.wn2_ens
      WN_BQ_COLS    init/valid/member/precip/geo column names (see _BQ_COLS)
  Auth is Application Default Credentials (GOOGLE_APPLICATION_CREDENTIALS).
  `google-cloud-bigquery` is an OPTIONAL dep (feeds.py pattern): absent lib,
  absent creds, or absent table => graceful "unavailable", never a crash.

OFFLINE / TEST PATH
  WN_FIXTURE=/path/to/fixture.json   loads a saved data-contract dict, so the
  whole pipeline (ensemble -> outlook -> feed -> live.html) runs with zero
  network or credentials. make_fixture() writes a deterministic synthetic one
  (seeded; no RNG at import time). Run `python weathernext_source.py` for the
  self-test.

BIAS  [the number that makes a 28-km model honest about a mountain creek]
  bias_mult(bid) returns an UPWARD-ONLY multiplier applied to member QPF
  before the engine. Ladder: WN_BIAS_MULT_JSON per-basin env > WN_BIAS_MULT
  global env > 1.0. The ledger (fetch_weathernext.py archives wn2-* sources
  against MRMS truth) is what will eventually SET these numbers per season /
  storm type; until a season of verification exists, leave them at 1.0 and
  treat the outlook probabilities as a FLOOR on the real risk.

Deps: standard library only (BigQuery import is lazy + optional).
"""

import datetime as _dt
import json
import math
import os

M_TO_IN = 39.3700787
WINDOW_HR = 6                 # WeatherNext 2 native precip accumulation
N_MEMBERS_EXPECTED = 64
MAX_LEAD_DAYS = 10            # fetch horizon (model goes to 15; 10 is plenty)

# Basin representative points — keep identical to live_rainfall.BASIN_POINTS /
# ledger/fetch_forecast.py. At 0.25 deg all eight land in 1-2 grid cells;
# per-basin series are kept anyway so a finer downscale can slot in later.
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

SRC_BQ = "wn2-bq"
SRC_FIXTURE = "wn2-fixture"

# [CONFIRM] column names once Google confirms the real-time table schema.
_BQ_COLS = dict(init="init_time", valid="forecast_time",
                member="ensemble_member",
                precip="total_precipitation_6hr",   # metres per 6-h window
                geo="geography_polygon")


def _unavailable(why):
    return {"status": f"unavailable: {why}", "source": None, "issued_utc": None,
            "n_members": 0, "valid_utc": [], "basins": {}}


# ---------------------------------------------------------------------------
# BIAS
# ---------------------------------------------------------------------------
def bias_mult(bid):
    """Upward-only orographic bias multiplier for one basin (>= 1.0).
    Mirrors live_rainfall's storm_correction contract: never scales DOWN
    (a warning system may over-call from a corrected forecast; it must not
    under-call because a correction went below 1)."""
    js = os.getenv("WN_BIAS_MULT_JSON")
    if js:
        try:
            m = json.loads(js).get(bid)
            if m is not None:
                return max(1.0, float(m))
        except (ValueError, AttributeError):
            pass                       # malformed env: fall through, note-free
    try:
        return max(1.0, float(os.getenv("WN_BIAS_MULT", "1.0")))
    except ValueError:
        return 1.0


def apply_bias(member_totals_in, mult):
    """Scale a list of member QPF totals (inches) by the bias multiplier."""
    return [round(q * mult, 3) for q in member_totals_in]


# ---------------------------------------------------------------------------
# WINDOW ALGEBRA  (pure; shared by ledger archiver + outlook publisher)
# ---------------------------------------------------------------------------
def window_totals(member_windows, hours):
    """Per-member QPF total (inches) over the FIRST `hours` of lead time.
    member_windows: [[inches per 6-h window] per member]."""
    n = max(1, hours // WINDOW_HR)
    return [round(sum(m[:n]), 3) for m in member_windows]


def max_window_totals(member_windows, hours, horizon_hr=None):
    """Per-member WORST `hours`-total (inches) anywhere in the horizon —
    a rolling-max sum of consecutive 6-h windows. This is the ensemble input
    the engine wants: assess_wet takes a 24-h design storm, and 'the biggest
    24 h the member produces this week' is the flood-relevant number, not
    'the first 24 h of lead time' (which is usually pre-storm calm).
    Returns (totals, start_idx): start_idx[i] is where member i's worst
    window begins, so callers can report WHEN the ensemble puts the event."""
    n = max(1, hours // WINDOW_HR)
    lim = None if horizon_hr is None else max(1, horizon_hr // WINDOW_HR)
    totals, starts = [], []
    for m in member_windows:
        w = m[:lim] if lim else m
        if len(w) <= n:
            totals.append(round(sum(w), 3)); starts.append(0); continue
        s = sum(w[:n]); best, bi = s, 0
        for i in range(1, len(w) - n + 1):
            s += w[i + n - 1] - w[i - 1]
            if s > best:
                best, bi = s, i
        totals.append(round(best, 3)); starts.append(bi)
    return totals, starts


def daily_series(member_windows, days=None):
    """Per-member DAILY totals (inches): chunks of four 6-h windows.
    Feeds wetness.project_wetness_members (API forward projection)."""
    out = []
    for m in member_windows:
        nd = len(m) // 4 if days is None else min(days, len(m) // 4)
        out.append([round(sum(m[4 * d:4 * d + 4]), 3) for d in range(nd)])
    return out


def quantiles(values, qs=(0.10, 0.50, 0.90)):
    """Sorted-interpolation quantiles, stdlib-only. Returns {"p10":..,...}."""
    v = sorted(x for x in values if x is not None)
    out = {}
    for q in qs:
        if not v:
            out[f"p{int(q * 100)}"] = None
            continue
        pos = q * (len(v) - 1)
        lo, hi = int(math.floor(pos)), int(math.ceil(pos))
        out[f"p{int(q * 100)}"] = round(v[lo] + (v[hi] - v[lo]) * (pos - lo), 3)
    return out


# ---------------------------------------------------------------------------
# FIXTURE PATH  (offline / CI / pre-approval)
# ---------------------------------------------------------------------------
def make_fixture(path=None, issued_utc=None, n_members=N_MEMBERS_EXPECTED,
                 days=MAX_LEAD_DAYS, wet=True):
    """Deterministic synthetic ensemble in the exact data contract.
    Seeded arithmetic (no random module): reproducible, inspectable. `wet`
    plants a 3-day synoptic event across days 1-4 so posture math exercises
    the WATCH/WARNING boundary inside the 24/48/72-h outlook windows;
    wet=False is a dry-week fixture."""
    issued = issued_utc or "2026-08-10T06:00:00Z"
    t0 = _dt.datetime.strptime(issued, "%Y-%m-%dT%H:%M:%SZ")
    nwin = days * 4
    valid = [(t0 + _dt.timedelta(hours=WINDOW_HR * (i + 1)))
             .strftime("%Y-%m-%dT%H:%M:%SZ") for i in range(nwin)]
    basins = {}
    for k, bid in enumerate(BASIN_POINTS):
        members = []
        for m in range(n_members):
            # member spread: deterministic pseudo-noise from (m, window) hash
            row = []
            for i in range(nwin):
                base = 0.0
                if wet and 4 <= i < 16:             # day 1-4 event
                    base = 0.55 + 0.25 * math.sin((i - 4) * math.pi / 12)
                jitter = 0.5 + ((m * 37 + i * 11 + k * 7) % 100) / 99.0  # 0.5-1.5
                row.append(round(base * jitter, 3))
            members.append(row)
        basins[bid] = members
    data = {"status": "ok", "source": SRC_FIXTURE, "issued_utc": issued,
            "n_members": n_members, "valid_utc": valid, "basins": basins}
    if path:
        with open(path, "w") as f:
            json.dump(data, f)
    return data


def _load_fixture(path):
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        return _unavailable(f"fixture unreadable: {e}")
    for key in ("issued_utc", "valid_utc", "basins"):
        if key not in data:
            return _unavailable(f"fixture missing '{key}'")
    data.setdefault("status", "ok")
    data.setdefault("source", SRC_FIXTURE)
    data.setdefault("n_members",
                    len(next(iter(data["basins"].values()), [])))
    return data


# ---------------------------------------------------------------------------
# BIGQUERY PATH  (real-time, post-approval)
# ---------------------------------------------------------------------------
def _fetch_bigquery(table, points=BASIN_POINTS, timeout=120):
    """Latest complete init from the WeatherNext 2 real-time BigQuery table.
    Lazy imports; every failure degrades to 'unavailable' with the reason in
    .status so feed_runner's status line says WHY (feeds.py `_empty` lesson:
    an ok that carries nothing is worse than an honest ERR)."""
    try:
        from google.cloud import bigquery            # optional dep
    except ImportError:
        return _unavailable("google-cloud-bigquery not installed")
    c = {**_BQ_COLS, **json.loads(os.getenv("WN_BQ_COLS", "{}"))}
    try:
        client = bigquery.Client()
    except Exception as e:                           # noqa: BLE001 — no ADC creds etc.
        return _unavailable(f"BigQuery auth: {type(e).__name__}: {e}")
    pts = " UNION ALL ".join(
        f"SELECT '{bid}' AS bid, ST_GEOGPOINT({lon}, {lat}) AS pt"
        for bid, (lat, lon) in points.items())
    sql = f"""
    WITH pts AS ({pts}),
    latest AS (SELECT MAX({c['init']}) AS it FROM `{table}`
               WHERE {c['init']} >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 2 DAY))
    SELECT p.bid, t.{c['init']} AS init_t, t.{c['valid']} AS valid_t,
           t.{c['member']} AS member, t.{c['precip']} AS precip_m
    FROM `{table}` t, latest, pts p
    WHERE t.{c['init']} = latest.it
      AND ST_CONTAINS(t.{c['geo']}, p.pt)
      AND t.{c['valid']} <= TIMESTAMP_ADD(latest.it, INTERVAL {MAX_LEAD_DAYS} DAY)
    ORDER BY p.bid, t.{c['member']}, t.{c['valid']}
    """
    try:
        rows = list(client.query(sql).result(timeout=timeout))
    except Exception as e:                           # noqa: BLE001
        return _unavailable(f"BigQuery query: {type(e).__name__}: {e}")
    if not rows:
        return _unavailable("query returned no rows (table empty for window?)")

    fmt = "%Y-%m-%dT%H:%M:%SZ"
    issued = min(r["init_t"] for r in rows).strftime(fmt)
    valid = sorted({r["valid_t"].strftime(fmt) for r in rows})
    vidx = {v: i for i, v in enumerate(valid)}
    basins = {}
    for r in rows:
        mem = basins.setdefault(r["bid"], {})
        row = mem.setdefault(int(r["member"]), [0.0] * len(valid))
        row[vidx[r["valid_t"].strftime(fmt)]] = round(
            float(r["precip_m"]) * M_TO_IN, 3)
    basins = {bid: [mem[k] for k in sorted(mem)] for bid, mem in basins.items()}
    n = len(next(iter(basins.values()), []))
    return {"status": "ok", "source": SRC_BQ, "issued_utc": issued,
            "n_members": n, "valid_utc": valid, "basins": basins}


# ---------------------------------------------------------------------------
# RESOLVER
# ---------------------------------------------------------------------------
def latest(mode=None):
    """Best-available WeatherNext ensemble, source ladder:
      1. WN_FIXTURE env (explicit offline/test data — out-ranks live so a
         drill can be run against a known storm without touching env creds)
      2. BigQuery real-time (needs WN_BQ_TABLE + ADC + optional dep)
      3. unavailable (callers publish the reason; nothing downstream breaks)
    `mode` forces one rung: 'fixture' | 'bigquery' | 'off'."""
    mode = mode or os.getenv("WN_MODE")
    if mode == "off":
        return _unavailable("disabled (WN_MODE=off)")
    fixture = os.getenv("WN_FIXTURE")
    if fixture and mode in (None, "fixture"):
        return _load_fixture(fixture)
    if mode == "fixture":
        return _unavailable("WN_MODE=fixture but WN_FIXTURE not set")
    table = os.getenv("WN_BQ_TABLE")
    if table:
        return _fetch_bigquery(table)
    return _unavailable("no source configured (set WN_FIXTURE or WN_BQ_TABLE)")


# ---------------------------------------------------------------------------
# self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 78)
    print("WEATHERNEXT SOURCE SELF-TEST (fixture path; no network)")
    print("=" * 78)
    d = make_fixture()
    assert d["n_members"] == N_MEMBERS_EXPECTED
    assert len(d["valid_utc"]) == MAX_LEAD_DAYS * 4
    assert set(d["basins"]) == set(BASIN_POINTS)

    campus = d["basins"]["CC-WCU-2260"]
    q24 = window_totals(campus, 24)
    q72 = window_totals(campus, 72)
    assert len(q24) == 64 and all(q >= 0 for q in q24)
    qq = quantiles(q72)
    assert qq["p10"] <= qq["p50"] <= qq["p90"]
    print(f"campus 72-h QPF: p10={qq['p10']}\" p50={qq['p50']}\" p90={qq['p90']}\"")

    w24, s24 = max_window_totals(campus, 24)
    assert all(a >= b for a, b in zip(w24, window_totals(campus, 24)))
    mq = quantiles(w24)
    print(f"campus WORST-24h QPF: p10={mq['p10']}\" p50={mq['p50']}\" "
          f"p90={mq['p90']}\"  (median member start: window {sorted(s24)[32]})")
    # capped horizon must never exceed the uncapped rolling max
    c24, _ = max_window_totals(campus, 24, horizon_hr=72)
    assert all(a <= b for a, b in zip(c24, w24))

    ds = daily_series(campus, days=7)
    assert all(len(m) == 7 for m in ds)
    # window/day conservation: day totals must re-sum to the window totals
    assert abs(sum(ds[0]) - sum(campus[0][:28])) < 1e-6

    assert bias_mult("CC-UP-503") >= 1.0
    os.environ["WN_BIAS_MULT_JSON"] = '{"CC-UP-503": 1.35}'
    assert bias_mult("CC-UP-503") == 1.35
    assert bias_mult("CC-WCU-2260") == 1.0     # falls to global default
    os.environ.pop("WN_BIAS_MULT_JSON")
    b = apply_bias([1.0, 2.0], 1.35)
    assert b == [1.35, 2.7]

    # resolver honesty: unconfigured => a REASON, not a crash or a lie
    for k in ("WN_FIXTURE", "WN_BQ_TABLE", "WN_MODE"):
        os.environ.pop(k, None)
    r = latest()
    assert r["status"].startswith("unavailable"), r["status"]
    print("resolver (unconfigured):", r["status"])

    dry = make_fixture(wet=False)
    assert max(window_totals(dry["basins"]["CC-WCU-2260"], 240)) == 0.0
    print("dry fixture: zero rain everywhere — OK")
    print("\nall weathernext_source self-tests passed")
