"""
test_gov_gauges.py — pure-logic tests for gov_gauges.py. NO network: every test
feeds a synthetic JSON payload shaped like the real USGS / Synoptic response and
checks the trailing totals, QC, geometry, and integration helpers.

Run:  python test_gov_gauges.py
"""

import datetime
import gov_gauges as gg

FAILS = []


def check(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILS.append(name)


def dt(h, day=19):
    return datetime.datetime(2026, 7, day, h, 0, 0)


# ---------------------------------------------------------------------------
def test_trailing_totals():
    print("trailing_totals")
    events = [(dt(12), 0.5), (dt(11), 0.3), (dt(10), 0.2), (dt(9), 0.1),
              (dt(0), 1.0)]                       # 00:00 same day -> inside 24h
    t = gg.trailing_totals(events)
    check("h1 = latest hour only (0.5)", t["h1"] == 0.5)
    check("h3 = 1.0", t["h3"] == 1.0)
    check("h6 = 1.1", t["h6"] == 1.1)
    check("h24 = 2.1", t["h24"] == 2.1)
    check("peak_hourly = 1.0", t["peak_hourly"] == 1.0)
    check("latest is 12:00", t["latest"] == dt(12))
    check("empty -> None", gg.trailing_totals([]) is None)
    # non-numeric / None increments are dropped, not crashed on
    t2 = gg.trailing_totals([(dt(12), None), (dt(11), "x"), (dt(10), 0.4)])
    check("junk increments dropped", t2 is not None and t2["h6"] == 0.4)


def test_usgs_compute():
    print("usgs_iv_compute")
    obj = {"value": {"timeSeries": [{
        "sourceInfo": {
            "siteName": "RAINGAGE AT FRANKLIN, NC",
            "siteCode": [{"value": "351205083213545"}],
            "geoLocation": {"geogLocation": {"latitude": 35.2014,
                                             "longitude": -83.3597}}},
        "variable": {"variableCode": [{"value": "00045"}],
                     "unit": {"unitCode": "in"}},
        "values": [{"value": [
            {"value": "0.20", "dateTime": "2026-07-19T12:00:00.000-04:00"},
            {"value": "0.10", "dateTime": "2026-07-19T11:30:00.000-04:00"},
            {"value": "-999999", "dateTime": "2026-07-19T11:00:00.000-04:00"},
        ]}]}]}}
    out = gg.usgs_iv_compute(obj)
    sid = "351205083213545"
    check("site parsed", sid in out)
    check("name kept", out[sid]["name"].startswith("RAINGAGE"))
    check("coords kept", out[sid]["lat"] == 35.2014)
    check("sentinel -999999 dropped, h1 = 0.30", out[sid]["h1"] == 0.30)
    # a non-precip timeseries must be ignored
    obj2 = {"value": {"timeSeries": [{
        "sourceInfo": {"siteName": "x", "siteCode": [{"value": "1"}],
                       "geoLocation": {"geogLocation": {}}},
        "variable": {"variableCode": [{"value": "00060"}]},   # discharge, not rain
        "values": [{"value": [{"value": "5", "dateTime": "2026-07-19T12:00:00Z"}]}]}]}}
    check("non-precip param ignored", gg.usgs_iv_compute(obj2) == {})


def test_synoptic_compute():
    print("synoptic_compute")
    obj = {"STATION": [{
        "STID": "HDSN7", "NAME": "HIGHLANDS 1NW",
        "LATITUDE": "35.0500", "LONGITUDE": "-83.2000",
        "OBSERVATIONS": {
            "date_time": ["2026-07-19T10:00:00Z", "2026-07-19T11:00:00Z",
                          "2026-07-19T12:00:00Z"],
            "precip_accum_one_hour_set_1": [0.2, 0.3, 0.5]}}]}
    out = gg.synoptic_compute(obj)
    check("station parsed", "HDSN7" in out)
    check("lat coerced to float", out["HDSN7"]["lat"] == 35.05)
    check("h1 = 0.5", out["HDSN7"]["h1"] == 0.5)
    check("h3 = 1.0", out["HDSN7"]["h3"] == 1.0)
    # variable-name flexibility: a differently-suffixed precip key still found
    obj2 = {"STATION": [{"STID": "FNKN7", "NAME": "FRANKLIN 1N",
            "LATITUDE": "35.2", "LONGITUDE": "-83.36",
            "OBSERVATIONS": {"date_time": ["2026-07-19T12:00:00Z"],
                             "precip_accum_one_hour_set_1d": [0.4]}}]}
    out2 = gg.synoptic_compute(obj2)
    check("alt precip key found", out2.get("FNKN7", {}).get("h1") == 0.4)


def test_qc():
    print("qc_flags")
    now = dt(12)
    clean = gg.trailing_totals([(dt(12), 0.5), (dt(11), 0.3)])
    check("clean gauge passes", gg.qc_flags(clean, now=now) == [])
    hot = gg.trailing_totals([(dt(12), 6.0)])          # 6 in in an hour
    check("impossible rate rejected", "impossible-rate" in gg.qc_flags(hot, now=now))
    neg = gg.trailing_totals([(dt(12), -0.5)])
    check("negative rejected", any(r.startswith("negative")
                                   for r in gg.qc_flags(neg, now=now)))
    stale = gg.trailing_totals([(dt(2), 0.2)])          # 10 h before now
    check("stale rejected", any(r.startswith("stale")
                                for r in gg.qc_flags(stale, now=now)))
    check("no-data -> reject", gg.qc_flags(None) == ["no-data"])


def test_geometry():
    print("geometry / direction")
    # Highlands ~ due south of the watershed center
    b_s = gg._bearing(gg.WATERSHED_CENTER, (35.05, -83.20))
    check("Highlands reads S", gg._dir8(b_s) == "S")
    # Franklin to the southwest
    b_sw = gg._bearing(gg.WATERSHED_CENTER, (35.2014, -83.3597))
    check("Franklin reads SW", gg._dir8(b_sw) == "SW")
    check("distance positive", gg._haversine_km(gg.WATERSHED_CENTER,
                                                (35.2014, -83.3597)) > 0)


def test_time_parsing():
    print("iso parsing")
    a = gg._parse_iso_utc("2026-07-19T12:00:00Z")
    b = gg._parse_iso_utc("2026-07-19T08:00:00.000-04:00")   # = 12:00 UTC
    check("Z and offset normalise equal", a == b == dt(12))
    check("bad string -> None", gg._parse_iso_utc("not-a-date") is None)


def test_integration_helpers():
    print("measured_upwind + qpf_bias")
    rows = [
        {"area": "Highlands 1NW", "dir": "S", "dist_km": 25, "qc": "ok", "h24": 3.0},
        {"area": "Franklin",      "dir": "SW", "dist_km": 30, "qc": "ok", "h24": 2.0},
        {"area": "Bad SW gauge",  "dir": "SW", "dist_km": 10,
         "qc": "reject:stale-9h", "h24": 9.9},          # closer but rejected
    ]
    mu = gg.measured_upwind(rows)
    check("S selected", mu["S"]["area"] == "Highlands 1NW")
    check("rejected gauge NOT chosen even though closer", mu["SW"]["area"] == "Franklin")

    bias = gg.qpf_bias(rows, model_by_dir={"S": 1.0, "SW": 2.0}, window="h24")
    check("S ratio = 3.0 (under-call)", bias["S"]["ratio"] == 3.0)
    check("S flagged under-calling", "UNDER" in bias["S"]["note"].upper())
    check("SW ratio = 1.0 on target", bias["SW"]["ratio"] == 1.0)
    # measured below threshold -> ratio undefined, no divide-by-zero
    bias2 = gg.qpf_bias(
        [{"area": "x", "dir": "S", "dist_km": 5, "qc": "ok", "h24": 0.0}],
        model_by_dir={"S": 0.0}, window="h24")
    check("no rain -> ratio None", bias2["S"]["ratio"] is None)


if __name__ == "__main__":
    for fn in (test_trailing_totals, test_usgs_compute, test_synoptic_compute,
               test_qc, test_geometry, test_time_parsing,
               test_integration_helpers):
        fn()
    print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
    raise SystemExit(1 if FAILS else 0)
