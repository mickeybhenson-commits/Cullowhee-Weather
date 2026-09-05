#!/usr/bin/env python3
"""
readiness.py — the four-step readiness chain, per basin, with provenance on every number.

    floor rises  →  read wetness  →  inches-to-trip at THAT wetness  →  compare forecast rain
                                                                    →  margin, ceiling, tags

Runs inside feed_runner (every publish cycle) and writes feed/readiness.json. The chain
never changes as sensors arrive: every input is resolved through sources.resolve(), which
returns the best available source and its tier (MEASURED > GOV_ESTIMATE > MODELED). Today
almost everything resolves MODELED; the day a NOAH node reports, the same call returns
MEASURED for that basin and the readout's tag flips. Nothing else moves.

Steps
  1. FLOOR      — corridor readiness from live NHC guidance + HURDAT2 analogs
                  (NONE / ANALOG / ELEVATED / WATCH_PENDING), latched until the storm has
                  passed. Soil never changes this rung; it answers "is rain coming here?"
  2. WETNESS    — per basin: sources.resolve(Q_SOIL) → soil %; modeled fallback from the
                  live_rainfall ladder (soil percentile > API); ARC-II default if all fail.
  3. TRIP       — inches of 24-h rain (Type II shape) to reach WATCH / WARNING / EMERGENCY
                  at the resolved wetness, by bisection on wetness.assess_wet (the same
                  engine the outlook feed uses). The mouth has no ladder by decision → null.
  4. FORECAST   — WeatherNext qpf24/qpf72 quantiles + NWS QPF24 from feed/outlook.json
                  (GOV_ESTIMATE). margin_in = trip[WATCH] − qpf24 p50 (and p90).
                  outlook_level = the rung the p50 rain would reach, CAPPED AT WATCH.
  Ceiling       — WATCH unless a measured stage exists for the basin (sources Q_STAGE,
                  or Q_STAGE_GOV at Speedwell). Forecast evidence never confirms.

Mode — the wake-up call (Mickey, 2026-09-05: "it doesn't need to be a warning; it is a wake-up
call for the system to start looking at soil moisture and stream depths and discharge")
  The floor was only ever one alarm. The mode is the highest alarm ringing, and it changes
  how closely the system LOOKS, never what posture it shows:
    QUIET      nothing ringing
    ATTENTION  a storm is on the belt (ANALOG), or WPC has a Marginal/Slight excessive-rain
               risk over the watershed, or the ground has been wetting for days, or forecast
               rain at p90 reaches a basin's WATCH line
    STORM      a forecast track crosses the corridor (ELEVATED / WATCH_PENDING), or WPC
               Moderate/High, or forecast rain at p50 reaches a WATCH line
  Each alarm names itself in `alarms` so the card and the operator message say WHY. The mode
  publishes a cadence recommendation the gateway/nodes and the feed loop can honour. Nothing in
  the mode raises a posture; WATCH still needs forecast rain against the trip line and
  WARNING still needs a measured rise.

Two-tier rule and the absent-data rule apply throughout: a sensor that is missing, stale
or out of range falls down the ladder and the readout SAYS SO in `note`; nothing is
silently substituted, and nothing is ever rendered as calm because it is unknown.

Filling the gaps (no code change to this chain):
  * FirestoreBackend (sources.py) — the ingest path; DORMANT until creds + docs exist.
  * noah_readings.FileBackend    — feed/noah/readings.json written by the gateway/Notehub
                                   exporter; the simplest thing that can possibly deploy.
  * fiman_source.FimanStageBackend — Speedwell state gage (Q_STAGE_GOV), already live.
  install_backends() chains them: NOAH file → Firestore → FIMAN → null.

Output: feed/readiness.json
  { fetched_utc, status, floor:{level, why, storms:[...], latched, sequence},
    basins:{bid:{name, lead_min, wetness:{w, soil_pct, tier, source, ts, note},
                 trip_in:{WATCH, WARNING, EMERGENCY} | null,
                 forecast:{qpf24_p50, qpf24_p90, qpf72_p50, qpf72_p90, nws_qpf24, tier, source},
                 margin_in:{p50, p90} | null, outlook_level, ceiling,
                 stage:{value, tier, source, note} | null,
                 sensors:{deployed:[q...], pending:[q...]}}},
    ladder:[...], cap:"WATCH", notes:[...] }
"""
from __future__ import annotations

import json
import math
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import basins as B
import sources
import wetness as wet

try:
    import corridor_analogs as CA
except Exception:                                    # noqa: BLE001
    CA = None

LADDER = ["NONE", "ANALOG", "ELEVATED", "WATCH_PENDING"]
CAP = "WATCH"
NHC_CURRENT = "https://www.nhc.noaa.gov/CurrentStorms.json"
NHC_MAPSERVER = ("https://mapservices.weather.noaa.gov/tropical/rest/services/tropical/"
                 "NHC_tropical_weather_summary/MapServer")
LATCH_HOURS = 36            # a met floor holds this long after the last forecast crossing time
MODES = ["QUIET", "ATTENTION", "STORM"]
CADENCE = {                 # recommended sampling / polling per mode (minutes)
    "QUIET":     dict(feed=30, fiman=30, node_stage=15, node_soil=360, node_rain="on tip"),
    "ATTENTION": dict(feed=15, fiman=15, node_stage=10, node_soil=60,  node_rain="on tip"),
    "STORM":     dict(feed=15, fiman=15, node_stage=5,  node_soil=60,  node_rain="on tip",
                      backhaul="satellite rung reserved for stage exceedances"),
}
WET_TREND_DAYS = 3          # wetness rising over this many days rings ATTENTION
WET_TREND_MIN = 0.04        # ... if the mean daily rise is at least this (w units/day)
WET_TREND_FLOOR = 0.6       # ... and the current wetness is at least this
PLANNED = {                 # what NOAH is expected to deploy per basin (for the pending list)
    "stage": [sources.Q_STAGE], "soil": [sources.Q_SOIL], "rain": [sources.Q_RAIN_1H, sources.Q_RAIN_STORM]}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


# --------------------------------------------------------------------------------------
# Backends — the gap fillers
# --------------------------------------------------------------------------------------
def install_backends() -> None:
    """NOAH file → Firestore → FIMAN → whatever is current. Each is gated independently."""
    chain = []
    try:
        import noah_readings
        chain.append(noah_readings.FileBackend())
    except Exception:                                # noqa: BLE001
        pass
    try:
        chain.append(sources.FirestoreBackend())      # dormant until creds/docs exist
    except Exception:                                # noqa: BLE001
        pass
    try:
        import fiman_source
        chain.append(fiman_source.FimanStageBackend())
    except Exception:                                # noqa: BLE001
        pass
    chain.append(sources.current_backend())
    sources.set_backend(sources.ChainBackend(chain))


# --------------------------------------------------------------------------------------
# Step 1 — floor
# --------------------------------------------------------------------------------------
def _get_json(url: str, timeout: int = 20):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_nhc(now: datetime) -> dict:
    """Active storms with current position/heading and forecast points (tau hours)."""
    cur = _get_json(NHC_CURRENT)
    storms = {}
    for s in cur.get("activeStorms", []):
        storms[str(s.get("name", "")).upper()] = dict(
            name=s.get("name"), cls=s.get("classification", ""), wind=s.get("intensity"),
            lat=_f(s.get("latitudeNumeric")), lon=_f(s.get("longitudeNumeric")),
            heading=_f(s.get("movementDir")), points=[])
    if not storms:
        return storms
    svc = _get_json(NHC_MAPSERVER + "?f=json")
    ids = [l["id"] for l in svc.get("layers", []) if "forecast point" in str(l.get("name", "")).lower()]
    for lid in ids:
        fc = _get_json(NHC_MAPSERVER + f"/{lid}/query?where=1%3D1&outFields=*&returnGeometry=true&f=geojson")
        for f in fc.get("features", []):
            a = f.get("properties") or {}
            g = f.get("geometry") or {}
            if g.get("type") != "Point":
                continue
            key = str(a.get("STORMNAME") or a.get("STORMNAM") or "").upper()
            st = storms.get(key)
            if st is None:
                continue
            tau = a.get("TAU", a.get("FCSTPRD", a.get("FHOUR")))
            try:
                tau = float(tau)
            except (TypeError, ValueError):
                tau = None
            st["points"].append(dict(lat=g["coordinates"][1], lon=g["coordinates"][0], tau=tau,
                                     status=str(a.get("STORMTYPE") or a.get("DVLBL") or "")))
    return storms


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


_STATUS_RANK = {"TD": 0, "STD": 0, "PTC": 0, "TS": 1, "STS": 1, "HU": 2, "MH": 3}


def _rank(cls: str) -> int:
    c = (cls or "").upper()
    for k, v in _STATUS_RANK.items():
        if k in c:
            return v
    if "HURRICANE" in c:
        return 2
    if "STORM" in c:
        return 1
    return 0


def eval_storm(st: dict, grid: Optional[dict]) -> dict:
    """Mirror of storm_watch.html evalStorm: segment gate test, then the analog rung."""
    pts = sorted([p for p in st["points"] if p["tau"] is not None], key=lambda p: p["tau"])
    if st.get("lat") is not None and st.get("lon") is not None:
        pts.insert(0, dict(lat=st["lat"], lon=st["lon"], tau=0.0, status=st.get("cls", "")))
    gate = CA.GATE if CA else dict(gateLon=-83.2, latMin=33.0, latMax=36.5, tauMax=72, tauPend=48)

    def in_gate(lat, lon):
        return lon < gate["gateLon"] and gate["latMin"] <= lat <= gate["latMax"]

    if pts and pts[0]["tau"] <= 3 and in_gate(pts[0]["lat"], pts[0]["lon"]):
        return dict(met=True, floor="WATCH_PENDING", tau=0.0, inside=True, analog=None)
    if _rank(st.get("cls", "")) < 1:
        return dict(met=False, floor="NONE", tau=None, inside=False, analog=None)
    best = None
    for i, p in enumerate(pts):
        if in_gate(p["lat"], p["lon"]) and p["tau"] <= gate["tauMax"]:
            best = p["tau"] if best is None else min(best, p["tau"])
        if i + 1 < len(pts):
            q = pts[i + 1]
            for k in range(13):
                f = k / 12.0
                if in_gate(p["lat"] + f * (q["lat"] - p["lat"]), p["lon"] + f * (q["lon"] - p["lon"])):
                    t = p["tau"] + f * (q["tau"] - p["tau"])
                    if t <= gate["tauMax"]:
                        best = t if best is None else min(best, t)
                    break
    if best is not None:
        return dict(met=True, tau=best, inside=False, analog=None,
                    floor="WATCH_PENDING" if best <= gate["tauPend"] else "ELEVATED")
    analog = None
    if CA and grid and st.get("lat") is not None and st.get("heading") is not None:
        lk = CA.analog_lookup(grid, st["lat"], st["lon"], st["heading"])
        analog = dict(lk, heading=st["heading"])
        return dict(met=False, floor=CA.analog_floor(lk), tau=None, inside=False, analog=analog)
    return dict(met=False, floor="NONE", tau=None, inside=False, analog=None)


def compute_floor(now: datetime, feed_dir: Path, state_path: Path) -> dict:
    """Live floor from NHC, with latch and SEQUENCE. Never raises; says why on failure."""
    out = dict(level="NONE", why="", storms=[], latched=False, sequence=None, status="ok")
    grid = None
    try:
        grid = json.loads((feed_dir / "corridor_analogs.json").read_text(encoding="utf-8")).get("grid")
    except Exception as e:                            # noqa: BLE001
        out["notes"] = [f"analog grid unavailable ({type(e).__name__}); ANALOG rung disabled"]
    state = {}
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:                                 # noqa: BLE001
        state = {}
    try:
        storms = fetch_nhc(now)
    except Exception as e:                            # noqa: BLE001
        out["status"] = f"nhc unavailable ({type(e).__name__})"
        # hold the last floor if its latch is still inside the window; otherwise unknown
        lat = state.get("latch")
        if lat and datetime.fromisoformat(lat["until"]) > now:
            out.update(level=lat["level"], why=f"holding last floor — {out['status']}", latched=True)
        else:
            out.update(level="UNKNOWN", why=out["status"])
        return out
    level = "NONE"
    for key, st in storms.items():
        r = eval_storm(st, grid)
        row = dict(name=st["name"], cls=st["cls"], wind=st["wind"], floor=r["floor"], tau=r["tau"],
                   inside=r["inside"], analog=r["analog"])
        out["storms"].append(row)
        if LADDER.index(r["floor"]) > LADDER.index(level):
            level = r["floor"]
        if r["met"]:
            until = now + timedelta(hours=LATCH_HOURS if r["inside"] else (r["tau"] or 0) + LATCH_HOURS)
            state["latch"] = dict(level=r["floor"], until=_iso(until), storm=st["name"])
    # latch: a floor that was met stays met until the storm has had time to pass through
    lat = state.get("latch")
    if lat and level in ("NONE", "ANALOG"):
        try:
            if datetime.fromisoformat(lat["until"].replace("Z", "+00:00")) > now:
                level, out["latched"] = lat["level"], True
                out["why"] = f"holding {lat['level']} for {lat['storm']} until {lat['until']}"
        except Exception:                             # noqa: BLE001
            pass
    if not out["why"]:
        hits = [s["name"] for s in out["storms"] if s["floor"] in ("ELEVATED", "WATCH_PENDING")]
        anas = [s["name"] for s in out["storms"] if s["floor"] == "ANALOG"]
        out["why"] = (", ".join(hits) + " meets the corridor gate") if hits else \
                     (", ".join(anas) + " · climatological analog") if anas else \
                     ("no active tropical cyclones" if not storms else "no corridor criterion met")
    out["level"] = level
    # corridor_events.json — the producer for the SEQUENCE flag. A storm inside the box
    # today is recorded once (by name + date); the file always exists after a publish so
    # the page can tell "no crossings" from "no producer".
    ev_path = feed_dir / "corridor_events.json"
    try:
        events = json.loads(ev_path.read_text(encoding="utf-8")).get("events", [])
    except Exception:                                 # noqa: BLE001
        events = []
    for s in out["storms"]:
        if s.get("inside"):
            key = (str(s["name"]).upper(), now.strftime("%Y-%m-%d"))
            if not any((str(e.get("name", "")).upper(), e.get("date")) == key for e in events):
                events.append(dict(name=s["name"], date=now.strftime("%Y-%m-%d"),
                                   cls=s.get("cls"), wind=s.get("wind"), source="readiness.py"))
    try:
        ev_path.write_text(json.dumps(dict(written_utc=_iso(now), window_days=14,
                                           events=events[-50:]), indent=1), encoding="utf-8")
    except OSError:
        pass
    # SEQUENCE: a gate crossing within the window
    try:
        ev = events
        dts = [datetime.fromisoformat(e["date"]).replace(tzinfo=timezone.utc) for e in ev if e.get("date")]
        if CA and CA.sequence_flag(dts, now):
            out["sequence"] = "corridor crossed within 14 days — second-storm rain falls on wet ground"
    except Exception:                                 # noqa: BLE001
        pass
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return out


# --------------------------------------------------------------------------------------
# Step 2 — wetness, step 3 — inches to trip
# --------------------------------------------------------------------------------------
def resolve_wetness(bid: str, modeled: dict, now: datetime) -> dict:
    """sources.resolve(Q_SOIL) with the live_rainfall ladder as the modeled fallback."""
    m_soil = modeled.get("soil_moisture_pct")
    r = sources.resolve(sources.Q_SOIL, bid, m_soil, now=now,
                        modeled_source="live_rainfall ladder (Open-Meteo soil percentile)")
    if r.valid and r.value is not None:
        w = wet.wetness_from_soil_percentile(r.value)
        return dict(w=round(w, 3), soil_pct=round(float(r.value), 1), tier=r.tier, source=r.source,
                    ts=_iso(r.ts), note=r.note)
    # soil unavailable everywhere: API / p5 / default, all MODELED and labelled
    w, tag = wet.resolve_wetness(soil_pct=None, api_in=modeled.get("api30"),
                                 p5_in=modeled.get("antecedent_5day"), month=now.month)
    return dict(w=round(w, 3), soil_pct=None, tier=sources.MODELED, source=tag, ts=None,
                note=(r.note or "no soil source; " + tag))


def trip_inches(bid: str, w: float, lo: float = 0.0, hi: float = 30.0, tol: float = 0.05) -> Optional[dict]:
    """24-h rain (Type II shape) to first reach each rung at wetness w. None where no ladder."""
    if B.BASINS[bid].get("thr_ft") is None or B.BASINS[bid].get("rating") == "none":
        return None
    order = ["NORMAL", "WATCH", "WARNING", "EMERGENCY"]

    def rung(q):
        p = wet.assess_wet(bid, q, w)["posture"]
        return order.index(p) if p in order else -1

    try:
        if rung(hi) < 1:
            return dict(WATCH=None, WARNING=None, EMERGENCY=None, note=f"not reached by {hi} in")
    except Exception as e:                            # noqa: BLE001
        return dict(WATCH=None, WARNING=None, EMERGENCY=None, note=f"engine error {type(e).__name__}")
    out = {}
    for i, lvl in enumerate(order[1:], start=1):
        a, b = lo, hi
        if rung(b) < i:
            out[lvl] = None
            continue
        while b - a > tol:
            m = 0.5 * (a + b)
            if rung(m) >= i:
                b = m
            else:
                a = m
        out[lvl] = round(b, 2)
    return out


# --------------------------------------------------------------------------------------
# Step 4 — forecast, margin, ceiling
# --------------------------------------------------------------------------------------
def forecast_for(bid: str, outlook: dict) -> dict:
    b = (outlook.get("basins") or {}).get(bid) or {}
    q24, q72 = b.get("qpf24_in") or {}, b.get("qpf72_in") or {}
    nws = (outlook.get("nws_qpf24_in") or {}).get(bid)
    ok = outlook.get("status") == "ok"
    return dict(qpf24_p50=q24.get("p50") if ok else None, qpf24_p90=q24.get("p90") if ok else None,
                qpf72_p50=q72.get("p50") if ok else None, qpf72_p90=q72.get("p90") if ok else None,
                nws_qpf24=nws, tier=sources.GOV_ESTIMATE if (ok or nws is not None) else None,
                source=("WeatherNext-2 ensemble" if ok else "outlook unavailable")
                       + (" + NWS gridded QPF" if nws is not None else ""),
                status=outlook.get("status"))


def outlook_level(trip: Optional[dict], rain: Optional[float]) -> str:
    if trip is None:
        return "N/A"
    if rain is None:
        return "UNKNOWN"
    lvl = "NORMAL"
    for r in ("WATCH", "WARNING", "EMERGENCY"):
        t = trip.get(r)
        if t is not None and rain >= t:
            lvl = r
    return "WATCH" if lvl in ("WARNING", "EMERGENCY") else lvl     # forecast tier cap


def resolve_stage(bid: str, now: datetime) -> Optional[dict]:
    """Measured stage if any backend has one (NOAH Q_STAGE, or FIMAN Q_STAGE_GOV). None otherwise."""
    for q in (sources.Q_STAGE, sources.Q_STAGE_GOV):
        r = sources.resolve(q, bid, None, now=now)
        if r.tier != sources.MODELED:
            return dict(value=r.value, tier=r.tier, source=r.source, ts=_iso(r.ts), note=r.note,
                        quantity=q, valid=r.valid)
        if r.note:                                     # a sensor was present but rejected — say so
            return dict(value=None, tier=sources.MODELED, source=None, ts=None, note=r.note,
                        quantity=q, valid=False)
    return None


def sensor_status(bid: str, now: datetime) -> dict:
    deployed, pending = [], []
    for q in (sources.Q_STAGE, sources.Q_SOIL, sources.Q_RAIN_1H, sources.Q_RAIN_STORM) + tuple(sources.ENV_QUANTITIES):
        r = sources.resolve(q, bid, None, now=now)
        (deployed if r.tier == sources.MEASURED else pending).append(q)
    return dict(deployed=deployed, pending=pending)


# --------------------------------------------------------------------------------------
# Alarms → mode
# --------------------------------------------------------------------------------------
def wetness_trend(state: dict, basins_out: dict, now: datetime) -> Optional[dict]:
    """Keep a 10-day history of campus wetness in the state file; ring when it has been
    rising for WET_TREND_DAYS at >= WET_TREND_MIN/day and sits above WET_TREND_FLOOR.
    Helene's ground went from half-charged to saturated in the two days before the main
    rain; a system that only reads the level would not have seen it coming."""
    hist = state.setdefault("wetness_hist", [])
    w = ((basins_out.get("CC-WCU-2260") or {}).get("wetness") or {}).get("w")
    if w is not None:
        hist.append([_iso(now), w])
    cutoff = now - timedelta(days=10)
    hist[:] = [h for h in hist if datetime.fromisoformat(h[0].replace("Z", "+00:00")) >= cutoff][-400:]
    if len(hist) < 2:
        return None
    t0 = now - timedelta(days=WET_TREND_DAYS)
    old = [h for h in hist if datetime.fromisoformat(h[0].replace("Z", "+00:00")) <= t0]
    if not old:
        return None
    w_old, w_now = old[-1][1], hist[-1][1]
    rate = (w_now - w_old) / WET_TREND_DAYS
    ring = w_now >= WET_TREND_FLOOR and rate >= WET_TREND_MIN
    return dict(w_now=round(w_now, 3), w_then=round(w_old, 3), rate_per_day=round(rate, 3), ringing=ring)


def alarms_and_mode(floor: dict, ero: Optional[dict], basins_out: dict, trend: Optional[dict]) -> tuple[list, str]:
    alarms = []
    lvl = floor.get("level")
    if lvl in ("ELEVATED", "WATCH_PENDING"):
        alarms.append(dict(name="corridor", mode="STORM", detail=f"{lvl}: {floor.get('why','')}"))
    elif lvl == "ANALOG":
        alarms.append(dict(name="corridor", mode="ATTENTION", detail=f"ANALOG: {floor.get('why','')}"))
    if ero and ero.get("status") == "ok" and ero.get("max_dn", 0) > 0:
        d = ero["max_dn"]; day = ero["max_day"]
        lab = {1: "Marginal", 2: "Slight", 3: "Moderate", 4: "High"}[d]
        alarms.append(dict(name="wpc_ero", mode="STORM" if d >= 3 else "ATTENTION",
                           detail=f"WPC excessive-rainfall risk {lab} on day {day} (>= {ero['days'][day-1]['pct']} %)"))
    if trend and trend.get("ringing"):
        alarms.append(dict(name="wetness_trend", mode="ATTENTION",
                           detail=f"campus wetness {trend['w_then']} → {trend['w_now']} over {WET_TREND_DAYS} d (+{trend['rate_per_day']}/d)"))
    neg50 = [b for b, r in basins_out.items() if (r.get("margin_in") or {}).get("p50") is not None and r["margin_in"]["p50"] <= 0]
    neg90 = [b for b, r in basins_out.items() if (r.get("margin_in") or {}).get("p90") is not None and r["margin_in"]["p90"] <= 0]
    if neg50:
        alarms.append(dict(name="forecast_margin", mode="STORM", detail="forecast rain (p50) reaches the WATCH line: " + ", ".join(neg50)))
    elif neg90:
        alarms.append(dict(name="forecast_margin", mode="ATTENTION", detail="forecast rain (p90) reaches the WATCH line: " + ", ".join(neg90)))
    mode = "QUIET"
    for a in alarms:
        if MODES.index(a["mode"]) > MODES.index(mode):
            mode = a["mode"]
    return alarms, mode


# --------------------------------------------------------------------------------------
# Publish
# --------------------------------------------------------------------------------------
def build(now: Optional[datetime] = None, feed_dir: Path = Path("feed"),
          modeled_rows: Optional[dict] = None, floor: Optional[dict] = None,
          outlook: Optional[dict] = None, ero: Optional[dict] = None) -> dict:
    """Assemble the readout. Every external fetch is optional and labelled when absent."""
    now = now or _utcnow()
    notes = []
    if modeled_rows is None:
        try:
            import live_rainfall
            modeled_rows = live_rainfall.compute_from_response(live_rainfall.fetch_all(), now=now)
        except Exception as e:                        # noqa: BLE001
            modeled_rows = {}
            notes.append(f"live_rainfall unavailable ({type(e).__name__}); wetness falls to default")
    if outlook is None:
        try:
            outlook = json.loads((feed_dir / "outlook.json").read_text(encoding="utf-8"))
        except Exception as e:                        # noqa: BLE001
            outlook = {"status": f"unavailable ({type(e).__name__})"}
    if floor is None:
        floor = compute_floor(now, feed_dir, feed_dir / "readiness_state.json")
        notes += floor.pop("notes", [])
    if ero is None:
        try:
            import wpc_ero
            ero = wpc_ero.fetch(now)
        except Exception as e:                        # noqa: BLE001
            ero = {"status": f"unavailable ({type(e).__name__})"}
    if ero.get("status") != "ok":
        notes.append(f"WPC excessive-rainfall outlook: {ero.get('status')}")

    basins_out = {}
    for bid, b in B.BASINS.items():
        row = modeled_rows.get(bid) or {}
        wz = resolve_wetness(bid, row, now)
        trip = trip_inches(bid, wz["w"])
        fc = forecast_for(bid, outlook)
        rain50, rain90 = fc["qpf24_p50"], fc["qpf24_p90"]
        margin = None
        if trip and trip.get("WATCH") is not None:
            margin = dict(p50=round(trip["WATCH"] - rain50, 2) if rain50 is not None else None,
                          p90=round(trip["WATCH"] - rain90, 2) if rain90 is not None else None)
        stage = resolve_stage(bid, now)
        confirmed = bool(stage and stage.get("valid") and stage.get("tier") != sources.MODELED)
        basins_out[bid] = dict(
            name=b.get("name"), lead_min=b.get("tc_min"), role=b.get("role"),
            wetness=wz, trip_in=trip, forecast=fc, margin_in=margin,
            outlook_level=outlook_level(trip, rain50),
            ceiling=("WARNING/EMERGENCY via measured stage" if confirmed else CAP),
            stage=stage, sensors=sensor_status(bid, now))
    # alarms → mode (state file carries the wetness history)
    state_path = feed_dir / "readiness_state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:                                 # noqa: BLE001
        state = {}
    trend = wetness_trend(state, basins_out, now)
    alarms, mode = alarms_and_mode(floor, ero, basins_out, trend)
    prev_mode = state.get("mode")
    state["mode"] = mode
    state["mode_since"] = state.get("mode_since") if prev_mode == mode else _iso(now)
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=1), encoding="utf-8")
    except OSError:
        pass
    return dict(fetched_utc=_iso(now), status="ok", tier="readiness",
                mode=mode, mode_since=state["mode_since"], prev_mode=prev_mode, alarms=alarms,
                cadence=CADENCE[mode], ero=ero, wetness_trend=trend,
                ladder=LADDER, cap=CAP, floor=floor, basins=basins_out, notes=notes,
                rule="forecast evidence tops out at WATCH; only measured stage earns WARNING/EMERGENCY; "
                     "absent data renders as absent")


def publish(outdir: Path, now: Optional[datetime] = None) -> dict:
    now = now or _utcnow()
    path = Path(outdir) / "readiness.json"
    try:
        install_backends()
        out = build(now, feed_dir=Path(outdir))
    except Exception as e:                            # noqa: BLE001 — a present file saying why
        out = dict(fetched_utc=_iso(now), status=f"error: {type(e).__name__}: {e}",
                   tier="readiness", ladder=LADDER, cap=CAP)
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"readiness feed: {out['status']}"
          + (f" · floor {out['floor']['level']} · mode {out.get('mode')}" if out.get("floor") else "")
          + (" · " + "; ".join(a["detail"] for a in out.get("alarms", [])) if out.get("alarms") else ""))
    return out


if __name__ == "__main__":
    out = publish(Path(sys.argv[1] if len(sys.argv) > 1 else "feed"))
    if out.get("basins"):
        print(f"{'basin':14s} {'w':>5s} {'src':9s} {'toWATCH':>8s} {'qpf24p50':>9s} {'margin':>7s}  outlook  ceiling")
        for bid, r in out["basins"].items():
            t = (r["trip_in"] or {}).get("WATCH")
            m = (r["margin_in"] or {}).get("p50")
            print(f"{bid:14s} {r['wetness']['w']:5.2f} {sources.badge(r['wetness']['tier']):9s} "
                  f"{('%.2f' % t) if t is not None else '  n/a':>8s} "
                  f"{('%.2f' % r['forecast']['qpf24_p50']) if r['forecast']['qpf24_p50'] is not None else '  n/a':>9s} "
                  f"{('%+.2f' % m) if m is not None else '  n/a':>7s}  {r['outlook_level']:8s} {r['ceiling']}")
