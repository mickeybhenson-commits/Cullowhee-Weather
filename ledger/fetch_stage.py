#!/usr/bin/env python3
"""
fetch_stage.py — verify the model's OUTPUT against the one measured gage.
=============================================================================
fetch_forecast.py / fetch_mrms.py verify the model's INPUT: forecast rain
against radar-observed rain. This closes the other end. It records, for
CC-SPD-1830 (Speedwell):

  MEASURED  NCEM FIMAN gage 25380, 830 ft from the pour point and the only
            measured stream stage in the watershed. Via fiman_source.latest(),
            so the freshness gate and condition mapping cannot drift from the
            engine's.

  MODELED   cwm_model.assess_event() on the same Open-Meteo forcing the live
            page uses — the REAL forecast hyetograph, not the SCS Type II
            design shape (see the note above assess_event).

DATUM. Do not difference the two stage columns and call it error:

    stage_obs.stage_ft    feet above GAGE DATUM 2125.0 ft NAVD88
    stage_model.stage_ft  feet above CHANNEL BED (Manning rectangular)

basins.py has bed_ft = None for CC-SPD-1830 — the bed has never been tied to
NAVD88. obs - mod is (model error) + (unknown constant offset). What IS valid
today: level agreement, rate of rise (a constant offset differentiates away),
and — the point of logging both columns — recovering the offset itself:

    SELECT COUNT(*) n,
           AVG(obs_peak_ft - mod_peak_ft)  AS mean_offset_ft,
           AVG(obs_peak_ft*mod_peak_ft) - AVG(obs_peak_ft)*AVG(mod_peak_ft)
             AS cov
    FROM stage_pairs_6h WHERE n_fresh > 0;

Regressed across enough events, the intercept IS the datum tie and the slope
says whether the rating shape is right. That is a survey you get for free by
writing two numbers to disk every half hour.

2026-08-11: the MODELED half now logs ALL EIGHT basins, not just Speedwell.
Verification (POD / FAR / CSI) has to be computed per basin against each basin's
own lead requirement — a pooled score hides that Cox Branch and Long Branch, the
flashiest reaches, are where the system is most likely to fail. Logging one basin
made a per-basin score impossible. The MEASURED half is still Speedwell only,
because it is still the only gage in the watershed.

Caveat, recorded deliberately: all eight basins are forced from ONE Open-Meteo
point. That is the same single-cell forcing the live page uses and it is why the
basins move in lockstep; basin-averaged MRMS QPE is the open fix. The log is
honest about it via the `source` column — do not read basin-to-basin spread in
this table as real spatial contrast.

Run: every 30 min from systemd (deploy/qpf-stage.timer), or by hand:
    python3 fetch_stage.py [--db PATH] [--csv PATH] [--dry-run]
`--csv` appends to a plain append-only CSV. Use it when no SQLite host is up:
an unlogged storm is a permanently lost verification sample, and enough of them
take years to accumulate.
Deps: standard library, plus the repo modules fiman_source and cwm_model.
"""

import argparse
import datetime as dt
import json
import os
import sys
import urllib.parse
import urllib.request

import ledger_db

# repo root, for fiman_source / cwm_model
sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..")))

BASIN = "CC-SPD-1830"
POINT = (35.270, -83.190)          # identical to fetch_forecast.BASIN_POINTS
API = "https://api.open-meteo.com/v1/forecast"
MODEL_SOURCE = "noah-live"
TIMEOUT = 30


def _iso(d):
    return d.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def measured():
    """Gated FIMAN reading, or None. Delegates entirely to fiman_source so the
    75-min gate and the CONDITION_TXT -> level map stay in one place."""
    try:
        import fiman_source
    except Exception as e:                       # pragma: no cover
        print(f"fiman_source unavailable: {e}", file=sys.stderr)
        return None
    try:
        return fiman_source.latest()
    except Exception as e:
        print(f"FIMAN fetch failed: {e}", file=sys.stderr)
        return None


# --- antecedent wetness ------------------------------------------------------
# DELIBERATE DUPLICATE of wetness.api_from_daily / wetness.wetness_from_api.
#
# Why not just import them: this collector's one job is to never miss a sample —
# the correct-negative denominator only exists if collection is continuous — and
# an import of a repo-root module that fails on a runner costs a row. mrms_live is
# imported lazily for exactly that reason and degrades to an empty column; wetness
# cannot degrade, because without it there is no model row at all.
#
# So the copy stays, and the RISK OF THE COPY is handled where risk of a copy
# always has to be handled in this repo: with a test that fails when the two
# drift. See test_registry_engine_consistency.test_ledger_wetness_matches_wetness_py.
# Four weeks of an unshippable 1.5-yr WATCH and eight days of stale LiDAR ladders
# were both a second copy that nothing compared.
#
# Verified 2026-08-13: identical to wetness.py to 0.0 across 12 months x API 0-12 in.
API_K = 0.90
API_DAYS = 30
API_5DAY_EQUIV = (1 - API_K ** API_DAYS) / (1 - API_K) / 5.0


def api_from_daily(daily_in):
    """Decayed antecedent precipitation index over a daily rainfall series."""
    api = 0.0
    for v in daily_in:
        api = API_K * api + (v or 0.0)
    return api


def wetness_from_api(api_in, month):
    """API -> wetness index in [0,1]. NRCS 5-day breakpoints (1.4/2.1 growing,
    0.5/1.1 dormant) rescaled by API_5DAY_EQUIV, linear between and below."""
    grow = 4 <= month <= 10
    lo = (1.4 if grow else 0.5) * API_5DAY_EQUIV
    hi = (2.1 if grow else 1.1) * API_5DAY_EQUIV
    if api_in <= 0:
        return 0.0
    if api_in < lo:
        return 0.5 * api_in / lo
    if api_in <= hi:
        return 0.5 + 0.5 * (api_in - lo) / (hi - lo)
    return 1.0


def forcing():
    """(hourly_in_from_now, wetness, issued_utc) for the Speedwell point.

    Same call shape as live.html: 31 past days for the antecedent index, the
    forecast horizon for the event, hourly precipitation for the real shape.
    """
    q = urllib.parse.urlencode({
        "latitude": POINT[0], "longitude": POINT[1],
        "daily": "precipitation_sum",
        "hourly": "precipitation",
        "past_days": 31, "forecast_days": 3,
        "precipitation_unit": "inch", "timezone": "UTC",
        "cell_selection": "nearest",
    })
    with urllib.request.urlopen(f"{API}?{q}", timeout=TIMEOUT) as r:
        j = json.load(r)

    now = dt.datetime.now(dt.timezone.utc)
    today = now.date().isoformat()
    days = j["daily"]["time"]
    pr = j["daily"]["precipitation_sum"]
    ti = days.index(today) if today in days else max(len(days) - 3, 0)

    api_in = api_from_daily(pr[max(0, ti - API_DAYS):ti])
    w = wetness_from_api(api_in, now.month)

    # hourly rain from the current hour forward
    ht = j["hourly"]["time"]
    hp = j["hourly"]["precipitation"]
    stamp = now.strftime("%Y-%m-%dT%H:00")
    hi_idx = ht.index(stamp) if stamp in ht else 0
    return [hp[i] or 0.0 for i in range(hi_idx, len(hp))], round(w, 4), now


# Columns 1-16 are what the run knew at decision time. They are written once and
# never revised.
#
# Columns 17-20 are what ACTUALLY HAPPENED, filled in later by a human or a
# reconciliation job. Added 2026-08-12 because the ledger could not do the job its
# own workflow header claims for it: "verification (POD / FAR / CSI) needs the
# correct-negative denominator." True, and insufficient — POD, FAR and CSI each
# need the OUTCOME as well as the prediction. The log was accumulating forecasts
# with nowhere to record truth, which looks complete and scores nothing.
#
# Schema follows noah_decision_ledger.md, which specified these on 2026-08-03 and
# whose implementation never reached the repo.
#
# `outcome` vocabulary, deliberately small:
#   flood      a flood occurred in this basin in this window
#   no_flood   confirmed nothing happened (a real observation, not an absence of one)
#   unknown    nobody looked, or nobody could tell   <- the honest default
# Leave it EMPTY rather than writing "no_flood" when nobody checked. An unverified
# quiet hour is not a correct negative, and scoring it as one inflates every skill
# statistic the ledger exists to produce.
OUTCOME_COLUMNS = ["outcome", "outcome_ts", "outcome_src", "outcome_note"]

# FORCING columns. Until 2026-08-12 the log recorded the posture and the wetness but
# never the rainfall that produced them, so skill could not be scored against the
# forcing that drove it.
#
#   rain_in     depth the model actually consumed for this basin, inches. Today that
#               is ONE Open-Meteo point shared by all eight basins, which is why the
#               basins move in lockstep. Recording it makes that visible in the data
#               rather than only in the docs.
#   mrms_in     basin-AREA-AVERAGED observed rainfall over the same window, from
#               mrms_live.observed_rain() across that basin's mask cells.
#   mrms_valid  surviving weight fraction behind mrms_in (1.0 = every mask cell
#               reported). A basin averaged over half its cells must not look like a
#               basin averaged over all of them.
#
# The pair is the point: one column is a point forecast, the other is a measured
# area mean, and the difference between them accumulated over a season is the local
# bias correction that MRMS needs here — radar ran 20-33% low against gauges in WNC
# during Helene. See noah_basin_averaged_qpe_2026-08-12.md.
#
# EMPTY, never 0.0, whenever MRMS could not be read. A zero in a rainfall column is
# a claim that it did not rain.
QPE_COLUMNS = ["rain_in", "mrms_in", "mrms_valid"]

CSV_COLUMNS = ["kind", "basin_id", "issued_utc", "valid_utc", "stage_ft", "q_cfs",
               "rp_yr", "level", "wetness", "cn", "condition", "trend", "age_min",
               "fresh", "site_id", "source"] + OUTCOME_COLUMNS + QPE_COLUMNS


def mrms_observed(hours=1):
    """{basin_id: {"in": float, "valid": float}} of area-averaged observed rain.

    Returns {} on ANY failure — missing decoder, network, upstream outage, a basin
    below the coverage floor. Never partial-credit zeros: mrms_live.observed_rain
    omits a basin it cannot measure rather than reporting 0.0, and this preserves
    that. The ledger job must not fail because a radar product was late.
    """
    try:
        import mrms_live
    except Exception as e:
        print(f"mrms      decoder unavailable ({e}) — mrms_in left empty",
              file=sys.stderr)
        return {}
    try:
        got = mrms_live.observed_rain(hours=hours)
    except Exception as e:
        print(f"mrms      fetch failed ({e}) — mrms_in left empty", file=sys.stderr)
        return {}
    out = {}
    for bid, v in (got.get("basins") or {}).items():
        try:
            out[bid] = {"in": round(float(v["in"]), 3),
                        "valid": round(float(v["valid_frac"]), 3)}
        except (KeyError, TypeError, ValueError):
            continue
    if out:
        print(f"mrms      {got.get('product')} · {len(out)}/8 basins · "
              f"latency ~{got.get('latency_min')} min")
    return out


def _migrate_csv_header(path):
    """Widen an existing log to the current schema, once, in place.

    Only ever ADDS columns, and only when the on-disk header is an exact prefix of
    CSV_COLUMNS — so this cannot reorder, rename or drop a field, and it refuses
    loudly on any header it does not recognise rather than guessing. Existing rows
    are padded with empty strings, which is the correct value for "nobody has
    recorded an outcome for this row yet".

    Writes to a temp file and renames, so an interrupted run leaves the original
    intact. The ledger-stage workflow sets `concurrency: cancel-in-progress: false`,
    which serialises runs, so there is no writer racing this.
    """
    import csv as _csv
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return "new"
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(_csv.reader(fh))
    if not rows:
        return "new"
    header = rows[0]
    if header == CSV_COLUMNS:
        return "current"
    if header != CSV_COLUMNS[:len(header)]:
        raise SystemExit(
            f"{path}: header is not a prefix of the current schema, refusing to "
            f"migrate.\n  on disk: {header}\n  expected prefix of: {CSV_COLUMNS}\n"
            "Fix by hand — an automatic guess here would corrupt the verification "
            "record.")
    pad = [""] * (len(CSV_COLUMNS) - len(header))
    tmp = path + ".migrating"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        wr = _csv.writer(fh)
        wr.writerow(CSV_COLUMNS)
        for r in rows[1:]:
            wr.writerow(r + pad[:len(CSV_COLUMNS) - len(r)])
    os.replace(tmp, path)
    return f"migrated +{len(pad)} column(s), {len(rows)-1} row(s) padded"


def _append_csv(path, obs_rows, mod_rows, qpe=None):
    """Append-only decision log. One row per basin per run, plus the measured row.

    Append-only and plain text on purpose: it is diffable, it survives with no
    database host, and it is the correct-negative denominator that FAR and CSI
    need. Every run that is not logged is a verification sample that cannot be
    recovered later.

    The outcome columns are left EMPTY here by design — this function records what
    was predicted, never what happened. Anything that fills them in must be a
    separate pass with its own evidence, or the log starts marking its own homework.
    """
    import csv as _csv
    state = _migrate_csv_header(path)
    if state.startswith("migrated"):
        print(f"  ledger schema: {state}")
    new = not os.path.exists(path) or os.path.getsize(path) == 0
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "a", newline="", encoding="utf-8") as fh:
        wr = _csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        if new:
            wr.writeheader()
        for (bid, valid, stage, cond, level, trend, age, fresh, site, src) in obs_rows:
            wr.writerow({"kind": "obs", "basin_id": bid, "valid_utc": valid,
                         "stage_ft": stage, "level": level, "condition": cond,
                         "trend": trend, "age_min": age, "fresh": fresh,
                         "site_id": site, "source": src})
        qpe = qpe or {}
        for (bid, issued, valid, stage, q, rp, level, w, cn, src) in mod_rows:
            row = {"kind": "model", "basin_id": bid, "issued_utc": issued,
                   "valid_utc": valid, "stage_ft": stage, "q_cfs": q,
                   "rp_yr": rp, "level": level, "wetness": w, "cn": cn,
                   "source": src}
            f = qpe.get(bid) or {}
            # Absent stays absent. DictWriter emits "" for keys we never set.
            if f.get("rain_in") is not None:
                row["rain_in"] = f["rain_in"]
            if f.get("mrms_in") is not None:
                row["mrms_in"] = f["mrms_in"]
                row["mrms_valid"] = f.get("mrms_valid")
            wr.writerow(row)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db")
    ap.add_argument("--csv", help="append rows to this CSV as well as / instead of "
                                  "SQLite; survives having no database host")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    obs_rows, mod_rows = [], []

    m = measured()
    if m:
        obs_rows.append((
            BASIN, m.get("last_updated_utc") or _iso(dt.datetime.now(dt.timezone.utc)),
            m.get("stage_ft"), m.get("condition"), m.get("level"), m.get("trend"),
            m.get("age_min"), 1 if m.get("fresh") else 0, "25380",
            "fiman-live" if m.get("source") else "fiman-csv"))
        print(f"measured  {m.get('stage_ft')} ft · {m.get('condition')} · "
              f"level={m.get('level')} · age={m.get('age_min')} min · "
              f"fresh={m.get('fresh')}")
    else:
        print("measured  unavailable")

    qpe = {}
    try:
        import cwm_model
        hourly, w, issued = forcing()
        mrms = mrms_observed(hours=1)
        for bid in cwm_model.ORDER:
            try:
                r = cwm_model.assess_event(bid, hourly, w)
            except Exception as e:                     # one basin must not lose the rest
                print(f"modeled   {bid}: {e}", file=sys.stderr)
                continue
            valid = issued + dt.timedelta(hours=r["peak_hr"])
            mod_rows.append((bid, _iso(issued), _iso(valid), r["stage_ft"],
                             r["calib_q"], r["rp_yr"], r["posture"], w, r["CN"],
                             MODEL_SOURCE))
            # Forcing side-channel. Deliberately NOT folded into mod_rows: that
            # tuple is also consumed by ledger_db.insert_stage_model, and widening
            # it would break the SQLite path for a column only the CSV needs.
            f = {"rain_in": r.get("total_in")}
            if bid in mrms:
                f["mrms_in"] = mrms[bid]["in"]
                f["mrms_valid"] = mrms[bid]["valid"]
            qpe[bid] = f
            _st = "  --  " if r["stage_ft"] is None else f"{r['stage_ft']:6.2f}"
            print(f"modeled   {bid:<14} {_st} ft · {r['calib_q']:>6.0f} cfs · "
                  f"RP {r['rp_yr']:>5} yr · {r['posture']:<9} · peak +{r['peak_hr']} h")
        print(f"modeled   forcing: w={w} · {r['total_in']} in · ONE point "
              f"(single-cell — see module docstring)")
        if mrms:
            spread = [v["in"] for v in mrms.values()]
            print(f"mrms      basin-averaged observed: {min(spread):.2f}-"
                  f"{max(spread):.2f} in across {len(spread)} basins "
                  f"(spread {max(spread) - min(spread):.2f} in)")
    except Exception as e:
        print(f"modeled   unavailable: {e}", file=sys.stderr)

    if a.dry_run:
        print("DRY RUN — nothing written")
        return 0
    if not (obs_rows or mod_rows):
        print("nothing to write")
        return 1

    if a.csv:
        _append_csv(a.csv, obs_rows, mod_rows, qpe)
        print(f"appended {len(obs_rows) + len(mod_rows)} row(s) -> {a.csv}")
        if not a.db and not os.environ.get("QPF_LEDGER_DB"):
            return 0            # CSV-only run (e.g. CI); no database host expected

    conn = ledger_db.connect(a.db)
    if obs_rows:
        ledger_db.insert_stage_obs(conn, obs_rows)
    if mod_rows:
        ledger_db.insert_stage_model(conn, mod_rows)
    conn.close()
    print(f"wrote {len(obs_rows)} observation, {len(mod_rows)} model row(s) "
          f"-> {ledger_db.db_path(a.db)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
