"""
feed_runner.py — wires the Cullowhee-Weather engine into publish_feed.py.

This is what the GitHub Action actually runs. publish_feed.py stays a dumb
formatter; all the integration lives here.

Why this file exists
--------------------
A cron job is stateless. Every Action run starts with an empty container and
no memory of the previous run. But two parts of flood_engine.py need memory:

  * rate_of_rise_ft_hr(series) fits a slope over the last 30 minutes, so it
    needs a stage TIME SERIES, not a single value.
  * classify_stage(stage_ft, prev_level) applies a 0.5 ft deadband on the way
    down, which only works if prev_level survives between runs.

So this runner keeps two small files in the repo and commits them each run:

  feed/history.json  rolling stage series, trimmed to HISTORY_WINDOW_MIN
  feed/state.json    last emitted level, for hysteresis

That is a deliberate choice over a database: it is free, it is inspectable in
the repo's own commit log, and the state is small. If the publish cadence ever
drops below ~5 minutes, move this to the Firestore project already configured
in sources.py — committing state that often would bloat the repo history.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import flood_engine
import publish_feed
import sources

BASIN_ID = os.getenv("CULLOWHEE_BASIN_ID", "CULLOWHEE_CK")
OUTDIR = Path("feed")
HISTORY_PATH = OUTDIR / "history.json"
STATE_PATH = OUTDIR / "state.json"

# Keep enough history for the 30-minute regression plus slack for missed runs.
HISTORY_WINDOW_MIN = 180
# If the newest sample is older than this, refuse to compute a rate — a slope
# fitted across a telemetry gap is not a rise rate, it is an artifact.
MAX_GAP_MIN = 45


# ---------------------------------------------------------------------------
# TODO(mickey): this is the one hook you need to fill in.
#
# Return the current modeled stage in feet, or None if the model cannot
# produce one right now. Returning None is a legitimate answer and is handled
# correctly downstream — the feed publishes "No Data" rather than a number.
#
# This most likely wraps whatever live_rainfall.py / flood_profile.py /
# flood_rating.py already do to turn current QPF + antecedent conditions into
# a stage. Do NOT return a placeholder constant here; a plausible fake number
# on a public URL is the failure mode this whole design is trying to avoid.
# ---------------------------------------------------------------------------
def get_modeled_stage_ft() -> Optional[float]:
    return None


def _load(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


# ---------------------------------------------------------------------------
# EXTERNAL GOVERNMENT FEEDS  (feeds.py — survey wiring 2026-07-30)
# Writes feed/external.json: measured USGS mainstem context, official NWS
# alerts, gridded FFG, the NWM reach forecast, and the NSSL FLASH cross-check
# (added 2026-08-02). Every connector is guarded individually — this step must
# NEVER sink the publish run. Consumed by live.html ("Measured & official"
# panel), flash.html (the FLASH window embedded in index.html), and anyone
# else reading the feed.
# ---------------------------------------------------------------------------
def publish_external(outdir: Path, now: datetime) -> None:
    out = {"fetched_utc": now.isoformat(), "status": {}}
    try:
        import feeds
    except Exception as e:                                   # pragma: no cover
        out["status"]["feeds"] = f"import failed: {e}"
        (outdir / "external.json").write_text(json.dumps(out, indent=2))
        return

    def _empty(v):
        """Did this feed come back carrying nothing usable?

        `ok` used to mean only "fn() did not raise". SERFC gridded FFG has been
        answering 200 with {"ffg1h": null, "ffg3h": null, "ffg6h": null} and
        being published as ok since at least 2026-07-30 - so anything reading
        this file saw a healthy flash-flood-guidance feed and had no guidance.
        A status field that says ok for a feed with no data in it is worse than
        one that says ERR, because nobody goes looking.
        """
        if v is None:
            return True
        if isinstance(v, dict):
            if v.get("error"):
                return True
            vals = [x for k, x in v.items()
                    if k not in ("source", "endpoint", "site_id", "nws_lid",
                                 "reach", "reach_is_cullowhee", "note", "status")]
            return bool(vals) and all(x is None or x == [] or x == {} for x in vals)
        if isinstance(v, (list, tuple, str)):
            return len(v) == 0
        return False

    def _try(name, fn, allow_empty=False):
        """allow_empty: this feed returning nothing is a NORMAL state, not a
        fault. `alerts` is the case that matters - no active NWS alert is the
        good day, and flagging it would train people to ignore the status
        line. Everywhere else, nothing back means no data, and it says so."""
        try:
            out[name] = fn()
            out["status"][name] = ("ok" if allow_empty or not _empty(out[name])
                                   else "empty")
        except Exception as e:
            out[name] = None
            out["status"][name] = type(e).__name__

    def _usgs():
        ctx = feeds.tuckasegee_context()
        # keep the committed diff small: last ~6 h at 15-min = 24 points
        for k in ("stage_series_up", "stage_series_down"):
            ctx[k] = (ctx.get(k) or [])[-24:]
        return ctx

    def _nwm():
        rid = feeds.NWM_REACH_CULLOWHEE or feeds.NWM_REACH_AT_GAUGE
        return {"reach": rid,
                "reach_is_cullowhee": bool(feeds.NWM_REACH_CULLOWHEE),
                "series": feeds.nwm_forecast(rid)[:36]}

    _try("usgs", _usgs)
    _try("alerts", lambda: feeds.active_alerts()[:5], allow_empty=True)
    _try("ffg_in", feeds.ffg_at)
    _try("nwm", _nwm)

    def _cucn7():
        import fiman_source
        return fiman_source.latest()
    _try("cucn7", _cucn7)

    # NSSL FLASH — third independent opinion on the roster (GOV_ESTIMATE).
    # Ships disabled: flash_source.latest() returns a status dict with
    # enabled=false until NOAH_FLASH_ENABLED is set, so this costs one
    # dict construction and no network until the self-test has passed.
    def _flash():
        import flash_source
        return flash_source.latest()
    _try("flash", _flash)

    (outdir / "external.json").write_text(json.dumps(out, indent=2))
    print("external feed:", out["status"])


def publish_outlook(outdir: Path, now: datetime) -> None:
    """feed/outlook.json — WeatherNext 2 ensemble outlook, per basin.

    OUTLOOK TIER ONLY. Everything in this file is forecast evidence: the
    published level is WATCH-capped (flood_network two-tier rule) and every
    probability is P(the FORECAST implies X), not P(X is happening). live.html
    renders it next to — never instead of — the operative posture.

    Wetness for the engine runs comes from the standard ladder via
    live_rainfall (soil percentile > API30), so the outlook shares the exact
    antecedent state the live map shows; if that fetch fails, members still
    publish with wetness=ARC-II default and the status line says so. If
    WeatherNext itself is unavailable (access not yet approved, BigQuery
    down), the file still publishes carrying the reason — the feeds.py
    lesson: an absent file reads as 'broken', a present file saying WHY is
    operational state."""
    out = {"fetched_utc": now.isoformat(), "status": "unavailable",
           "tier": "outlook", "cap": "WATCH"}
    path = outdir / "outlook.json"
    try:
        import weathernext_source as wn
        import outlook_engine
        import wetness as wet
        data = wn.latest()
        out["status"] = data["status"]
        out["source"] = data.get("source")
        out["issued_utc"] = data.get("issued_utc")
        out["n_members"] = data.get("n_members", 0)

        # NWS gridded QPF cross-check (GOV_ESTIMATE; independent of both
        # WeatherNext and Open-Meteo). Runs BEFORE the WN availability gate:
        # it carries value today, while WeatherNext access is still pending.
        # Best effort — the outlook is not gated on it, and its absence is
        # stated rather than silent.
        try:
            import nws_qpf
            atoms, _ = nws_qpf.fetch_atoms()
            out["nws_qpf24_in"] = nws_qpf.qpf24_by_basin(atoms, now=now)
            out["nws_note"] = "api.weather.gov gridded forecast, next 24 h"
        except Exception as e:                       # noqa: BLE001
            out["nws_qpf24_in"] = None
            out["nws_note"] = f"unavailable ({type(e).__name__})"

        if data["status"] != "ok":
            path.write_text(json.dumps(out, indent=2))
            print(f"outlook feed: {data['status']}")
            return

        # shared antecedent state (best effort; engine defaults are honest)
        ante, ante_note = {}, "live_rainfall unavailable; ARC-II default"
        try:
            import live_rainfall
            rows = live_rainfall.compute_from_response(
                live_rainfall.fetch_all(), now=now)
            ante = {b: dict(soil_pct=r.get("soil_moisture_pct"),
                            p5=r.get("antecedent_5day"))
                    for b, r in rows.items()}
            ante_note = "live_rainfall ladder (soil percentile > p5)"
        except Exception as e:                       # noqa: BLE001
            ante_note += f" ({type(e).__name__})"
        out["wetness_note"] = ante_note

        basins_out = {}
        for bid, members in data["basins"].items():
            mult = wn.bias_mult(bid)
            m24, starts = wn.max_window_totals(members, 24, horizon_hr=72)
            m24 = wn.apply_bias(m24, mult)
            a = ante.get(bid, {})
            try:
                fc = outlook_engine.forecast_basin_ens(
                    bid, m24, soil_pct=a.get("soil_pct"), p5_in=a.get("p5"))
            except Exception as e:                   # noqa: BLE001 — one bad basin
                basins_out[bid] = {"error": f"{type(e).__name__}: {e}"}
                continue
            fc["bias_mult"] = mult
            fc["qpf72_in"] = wn.quantiles(
                wn.apply_bias(wn.window_totals(members, 72), mult))
            fc["worst24_start_utc"] = (
                data["valid_utc"][sorted(starts)[len(starts) // 2]]
                if starts else None)                 # median member's onset
            basins_out[bid] = fc
        out["basins"] = basins_out

        # campus wetness projection: does the horizon PRIME the watershed?
        campus = data["basins"].get("CC-WCU-2260")
        if campus:
            a = ante.get("CC-WCU-2260", {})
            api0 = (a.get("p5") or 0.0) * wet.API_5DAY_EQUIV
            daily = wn.daily_series(campus, days=7)
            out["campus_wetness_projection"] = wet.project_wetness_members(
                api0, daily, month=now.month)

            # per-day P(WATCH) strip (storm_watch.html): each member's day-d
            # rain lands on that member's OWN projected antecedent state
            # (API chained through its days 1..d-1) — a coherent scenario
            # per member, not a mixed marginal. Day 1 uses today's wetness.
            mult = wn.bias_mult("CC-WCU-2260")
            w0, _ = wet.resolve_wetness(soil_pct=a.get("soil_pct"),
                                        p5_in=a.get("p5"))
            ndays = min(len(m) for m in daily) if daily else 0
            strip = []
            for d in range(ndays):
                hits = wq = 0
                qs = []
                for m in daily:
                    w_d = (w0 if d == 0 else wet.wetness_from_api(
                        wet.project_api(api0, m[:d])[-1], now.month))
                    q = m[d] * mult
                    qs.append(q)
                    r = wet.assess_wet("CC-WCU-2260", q, w_d)
                    if r["posture"] in ("WATCH", "WARNING", "EMERGENCY"):
                        hits += 1
                strip.append({
                    "day": d + 1,
                    "date_utc": data["valid_utc"][min(4 * d + 3,
                                                      len(data["valid_utc"]) - 1)][:10],
                    "p_watch": round(hits / len(daily), 3),
                    "qpf_in": wn.quantiles(qs)})
            out["campus_daily"] = strip

    except Exception as e:                           # noqa: BLE001 — belt and braces
        out["status"] = f"error: {type(e).__name__}: {e}"
    path.write_text(json.dumps(out, indent=2))
    print(f"outlook feed: {out['status']}"
          + (f" ({out.get('n_members')} members, {out.get('source')})"
             if out.get("status") == "ok" else ""))


def _trend_from_rate(rate: Optional[float]) -> str:
    if rate is None:
        return "Unknown"
    if rate > 0.05:
        return "Rising"
    if rate < -0.05:
        return "Falling"
    return "Constant"


def _tier_to_source(tier: str) -> str:
    return {
        sources.MEASURED: "SENSOR",
        sources.GOV_ESTIMATE: "GOV",
        sources.MODELED: "MODEL",
    }.get(tier, "MODEL")


def main() -> None:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)

    # External government context — independent of the stage chain, so it runs
    # first and unconditionally (also on the No-Data path below).
    try:
        publish_external(OUTDIR, now)
    except Exception as e:                                   # belt and braces
        print(f"external feed skipped: {e}")

    # WeatherNext ensemble outlook — forecast tier, independent of the stage
    # chain; runs unconditionally for the same reason publish_external does.
    try:
        publish_outlook(OUTDIR, now)
    except Exception as e:                                   # belt and braces
        print(f"outlook feed skipped: {e}")

    # 1. Resolve the best available stage. sources.resolve() already applies
    #    its own freshness and plausibility gates and will reject a sensor
    #    that has gone quiet or is reporting nonsense, falling back down the
    #    tier chain. We trust that and do not second-guess it here.
    reading = sources.resolve(
        sources.Q_STAGE, BASIN_ID, get_modeled_stage_ft(), now=now
    )

    if not reading.valid or reading.value is None:
        print(f"no valid stage: {reading.note or 'no value'} — publishing No Data")
        publish_feed.publish({"CULLOWHEE_CK": None}, outdir=str(OUTDIR))
        # Still persist state on this path. An early return that skips the
        # state files leaves the repo without them entirely, and anything
        # downstream that expects them (the workflow's git add, a consumer
        # checking freshness) breaks on a condition that is itself normal.
        state = _load(STATE_PATH, {})
        HISTORY_PATH.write_text(json.dumps(_load(HISTORY_PATH, [])))
        STATE_PATH.write_text(json.dumps({
            **state,
            "level": state.get("level", "NORMAL"),
            "updated_utc": now.isoformat(),
            "source_tier": reading.tier,
            "source_note": reading.note or "no valid stage",
        }, indent=2))
        return

    stage = float(reading.value)
    obs_ts = reading.ts or now

    # 2. Append to the rolling history, de-duplicating on timestamp so a
    #    re-run of the same Action does not inject a phantom sample.
    history = _load(HISTORY_PATH, [])
    epoch = int(obs_ts.timestamp())
    if not history or history[-1][0] != epoch:
        history.append([epoch, stage])
    cutoff = int(now.timestamp()) - HISTORY_WINDOW_MIN * 60
    history = [p for p in history if p[0] >= cutoff]
    history.sort(key=lambda p: p[0])

    # 3. Only hand the engine a series it can honestly fit. One sample, or a
    #    series with a hole in it, yields a meaningless slope.
    series = [(int(t), float(v)) for t, v in history]
    gap_min = (now.timestamp() - series[-1][0]) / 60.0 if series else 1e9
    usable = len(series) >= 3 and gap_min <= MAX_GAP_MIN
    if not usable:
        print(f"history too thin for rate ({len(series)} pts, "
              f"newest {gap_min:.0f} min old) — publishing level only")

    # 4. Run the engine, carrying the previous level so the deadband works.
    state = _load(STATE_PATH, {})
    prev_level = state.get("level", "NORMAL")

    a = flood_engine.assess(
        series if usable else [(epoch, stage)],
        prev_level=prev_level,
        soil_moisture_pct=state.get("soil_moisture_pct"),
        storm_rain_in=state.get("storm_rain_in"),
    )

    rate = a.rate_ft_hr if usable else None

    publish_feed.publish(
        {"CULLOWHEE_CK": publish_feed.Reading(
            stage_ft=a.stage_ft,
            observed_utc=obs_ts,
            trend=_trend_from_rate(rate),
            source=_tier_to_source(reading.tier),
            level=a.level,
            rise_rate_ft_hr=rate,
            hours_to_next=a.time_to_next_hr if usable else None,
            next_level=a.next_level,
            peak_cfs=a.tr55_peak_cfs,
            warn_probability=a.ew_probability,
            discharge_cfs=a.discharge_cfs,
        )},
        outdir=str(OUTDIR),
    )

    # 5. Persist state for the next run.
    HISTORY_PATH.write_text(json.dumps(history))
    STATE_PATH.write_text(json.dumps({
        **state,
        "level": a.level,
        "updated_utc": now.isoformat(),
        "source_tier": reading.tier,
        "source_note": reading.note,
    }, indent=2))

    print(f"published stage={a.stage_ft:.2f} level={a.level} "
          f"tier={reading.tier} rate={rate} pts={len(series)}")


if __name__ == "__main__":
    main()
