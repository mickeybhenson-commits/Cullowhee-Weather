"""
test_gov_sources.py — the bias-correction safety property + the in-basin sensor
seam. No network: the backend is fed a synthetic fetcher.

Key safety assertion: the storm correction is UPWARD-ONLY — a gov gauge that
reads low can never scale a basin's storm rain down (never suppress a warning).

Run:  python test_gov_sources.py
"""

from datetime import timedelta

import sources
import gov_sources as gs

FAILS = []


def check(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILS.append(name)


# --------------------------------------------------------------------------
def test_correction_upward_only():
    print("storm_correction_map — upward-only")
    gauge_rows = [
        {"area": "Franklin", "station": "351205083213545", "dir": "SW",
         "dist_km": 30, "qc": "ok", "h24": 2.0},     # 2x the model
        {"area": "Highlands", "station": "HDSN7", "dir": "S",
         "dist_km": 25, "qc": "ok", "h24": 0.2},      # reads LOW
    ]
    modeled = [{"dir": "SW", "h24": 1.0}, {"dir": "S", "h24": 1.0}]
    inflow = {"CC-UP-503": "SW", "CC-TIL-705": "S", "CC-MS-1100": "NW"}
    corr = gs.storm_correction_map(inflow, gauge_rows, modeled)

    check("under-called SW basin scaled up 2x", corr["CC-UP-503"] == 2.0)
    check("low-reading S basin NEVER scaled down (=1.0)", corr["CC-TIL-705"] == 1.0)
    check("basin with no gauge on its dir -> 1.0", corr["CC-MS-1100"] == 1.0)
    check("empty inflow -> {}", gs.storm_correction_map({}, gauge_rows, modeled) == {})

    # cap respected
    hot = [{"area": "F", "station": "x", "dir": "SW", "dist_km": 30,
            "qc": "ok", "h24": 99.0}]
    capped = gs.storm_correction_map({"b": "SW"}, hot, [{"dir": "SW", "h24": 1.0}],
                                     cap=2.5)
    check("factor capped at 2.5", capped["b"] == 2.5)

    # a rejected gauge contributes no bias (qpf_bias ignores qc!=ok)
    bad = [{"area": "F", "station": "x", "dir": "SW", "dist_km": 30,
            "qc": "reject:stale-9h", "h24": 9.0}]
    r = gs.storm_correction_map({"b": "SW"}, bad, [{"dir": "SW", "h24": 1.0}])
    check("rejected gauge -> no scaling (1.0)", r["b"] == 1.0)


def _fetcher(rows):
    def f(token=None):
        return rows, {}
    return f


def _fresh_iso(minutes_ago):
    return (sources._utcnow() - timedelta(minutes=minutes_ago)) \
        .replace(tzinfo=None).isoformat()


def test_backend_and_resolve():
    print("GovGaugeBackend via sources.resolve")
    rows = [{"area": "In-basin gauge", "station": "SKYE1", "dir": "SW",
             "dist_km": 1, "qc": "ok", "h24": 3.4,
             "latest_iso": _fresh_iso(5)}]
    be = gs.GovGaugeBackend({"CC-TIL-705": "SKYE1"}, fetcher=_fetcher(rows))

    r = sources.resolve(sources.Q_RAIN_STORM, "CC-TIL-705", 1.0, backend=be)
    check("mapped basin -> GOV_ESTIMATE wins", r.tier == sources.GOV_ESTIMATE)
    check("value = gauge h24", r.value == 3.4)

    r2 = sources.resolve(sources.Q_RAIN_STORM, "CC-COX-097", 1.0, backend=be)
    check("unmapped basin -> MODELED", r2.tier == sources.MODELED)

    r3 = sources.resolve(sources.Q_SOIL, "CC-TIL-705", 50, backend=be)
    check("unserved quantity -> MODELED", r3.tier == sources.MODELED)

    # stale gauge must be gate-rejected -> model
    rows_stale = [dict(rows[0], latest_iso=_fresh_iso(200))]   # >90 min
    be_s = gs.GovGaugeBackend({"CC-TIL-705": "SKYE1"}, fetcher=_fetcher(rows_stale))
    r4 = sources.resolve(sources.Q_RAIN_STORM, "CC-TIL-705", 1.0, backend=be_s)
    check("stale gauge rejected -> MODELED", r4.tier == sources.MODELED
          and "stale" in r4.note)

    # qc-rejected row -> backend serves nothing
    rows_bad = [dict(rows[0], qc="reject:impossible-rate")]
    be_b = gs.GovGaugeBackend({"CC-TIL-705": "SKYE1"}, fetcher=_fetcher(rows_bad))
    r5 = sources.resolve(sources.Q_RAIN_STORM, "CC-TIL-705", 1.0, backend=be_b)
    check("qc-rejected gauge -> MODELED", r5.tier == sources.MODELED)

    # scale (orographic uplift) applied
    be_sc = gs.GovGaugeBackend({"CC-TIL-705": "SKYE1"}, fetcher=_fetcher(rows),
                               scale={"CC-TIL-705": 1.5})
    r6 = sources.resolve(sources.Q_RAIN_STORM, "CC-TIL-705", 1.0, backend=be_sc)
    check("scale factor applied (3.4*1.5=5.1)", r6.value == 5.1)


def test_cache_one_fetch():
    print("backend caches the arc fetch")
    calls = {"n": 0}
    rows = [{"area": "g", "station": "S1", "dir": "SW", "dist_km": 1,
             "qc": "ok", "h24": 2.0, "latest_iso": _fresh_iso(5)}]

    def counting(token=None):
        calls["n"] += 1
        return rows, {}

    be = gs.GovGaugeBackend({"b1": "S1", "b2": "S1"}, fetcher=counting)
    be.latest(sources.Q_RAIN_STORM, "b1")
    be.latest(sources.Q_RAIN_STORM, "b2")
    check("fetch happened once, then cached", calls["n"] == 1)


def test_install_chain_priority():
    print("install(): real sensor > gov proxy > model")
    rows = [{"area": "proxy", "station": "S1", "dir": "SW", "dist_km": 1,
             "qc": "ok", "h24": 2.0, "latest_iso": _fresh_iso(5)}]
    proxy = gs.GovGaugeBackend({"CC-TIL-705": "S1"}, fetcher=_fetcher(rows))
    real = sources.DictBackend()
    real.put(sources.Reading(4.0, sources.MEASURED, "SKYE in-basin",
                             sources._utcnow(), sources.Q_RAIN_STORM), "CC-TIL-705")
    sources.set_backend(sources.ChainBackend([real, proxy]))
    r = sources.resolve(sources.Q_RAIN_STORM, "CC-TIL-705", 1.0)
    check("true MEASURED sensor beats the proxy", r.tier == sources.MEASURED
          and r.value == 4.0)
    sources.set_backend(sources.NullBackend())


if __name__ == "__main__":
    for t in (test_correction_upward_only, test_backend_and_resolve,
              test_cache_one_fetch, test_install_chain_priority):
        t()
    print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
    raise SystemExit(1 if FAILS else 0)
