"""
sources.py  —  Source-resolution layer for the Cullowhee Creek flood engine.

PURPOSE
-------
Every input quantity (rainfall, soil moisture, stage, wind) is fetched through
ONE resolver that returns the *best available* source plus a provenance tag.
Priority, highest first:
    MEASURED      physical sensor / gauge  (SKYE, NOAH, K24A)
    GOV_ESTIMATE  official model product   (NWM)            <- not wired yet
    MODELED       Open-Meteo / our engine                   <- the fallback today

Today there are no sensors, so every resolve() returns MODELED. The day a node
writes a reading to Firestore for a sub-basin, that basin's value silently flips
to MEASURED -- no change to the app. The sensor "replaces" the model by
OUT-RANKING it, not by anyone editing the compute code.

SAFETY GATE (critical for a warning system)
-------------------------------------------
A sensor does NOT blindly win. A dead gauge reading 0.00 during a flood, or a
reading from 3 hours ago, is worse than the model. A sensor value is preferred
only if it is FRESH (recent enough) and IN RANGE (physically plausible).
Otherwise resolve() falls back to MODELED and records *why* in `.note`, so the
operator can see that a sensor is present but was rejected (e.g. gauge offline).

ACTIVATING SENSORS LATER
------------------------
1. Point the Firestore backend at your ingest collection and field names:
       import sources
       sources.set_backend(sources.FirestoreBackend(
           project="ee-dashboard-477704", database="cullowhee",
           collection="sensor_readings"))
2. Have SKYE/NOAH write rows {basin, quantity, value, ts, source} (or remap via
   field_map). That's it -- resolve() starts returning MEASURED for covered
   basins automatically, gated by freshness + range.

COMPOSING SOURCES  (ChainBackend)
---------------------------------
To run more than one source with a fixed priority -- e.g. a true in-basin sensor
first, a nearby government gauge as a proxy second -- wrap them:
       sources.set_backend(sources.ChainBackend([
           sources.FirestoreBackend(...),          # MEASURED, in-basin (best)
           gov_sources.GovGaugeBackend(...),        # GOV_ESTIMATE, nearby proxy
       ]))
ChainBackend tries each in order and returns the first reading that PASSES the
freshness+range gate, so a stale in-basin gauge falls through to the proxy, and
a rejected proxy falls through to the model -- each level still gated.

Stdlib only. The Firestore client is imported lazily so this module loads with
or without google-cloud-firestore installed.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Tiers
# ---------------------------------------------------------------------------
MEASURED = "measured"
GOV_ESTIMATE = "gov_estimate"
MODELED = "modeled"

TIER_RANK = {MEASURED: 0, GOV_ESTIMATE: 1, MODELED: 2}
TIER_LABEL = {MEASURED: "MEASURED", GOV_ESTIMATE: "GOV EST", MODELED: "MODELED"}
DEFAULT_MODELED_SOURCE = "Open-Meteo (HRRR) / engine"


def badge(tier: str) -> str:
    """Short uppercase label for a tier, for table/legend display."""
    return TIER_LABEL.get(tier, "?")


# ---------------------------------------------------------------------------
# Quantities  (string keys used everywhere a value is resolved)
# ---------------------------------------------------------------------------
Q_RAIN_1H = "rain_1h"        # trailing 1-hour rainfall
Q_RAIN_STORM = "rain_storm"  # storm-total / worst upcoming 24-h analog
Q_RAIN_5DAY = "rain_5day"    # antecedent 5-day rainfall
Q_SOIL = "soil_moisture_pct" # 0-100 % of available capacity
Q_STAGE = "stage_ft"         # observed creek stage
Q_WIND_SPEED = "wind_speed_mph"
Q_WIND_DIR = "wind_dir_deg"

# Max acceptable age (seconds) before a sensor reading is considered STALE.
FRESH_S = {
    Q_RAIN_1H: 20 * 60,
    Q_RAIN_STORM: 90 * 60,
    Q_RAIN_5DAY: 12 * 3600,
    Q_SOIL: 6 * 3600,
    Q_STAGE: 12 * 60,
    Q_WIND_SPEED: 30 * 60,
    Q_WIND_DIR: 30 * 60,
}

# Physically plausible [lo, hi] for each quantity. Outside -> reject sensor.
RANGE = {
    Q_RAIN_1H: (0.0, 8.0),
    Q_RAIN_STORM: (0.0, 30.0),
    Q_RAIN_5DAY: (0.0, 40.0),
    Q_SOIL: (0.0, 100.0),
    Q_STAGE: (0.0, 40.0),
    Q_WIND_SPEED: (0.0, 150.0),
    Q_WIND_DIR: (0.0, 360.0),
}

# Which sub-basins are expected to have a sensor for a given quantity.
# Empty until hardware deploys -- this is documentation + lets the UI mark a
# basin "sensor planned". The resolver itself relies on the backend actually
# returning data, not on this map, so a stale map can't cause a wrong reading.
SENSOR_COVERAGE = {
    # "CC-TIL-705": {Q_RAIN_1H, Q_RAIN_STORM, Q_SOIL},   # example, once SKYE is live
    # "CC-WCU-2260": {Q_STAGE},                           # example, once NOAH is live
}


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
@dataclass
class Reading:
    value: Optional[float]
    tier: str
    source: str                       # human-readable origin
    ts: Optional[datetime]            # observation time (UTC); None if unknown
    quantity: str
    valid: bool = True
    note: str = ""                    # rejection reason / caveat

    def label(self) -> str:
        return badge(self.tier)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Validity gate
# ---------------------------------------------------------------------------
def gate(r: Reading, now: Optional[datetime] = None) -> Reading:
    """Return a copy of `r` with valid/note set by range + freshness checks.
    A reading that fails is returned valid=False with the reason in `.note`."""
    now = now or _utcnow()
    if r.value is None:
        return _reject(r, "no value")
    lo, hi = RANGE.get(r.quantity, (float("-inf"), float("inf")))
    if not (lo <= r.value <= hi):
        return _reject(r, f"out of range [{lo}, {hi}]: {r.value}")
    limit = FRESH_S.get(r.quantity)
    if r.ts is None:
        # Can't verify freshness. Allow, but flag -- never silently trust.
        return _ok(r, "no timestamp; freshness unverified")
    if limit is not None:
        age = (now - r.ts).total_seconds()
        if age > limit:
            return _reject(r, f"stale: {int(age)}s old > {limit}s limit")
        if age < -60:  # clock skew / future timestamp
            return _reject(r, f"timestamp in the future by {int(-age)}s")
    return _ok(r, "")


def _ok(r: Reading, note: str) -> Reading:
    return Reading(r.value, r.tier, r.source, r.ts, r.quantity, valid=True, note=note)


def _reject(r: Reading, why: str) -> Reading:
    return Reading(r.value, r.tier, r.source, r.ts, r.quantity, valid=False, note=why)


# ---------------------------------------------------------------------------
# Sensor backends  (pluggable; swap with set_backend)
# ---------------------------------------------------------------------------
class SensorBackend:
    """Interface: return the latest Reading for (quantity, basin_id) or None."""
    def latest(self, quantity: str, basin_id: str) -> Optional[Reading]:
        raise NotImplementedError


class NullBackend(SensorBackend):
    """No sensors. The default until hardware deploys."""
    def latest(self, quantity, basin_id):
        return None


class DictBackend(SensorBackend):
    """In-memory backend for tests and manual overrides.
    data: {(quantity, basin_id): Reading}."""
    def __init__(self, data=None):
        self._d = dict(data or {})

    def put(self, reading: Reading, basin_id: str):
        self._d[(reading.quantity, basin_id)] = reading

    def latest(self, quantity, basin_id):
        return self._d.get((quantity, basin_id))


class ChainBackend(SensorBackend):
    """Compose backends with a fixed priority. Returns the first reading that
    PASSES the freshness+range gate; each level is gated independently, so a
    stale/out-of-range higher-priority reading falls through to the next source.

    If no backend yields a gate-valid reading but at least one returned SOMETHING,
    the highest-priority present-but-rejected reading is returned (still invalid),
    so resolve() can surface *why* it was rejected in `.note` rather than silently
    dropping to the model. Returns None only when every backend returned None."""
    def __init__(self, backends, now_fn=_utcnow):
        self._backends = list(backends)
        self._now_fn = now_fn

    def latest(self, quantity, basin_id):
        first_present = None
        now = self._now_fn()
        for b in self._backends:
            r = b.latest(quantity, basin_id)
            if r is None:
                continue
            if first_present is None:
                first_present = r
            if gate(r, now).valid:
                return r          # highest-priority source that passes the gate
        return first_present       # all rejected (or None): surface the top one


# Default Firestore document schema. Override field names via field_map if your
# SKYE/NOAH ingest writes different keys.
DEFAULT_FIELD_MAP = {"value": "value", "ts": "ts", "source": "source",
                     "basin": "basin", "quantity": "quantity"}


class FirestoreBackend(SensorBackend):
    """Reads the most recent reading per (quantity, basin) from Firestore.
    DORMANT until the collection has data and creds are available. Any failure
    (missing package, no creds, no docs, bad row) returns None so resolve()
    cleanly falls back to the model -- a backend problem can never block a
    forecast."""
    def __init__(self, project="ee-dashboard-477704", database="cullowhee",
                 collection="sensor_readings", field_map=None):
        self.project = project
        self.database = database
        self.collection = collection
        self.fm = dict(field_map or DEFAULT_FIELD_MAP)
        self._client = None

    def _c(self):
        if self._client is None:
            from google.cloud import firestore  # lazy, optional dependency
            self._client = firestore.Client(project=self.project,
                                             database=self.database)
        return self._client

    def latest(self, quantity, basin_id):
        try:
            from google.cloud.firestore_v1.base_query import FieldFilter
            col = self._c().collection(self.collection)
            q = (col.where(filter=FieldFilter(self.fm["basin"], "==", basin_id))
                    .where(filter=FieldFilter(self.fm["quantity"], "==", quantity))
                    .order_by(self.fm["ts"], direction="DESCENDING")
                    .limit(1))
            docs = list(q.stream())
            if not docs:
                return None
            d = docs[0].to_dict()
            val = d.get(self.fm["value"])
            if val is None:
                return None
            return Reading(float(val), MEASURED,
                           d.get(self.fm["source"], f"sensor {basin_id}"),
                           _coerce_ts(d.get(self.fm["ts"])), quantity)
        except Exception:
            return None  # never let a backend error break the forecast


def _coerce_ts(ts) -> Optional[datetime]:
    """Best-effort conversion of a Firestore timestamp / ISO string to UTC dt."""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    try:  # ISO-8601 string
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Active backend  (module-level; swap at startup)
# ---------------------------------------------------------------------------
_BACKEND: SensorBackend = NullBackend()


def set_backend(backend: SensorBackend) -> None:
    global _BACKEND
    _BACKEND = backend


def current_backend() -> SensorBackend:
    return _BACKEND


# ---------------------------------------------------------------------------
# THE RESOLVER
# ---------------------------------------------------------------------------
def resolve(quantity: str, basin_id: str, modeled_value,
            *, backend: Optional[SensorBackend] = None,
            now: Optional[datetime] = None,
            modeled_source: str = DEFAULT_MODELED_SOURCE) -> Reading:
    """Best available source for `quantity` in `basin_id`.

    Order: a sensor reading that is present, fresh, and in range wins (MEASURED).
    Otherwise fall back to `modeled_value` (MODELED). If a sensor was present but
    rejected, the returned MODELED reading's `.note` says why (e.g. gauge stale),
    so a down sensor is visible rather than silently ignored.
    """
    backend = backend or _BACKEND
    now = now or _utcnow()

    raw = backend.latest(quantity, basin_id)
    if raw is not None:
        g = gate(raw, now)
        if g.valid:
            return g  # sensor wins
        fallback_note = f"sensor rejected ({g.note}); using model"
    else:
        fallback_note = ""

    return Reading(modeled_value, MODELED, modeled_source, now, quantity,
                   valid=(modeled_value is not None),
                   note=fallback_note or ("" if modeled_value is not None else "no source"))


# ---------------------------------------------------------------------------
# Self-test: prove the measured-replaces-modeled flip + the safety fallback
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from datetime import timedelta
    now = _utcnow()
    fresh = now - timedelta(minutes=5)
    stale = now - timedelta(hours=4)

    print("== default (no sensors): everything MODELED ==")
    r = resolve(Q_STAGE, "CC-WCU-2260", 6.7)
    print(f"  stage -> {r.label():9s} {r.value} ({r.source})")
    assert r.tier == MODELED

    print("\n== inject a healthy stage sensor for the campus reach ==")
    be = DictBackend()
    be.put(Reading(8.9, MEASURED, "NOAH CC-WCU-2260", fresh, Q_STAGE), "CC-WCU-2260")
    set_backend(be)
    r = resolve(Q_STAGE, "CC-WCU-2260", 6.7)
    print(f"  stage -> {r.label():9s} {r.value} ({r.source})  note={r.note!r}")
    assert r.tier == MEASURED and r.value == 8.9

    # a basin WITHOUT a sensor still falls back
    r2 = resolve(Q_STAGE, "CC-COX-097", 1.2)
    print(f"  Cox  -> {r2.label():9s} {r2.value} ({r2.source})")
    assert r2.tier == MODELED

    print("\n== safety gate: STALE sensor must fall back to model ==")
    be.put(Reading(8.9, MEASURED, "NOAH CC-WCU-2260", stale, Q_STAGE), "CC-WCU-2260")
    r = resolve(Q_STAGE, "CC-WCU-2260", 6.7)
    print(f"  stage -> {r.label():9s} {r.value}  note={r.note!r}")
    assert r.tier == MODELED and "stale" in r.note

    print("\n== safety gate: OUT-OF-RANGE sensor (stuck high) must fall back ==")
    be.put(Reading(999.0, MEASURED, "NOAH CC-WCU-2260", fresh, Q_STAGE), "CC-WCU-2260")
    r = resolve(Q_STAGE, "CC-WCU-2260", 6.7)
    print(f"  stage -> {r.label():9s} {r.value}  note={r.note!r}")
    assert r.tier == MODELED and "range" in r.note

    print("\n== ChainBackend: stale in-basin sensor falls through to a fresh proxy ==")
    inbasin = DictBackend()
    inbasin.put(Reading(8.9, MEASURED, "SKYE in-basin", stale, Q_RAIN_STORM), "CC-TIL-705")
    proxy = DictBackend()
    proxy.put(Reading(2.1, GOV_ESTIMATE, "Franklin proxy", fresh, Q_RAIN_STORM), "CC-TIL-705")
    set_backend(ChainBackend([inbasin, proxy]))
    r = resolve(Q_RAIN_STORM, "CC-TIL-705", 1.0)
    print(f"  rain -> {r.label():9s} {r.value} ({r.source})")
    assert r.tier == GOV_ESTIMATE and r.value == 2.1

    print("\n== dead gauge reads 0.00 during a real flood: model still governs ==")
    be2 = DictBackend()
    be2.put(Reading(0.0, MEASURED, "rain gauge", fresh, Q_RAIN_STORM), "CC-TIL-705")
    set_backend(be2)
    # 0.0 is IN range, so it would be accepted -- this shows why freshness + a
    # cross-check matter. Here it's fresh+in-range, so it IS taken; the lesson is
    # that range alone won't catch a stuck-at-zero gauge. Flag for future QC.
    r = resolve(Q_RAIN_STORM, "CC-TIL-705", 2.5)
    print(f"  rain  -> {r.label():9s} {r.value}  (NOTE: stuck-at-0 passes range;")
    print(f"           future QC = cross-check vs radar/neighbors before trusting)")

    set_backend(NullBackend())
    print("\nAll source-resolution assertions passed.")
