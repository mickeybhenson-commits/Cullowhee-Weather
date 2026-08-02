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

    def _try(name, fn):
        try:
            out[name] = fn()
            out["status"][name] = "ok"
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
    _try("alerts", lambda: feeds.active_alerts()[:5])
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
