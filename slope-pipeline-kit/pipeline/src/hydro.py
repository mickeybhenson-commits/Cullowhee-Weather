"""Hydrologic conditions for the watershed: rainfall, soil moisture, stream flow.

Two interchangeable sources feed the same metrics:
  * PUBLIC FEEDS (default, no accounts): Open-Meteo model precipitation and
    soil moisture for the watershed centroid, plus the nearest active USGS
    stream gauge. Good enough to exercise the fusion logic today.
  * FIRESTORE (your sensors, once deployed): set hydro.firestore.enabled: true
    in config.yaml. Schema documented there.

The output is a HydroConditions object with a state ladder:
  DRY -> NORMAL -> ELEVATED -> PRIMED -> PRIMED_SEVERE
based on WNC-appropriate rainfall intensity-duration + antecedent wetness.

    python -m src.hydro            # print current conditions (live feeds)
    python -m src.hydro selftest   # verify the logic on canned scenarios
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .config import Config

STATES = ["DRY", "NORMAL", "ELEVATED", "PRIMED", "PRIMED_SEVERE"]


@dataclass
class HydroConditions:
    rain_24h_mm: float
    rain_72h_mm: float
    api_mm: float                      # antecedent precipitation index
    soil_saturation: float | None      # 0..1, None if unavailable
    forecast_48h_mm: float
    stream_note: str = ""
    source: str = "public-feeds"
    state: str = "NORMAL"
    reasons: list[str] = field(default_factory=list)

    def as_dict(self):
        return {
            "state": self.state,
            "rain_24h_mm": round(self.rain_24h_mm, 1),
            "rain_72h_mm": round(self.rain_72h_mm, 1),
            "antecedent_api_mm": round(self.api_mm, 1),
            "soil_saturation": None if self.soil_saturation is None else round(self.soil_saturation, 2),
            "forecast_48h_mm": round(self.forecast_48h_mm, 1),
            "stream": self.stream_note,
            "source": self.source,
            "reasons": self.reasons,
        }


# --------------------------------------------------------------------------
# Metric computation + state ladder (source-independent — this is the logic
# your Firestore data will flow through unchanged)
# --------------------------------------------------------------------------

def antecedent_api(daily_mm: list[float], decay: float) -> float:
    """API = sum of daily rain, each day discounted by decay^age. Oldest first."""
    api = 0.0
    for mm in daily_mm:
        api = api * decay + mm
    return api


def classify(cond: HydroConditions, th: dict) -> HydroConditions:
    wet_antecedent = cond.api_mm >= th["antecedent_api_mm"] or (
        cond.soil_saturation is not None and cond.soil_saturation >= th["soil_sat_primed"]
    )
    r = []
    if cond.rain_24h_mm >= th["rain24_severe_mm"]:
        cond.state = "PRIMED_SEVERE"
        r.append(f"{cond.rain_24h_mm:.0f} mm rain in 24 h exceeds the debris-flow "
                 f"threshold ({th['rain24_severe_mm']:.0f} mm)")
    elif cond.rain_24h_mm >= th["rain24_primed_mm"] and wet_antecedent:
        cond.state = "PRIMED"
        r.append(f"{cond.rain_24h_mm:.0f} mm rain in 24 h on wet antecedent ground")
    elif cond.forecast_48h_mm >= th["forecast48_elevated_mm"] and wet_antecedent:
        cond.state = "ELEVATED"
        r.append(f"{cond.forecast_48h_mm:.0f} mm forecast in 48 h onto wet ground")
    elif cond.rain_24h_mm >= th["rain24_primed_mm"]:
        cond.state = "ELEVATED"
        r.append(f"{cond.rain_24h_mm:.0f} mm rain in 24 h (ground not saturated)")
    elif cond.rain_72h_mm < 2 and cond.api_mm < 15:
        cond.state = "DRY"
        r.append("no meaningful rain in 72 h, dry antecedent conditions")
    else:
        cond.state = "NORMAL"
        r.append("rainfall and wetness within normal range")
    if wet_antecedent and cond.state in ("NORMAL", "ELEVATED"):
        r.append(f"antecedent wetness is high (API {cond.api_mm:.0f} mm"
                 + (f", saturation {cond.soil_saturation:.0%}" if cond.soil_saturation is not None else "")
                 + ")")
    cond.reasons = r
    return cond


# --------------------------------------------------------------------------
# Source: public feeds (Open-Meteo + USGS)
# --------------------------------------------------------------------------

def from_public_feeds(cfg: Config) -> HydroConditions:
    import requests

    h = cfg["hydro"]
    r = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": h["center_lat"], "longitude": h["center_lon"],
            "hourly": "precipitation,soil_moisture_9_to_27cm",
            "past_days": 7, "forecast_days": 2, "timezone": "UTC",
        },
        timeout=30,
    )
    r.raise_for_status()
    j = r.json()["hourly"]
    times = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in j["time"]]
    precip = [p or 0.0 for p in j["precipitation"]]
    soil = j.get("soil_moisture_9_to_27cm") or []

    now = datetime.now(timezone.utc)
    past = [(t, p) for t, p in zip(times, precip) if t <= now]
    future = [(t, p) for t, p in zip(times, precip) if t > now]

    rain24 = sum(p for t, p in past if t >= now - timedelta(hours=24))
    rain72 = sum(p for t, p in past if t >= now - timedelta(hours=72))
    fc48 = sum(p for t, p in future if t <= now + timedelta(hours=48))

    # daily totals for the API (oldest first)
    daily: dict[str, float] = {}
    for t, p in past:
        daily[t.date().isoformat()] = daily.get(t.date().isoformat(), 0.0) + p
    api = antecedent_api([daily[k] for k in sorted(daily)], h["api_decay_per_day"])

    sat = None
    past_soil = [s for t, s in zip(times, soil) if s is not None and t <= now]
    if past_soil:
        sat = min(1.0, past_soil[-1] / h["porosity"])

    cond = HydroConditions(
        rain_24h_mm=rain24, rain_72h_mm=rain72, api_mm=api,
        soil_saturation=sat, forecast_48h_mm=fc48,
        stream_note=_usgs_note(h), source="public-feeds (Open-Meteo model + USGS)",
    )
    return classify(cond, h["thresholds"])


def _usgs_note(h) -> str:
    try:
        import requests

        b = h["usgs_bbox"]
        r = requests.get(
            "https://waterservices.usgs.gov/nwis/iv/",
            params={"format": "json", "bBox": ",".join(str(x) for x in b),
                    "parameterCd": "00060", "siteStatus": "active"},
            timeout=30,
        )
        r.raise_for_status()
        ts = r.json()["value"]["timeSeries"]
        if not ts:
            return "no active USGS gauge in search box"
        s = ts[0]
        name = s["sourceInfo"]["siteName"]
        val = s["values"][0]["value"][-1]["value"]
        return f"{name}: {val} cfs"
    except Exception as e:  # stream note is best-effort, never fatal
        return f"USGS gauge unavailable ({type(e).__name__})"


# --------------------------------------------------------------------------
# Source: your Firestore sensors (activates when hydro.firestore.enabled)
# --------------------------------------------------------------------------

def from_firestore(cfg: Config) -> HydroConditions:
    from google.cloud import firestore  # pip install google-cloud-firestore

    h = cfg["hydro"]
    fs = h["firestore"]
    client = firestore.Client(project=fs["project"] or None)
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=7)
    docs = (
        client.collection(fs["collection"])
        .where("timestamp", ">=", since)
        .stream()
    )
    rain, soil = [], []
    for d in docs:
        v = d.to_dict()
        if v.get("sensor_type") == "rain":
            rain.append((v["timestamp"], float(v["value"])))
        elif v.get("sensor_type") == "soil_moisture":
            soil.append((v["timestamp"], float(v["value"])))
    rain.sort()
    soil.sort()

    rain24 = sum(v for t, v in rain if t >= now - timedelta(hours=24))
    rain72 = sum(v for t, v in rain if t >= now - timedelta(hours=72))
    daily: dict[str, float] = {}
    for t, v in rain:
        daily[t.date().isoformat()] = daily.get(t.date().isoformat(), 0.0) + v
    api = antecedent_api([daily[k] for k in sorted(daily)], h["api_decay_per_day"])
    sat = min(1.0, soil[-1][1] / h["porosity"]) if soil else None

    cond = HydroConditions(
        rain_24h_mm=rain24, rain_72h_mm=rain72, api_mm=api,
        soil_saturation=sat, forecast_48h_mm=0.0,   # forecast still from NWS/Open-Meteo
        stream_note="from Firestore sensors", source="firestore",
    )
    return classify(cond, h["thresholds"])


def get_conditions(cfg: Config) -> HydroConditions:
    if cfg["hydro"]["firestore"]["enabled"]:
        return from_firestore(cfg)
    return from_public_feeds(cfg)


# --------------------------------------------------------------------------

def selftest():
    th = Config.load()["hydro"]["thresholds"]
    cases = [
        ("dry spell", HydroConditions(0, 1, 5, 0.3, 0), "DRY"),
        ("ordinary week", HydroConditions(8, 30, 35, 0.6, 5), "NORMAL"),
        ("big storm, dry ground", HydroConditions(80, 85, 40, 0.5, 0), "ELEVATED"),
        ("storm forecast onto saturated ground", HydroConditions(10, 60, 70, 0.9, 60), "ELEVATED"),
        ("3in storm on saturated ground", HydroConditions(80, 140, 90, 0.9, 10), "PRIMED"),
        ("helene-scale rain", HydroConditions(180, 250, 120, 0.95, 40), "PRIMED_SEVERE"),
    ]
    ok = True
    for name, cond, expect in cases:
        got = classify(cond, th).state
        mark = "PASS" if got == expect else "FAIL"
        ok &= got == expect
        print(f"  [{mark}] {name}: {got} (expected {expect}) — {cond.reasons[0]}")
    print("selftest", "PASSED" if ok else "FAILED")
    return ok


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        raise SystemExit(0 if selftest() else 1)
    cfg = Config.load()
    cond = get_conditions(cfg)
    print(f"\nHydrologic conditions — Cullowhee Creek Watershed [{cond.source}]")
    print(f"  state: {cond.state}")
    for k, v in cond.as_dict().items():
        if k not in ("state", "reasons", "source"):
            print(f"  {k}: {v}")
    for r in cond.reasons:
        print(f"  - {r}")


if __name__ == "__main__":
    main()
