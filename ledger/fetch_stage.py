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

    # 30-day decayed antecedent index -> wetness, mirroring wetness.py
    api_in = 0.0
    for v in pr[max(0, ti - 30):ti]:
        api_in = 0.90 * api_in + (v or 0.0)
    equiv = (1 - 0.90 ** 30) / (1 - 0.90) / 5.0
    grow = 4 <= now.month <= 10
    lo, hi = (1.4 if grow else 0.5) * equiv, (2.1 if grow else 1.1) * equiv
    if api_in <= 0:
        w = 0.0
    elif api_in < lo:
        w = 0.5 * api_in / lo
    elif api_in <= hi:
        w = 0.5 + 0.5 * (api_in - lo) / (hi - lo)
    else:
        w = 1.0

    # hourly rain from the current hour forward
    ht = j["hourly"]["time"]
    hp = j["hourly"]["precipitation"]
    stamp = now.strftime("%Y-%m-%dT%H:00")
    hi_idx = ht.index(stamp) if stamp in ht else 0
    return [hp[i] or 0.0 for i in range(hi_idx, len(hp))], round(w, 4), now


CSV_COLUMNS = ["kind", "basin_id", "issued_utc", "valid_utc", "stage_ft", "q_cfs",
               "rp_yr", "level", "wetness", "cn", "condition", "trend", "age_min",
               "fresh", "site_id", "source"]


def _append_csv(path, obs_rows, mod_rows):
    """Append-only decision log. One row per basin per run, plus the measured row.

    Append-only and plain text on purpose: it is diffable, it survives with no
    database host, and it is the correct-negative denominator that FAR and CSI
    need. Every run that is not logged is a verification sample that cannot be
    recovered later.
    """
    import csv as _csv
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
        for (bid, issued, valid, stage, q, rp, level, w, cn, src) in mod_rows:
            wr.writerow({"kind": "model", "basin_id": bid, "issued_utc": issued,
                         "valid_utc": valid, "stage_ft": stage, "q_cfs": q,
                         "rp_yr": rp, "level": level, "wetness": w, "cn": cn,
                         "source": src})


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

    try:
        import cwm_model
        hourly, w, issued = forcing()
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
            _st = "  --  " if r["stage_ft"] is None else f"{r['stage_ft']:6.2f}"
            print(f"modeled   {bid:<14} {_st} ft · {r['calib_q']:>6.0f} cfs · "
                  f"RP {r['rp_yr']:>5} yr · {r['posture']:<9} · peak +{r['peak_hr']} h")
        print(f"modeled   forcing: w={w} · {r['total_in']} in · ONE point "
              f"(single-cell — see module docstring)")
    except Exception as e:
        print(f"modeled   unavailable: {e}", file=sys.stderr)

    if a.dry_run:
        print("DRY RUN — nothing written")
        return 0
    if not (obs_rows or mod_rows):
        print("nothing to write")
        return 1

    if a.csv:
        _append_csv(a.csv, obs_rows, mod_rows)
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
