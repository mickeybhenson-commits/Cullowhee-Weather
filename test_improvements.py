"""
test_improvements.py - unit tests for the 2026-07 improvement set.

Covers §2 frequency classification, §3 PI band + ensemble, §4 lead time, and the
Helene back-test. Pure stdlib unittest; no numbers are asserted that are not
traceable to basins.py or the WCU ground truth.

  python -m unittest test_improvements -v
"""
import unittest

import basins
import cwm_model as cwm
from basins import BASINS, routed_order, LEAD_REQ_MIN
import flood_rating as fr
import lead_time as lt
import flood_ensemble as fe
import backtest_helene as bt

NON_CAMPUS = [b for b in routed_order() if b not in ("CC-WCU-2260", "CC-MOUTH-2340")]


class TestReturnPeriod(unittest.TestCase):
    def test_anchor_exactness(self):
        # a flow equal to a regression quantile returns that return period
        for bid in NON_CAMPUS:
            rq = BASINS[bid]["reg_q"]
            for aep, rp in basins.AEP_RP.items():
                got = fr.rp_from_q(rq[aep], rq)
                self.assertAlmostEqual(got, rp, places=6,
                    msg=f"{bid} AEP {aep}: expected RP {rp}, got {got}")

    def test_monotonic_in_flow(self):
        for bid in NON_CAMPUS:
            rq = BASINS[bid]["reg_q"]
            qs = [50, 100, 300, 600, 1200, 2500, 5000]
            rps = [fr.rp_from_q(q, rq) for q in qs]
            self.assertEqual(rps, sorted(rps), f"{bid} RP not monotonic in flow")

    def test_500yr_cap(self):
        rq = BASINS["CC-UP-503"]["reg_q"]
        self.assertEqual(fr.rp_from_q(rq[0.002] * 5, rq), 500)


class TestCategory(unittest.TestCase):
    def test_default_cutoffs(self):
        self.assertEqual(fr.category_from_rp(1.0), "NORMAL")
        self.assertEqual(fr.category_from_rp(2.0), "WATCH")
        self.assertEqual(fr.category_from_rp(9.9), "WATCH")
        self.assertEqual(fr.category_from_rp(10.0), "WARNING")
        self.assertEqual(fr.category_from_rp(99.9), "WARNING")
        self.assertEqual(fr.category_from_rp(100.0), "EMERGENCY")

    def test_flashy_1_5yr_watch(self):
        # Cox/LB drop WATCH to 1.5-yr; a generic reach stays WATCH>=2
        self.assertEqual(fr.category_from_rp(1.6, "CC-COX-097"), "WATCH")
        self.assertEqual(fr.category_from_rp(1.6, "CC-LB-171"), "WATCH")
        self.assertEqual(fr.category_from_rp(1.6, "CC-UP-503"), "NORMAL")


class TestPIBand(unittest.TestCase):
    def test_band_brackets_best(self):
        for bid in NON_CAMPUS:
            cq = BASINS[bid]["calib_anchors"][1][0]     # ~100-yr model peak
            cq = fr.calibrate_peak(cq, bid)
            best, lo, hi = fr.pi_band(cq, bid)
            self.assertIsNotNone(lo); self.assertIsNotNone(hi)
            self.assertLessEqual(lo, best + 1e-6, f"{bid}: lo>best")
            self.assertGreaterEqual(hi, best - 1e-6, f"{bid}: hi<best")

    def test_helene_up503_band(self):
        # DESIGN-STORM stress input (10 in Type II), NOT observed Helene:
        # exercises pi_band mechanics at a high-flow input.
        m = cwm.assess("CC-UP-503", 10, 0.25)
        best, lo, hi = fr.pi_band(m["calib_q"], "CC-UP-503")
        self.assertTrue(15 <= round(lo) <= 25, f"lo={lo}")
        self.assertEqual(round(hi), 500)
        self.assertEqual(fr.category_from_rp(lo, "CC-UP-503"), "WARNING")
        self.assertEqual(fr.category_from_rp(hi, "CC-UP-503"), "EMERGENCY")


class TestAssess(unittest.TestCase):
    def test_campus_uses_validated_stage(self):
        a = fr.assess(12655, "CC-WCU-2260")            # model 100-yr peak
        self.assertEqual(a["basis"], "validated stage (TVA 7/9/11 ft)")
        self.assertTrue(a["thr_validated"])
        self.assertEqual(a["confidence"], "validated")

    def test_noncampus_uses_frequency(self):
        for bid in NON_CAMPUS:
            a = fr.assess(BASINS[bid]["calib_anchors"][0][0], bid)
            self.assertEqual(a["basis"], "discharge frequency (USGS regression)")
            self.assertIsNotNone(a["rp_band"])

    def test_mouth_gives_creek_only_status(self):
        # The mouth used to return "N/A" and this test asserted that. The
        # engine deliberately stopped doing so (flood_rating.assess, rating ==
        # "none"): it now computes the CREEK half of the confluence posture by
        # §2 discharge frequency and says plainly that Tuckasegee backwater is
        # not in it. That is the more protective behaviour and it matches the
        # project scope — out of scope for MEASUREMENT under the no-mainstem
        # rule is not out of scope for PROTECTION. The test was simply never
        # updated, so it had been failing on main. Assert the real contract.
        a = fr.assess(5000, "CC-MOUTH-2340")
        self.assertIn(a["posture"], ("NORMAL", "WATCH", "WARNING", "EMERGENCY"))
        self.assertIsNotNone(a["rp_best"])
        self.assertIn("backwater", a["basis"])
        self.assertIn("backwater not included", a["confidence"])
        # and it must NOT masquerade as a full confluence assessment
        self.assertIsNone(a["rp_band"])


class TestLeadTime(unittest.TestCase):
    def test_lead_limited_set(self):
        limited = set(lt.lead_limited_basins())
        # every reach with Tc<120 is lead-limited; only campus(127)/mouth(147) clear it
        self.assertNotIn("CC-WCU-2260", limited)
        self.assertNotIn("CC-MOUTH-2340", limited)
        for bid in NON_CAMPUS:
            self.assertIn(bid, limited, f"{bid} should be lead-limited")

    def test_margin_sign(self):
        for bid in routed_order():
            f = lt.lead_flags(bid)
            if f["tc_min"] is None:
                continue
            self.assertEqual(f["lead_limited"], f["margin_min"] < 0)
            self.assertEqual(f["margin_min"], f["tc_min"] - LEAD_REQ_MIN)

    def test_registry_agreement(self):
        # derived flag must agree with the basins.py `lead` tag
        for bid in routed_order():
            f = lt.lead_flags(bid)
            if f["registry_lead"] in ("limited", "adequate"):
                derived = "limited" if f["lead_limited"] else "adequate"
                self.assertEqual(derived, f["registry_lead"], f"{bid} lead mismatch")


class TestEnsemble(unittest.TestCase):
    def test_distribution_normalized(self):
        e = fe.ensemble("CC-SPD-1830", 10, 0.25)
        self.assertAlmostEqual(sum(e["posture_dist"].values()), 1.0, places=3)
        self.assertEqual(len(e["members"]), 9)          # 3x3 grid

    def test_firm_flag(self):
        e = fe.ensemble("CC-SPD-1830", 10, 0.25)
        self.assertEqual(e["firm"], len(e["posture_dist"]) == 1)


class TestHeleneBacktest(unittest.TestCase):
    def test_validates(self):
        self.assertTrue(bt.main(), "Helene back-test must validate")

    def test_frequency_never_under_warns(self):
        # §2's claim is an invariant, not a cast list: above bankfull the
        # rectangular stage rating collapses, so frequency posture must never
        # come out BELOW stage posture on a non-campus reach, and under the
        # 10-in Type II it must still rescue most of them. The old assertion
        # froze WHICH four were rescued, which encoded the placeholder thr_ft
        # and broke the moment five reaches got real LiDAR sections
        # (2026-08-03) — with no physics changing. See GT_STRESS_MIN_CORRECTED.
        rows = bt.run()
        fixed, under = [], []
        rank = {"NORMAL": 0, "WATCH": 1, "WARNING": 2, "EMERGENCY": 3}
        for r in rows:
            if r["bid"] in ("CC-WCU-2260", "CC-MOUTH-2340"):
                continue
            hi, lo = rank[r["eng_posture"]], rank[r["eng_stage_posture"]]
            if hi > lo:
                fixed.append(r["bid"])
            elif hi < lo:
                under.append(r["bid"])
        self.assertEqual(under, [],
                         f"frequency posture under-warns vs stage on {under}")
        self.assertGreaterEqual(len(fixed), bt.GT_STRESS_MIN_CORRECTED,
                                f"only {len(fixed)} reaches rescued: {fixed}")

    def test_campus_emergency_design_storm(self):
        # bt.run() is the DESIGN-STORM stress test (10 in Type II), not the
        # observed event. Campus is EMERGENCY under that hypothetical shape.
        rows = {r["bid"]: r for r in bt.run()}
        self.assertEqual(rows["CC-WCU-2260"]["eng_posture"], "EMERGENCY")

    def test_observed_matches_surveyed_marks(self):
        # OBSERVED Helene (real ~40-h hyetograph) must reproduce the surveyed
        # NCGS high-water marks within tolerance.
        for mk, (pred, surv, diff) in bt.mark_reconciliation().items():
            self.assertLessEqual(abs(diff), bt.GT_MARK_TOL_FT,
                                 f"mark {mk}: model {pred} vs surveyed {surv}")

    def test_observed_campus_below_emergency(self):
        # OBSERVED campus peak is well below the 11 ft EMERGENCY threshold
        # (design-storm gave 11.2 ft; the real hyetograph gives ~8.4 ft).
        obs = {r["bid"]: r for r in bt.run_observed()}
        self.assertLess(obs["CC-WCU-2260"]["stage"], 11.0)
        self.assertIn(obs["CC-WCU-2260"]["posture"], ("WATCH", "WARNING"))


class TestOutlookFeedPath(unittest.TestCase):
    """feed/outlook.json end to end, 2026-08-23. Two defects found the day the
    WeatherNext feed first carried real members, both on the same root: the
    mouth basin has no creek-stage ladder BY DECISION (backwater-controlled,
    basins.py 2026-08-13) and two consumers assumed every basin has one.
    (1) live_rainfall.compute_from_response: round(None) on the mouth's stage
        raised TypeError — and because feed_runner wraps the whole antecedent
        fetch in one try, every basin silently ran at the ARC-II default.
    (2) outlook_engine._cap("N/A") raised ValueError, dropping the mouth from
        the feed with an error string. The mouth's honest answer is "N/A".
    Plus the doc-vs-repo check: the Aug 10 runbook said live.html rendered this
    feed; it did not. A page that reads a feed must name it, and the keys it
    reads must be keys the publisher writes."""

    MOUTH = "CC-MOUTH-2340"

    def _daily_response(self, n=8, rain=0.1):
        import datetime as dt
        today = dt.date.today()
        dates = [(today + dt.timedelta(days=d)).isoformat() for d in range(-30, 8)]
        loc = {"daily": {"time": dates,
                         "precipitation_sum": [rain] * len(dates),
                         "et0_fao_evapotranspiration": [3.0] * len(dates)},
               "daily_units": {"et0_fao_evapotranspiration": "mm"}}
        return [dict(loc) for _ in range(n)]

    def test_mouth_has_no_ladder_by_decision(self):
        # the premise the two fixes rest on; if this changes, revisit both
        self.assertIsNone(BASINS[self.MOUTH]["thr_ft"])

    def test_live_rainfall_survives_mouth(self):
        import datetime as dt
        import live_rainfall
        rows = live_rainfall.compute_from_response(
            self._daily_response(), now=dt.datetime.now(dt.timezone.utc))
        self.assertEqual(set(rows), set(BASINS))
        self.assertIsNone(rows[self.MOUTH]["stage"])
        self.assertEqual(rows[self.MOUTH]["posture"], "N/A")
        # and the other seven still carry a live (non-default) wetness source
        self.assertNotEqual(rows["CC-WCU-2260"]["wetness_src"], "default_ARC_II")

    def test_cap_passes_na_through(self):
        import outlook_engine as oe
        self.assertEqual(oe._cap("N/A"), "N/A")
        self.assertEqual(oe._cap("EMERGENCY"), "WATCH")
        self.assertEqual(oe._cap("NORMAL"), "NORMAL")

    def test_ensemble_forecast_survives_mouth(self):
        import outlook_engine as oe
        import weathernext_source as wn
        d = wn.make_fixture()
        m24, _ = wn.max_window_totals(d["basins"][self.MOUTH], 24, horizon_hr=72)
        fc = oe.forecast_basin_ens(self.MOUTH, m24, p5_in=1.7)
        self.assertEqual(fc["outlook_level"], "N/A")
        self.assertIsNone(fc["stage_ft"])
        self.assertEqual(fc["p_exceed"]["WATCH"], 0.0)
        # a real basin on the same fixture still posts a WATCH-capped outlook
        m24, _ = wn.max_window_totals(d["basins"]["CC-WCU-2260"], 24, horizon_hr=72)
        fc = oe.forecast_basin_ens("CC-WCU-2260", m24, p5_in=1.7)
        self.assertIn(fc["outlook_level"], ("NORMAL", "WATCH"))

    def test_live_html_reads_the_outlook_feed(self):
        import os
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "live.html"), encoding="utf-8") as f:
            page = f.read()
        self.assertIn("feed/outlook.json", page,
                      "live.html does not fetch feed/outlook.json — the extended "
                      "outlook panel is missing (runbook claimed it since 2026-08-10)")
        # every key the page reads is a key feed_runner.publish_outlook writes
        for key in ("campus_daily", "p_watch", "qpf_in", "p_exceed", "outlook_level",
                    "qpf72_in", "worst24_start_utc", "bias_mult", "wetness_note",
                    "n_members", "fetched_utc"):
            self.assertIn(key, page, f"page never reads {key}")
        with open(os.path.join(here, "feed_runner.py"), encoding="utf-8") as f:
            runner = f.read()
        for key in ("campus_daily", "p_watch", "qpf72_in", "worst24_start_utc",
                    "bias_mult", "wetness_note", "n_members", "fetched_utc"):
            self.assertIn(f'"{key}"', runner, f"feed_runner never writes {key}")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestReadinessChain(unittest.TestCase):
    """readiness.py: the four-step chain with provenance. Sensors fill gaps through
    sources backends; the chain itself is never edited when hardware arrives."""

    def _build(self, backend, floor_level="ELEVATED", ero=None, feed_dir=None, now=None, qpf=(2.0, 4.0)):
        import readiness, sources, json, tempfile
        from datetime import datetime, timezone
        from pathlib import Path
        now = now or datetime(2026, 10, 3, 14, 0, tzinfo=timezone.utc)
        d = Path(feed_dir) if feed_dir else Path(tempfile.mkdtemp())
        (d / "outlook.json").write_text(json.dumps({
            "status": "ok", "nws_qpf24_in": {b: 1.0 for b in BASINS},
            "basins": {b: {"qpf24_in": {"p50": qpf[0], "p90": qpf[1]}, "qpf72_in": {"p50": qpf[0] * 1.5, "p90": qpf[1] * 1.5}}
                       for b in BASINS}}))
        sources.set_backend(backend)
        try:
            floor = dict(level=floor_level, why="test", storms=[], latched=False, sequence=None, status="ok")
            ero = ero if ero is not None else {"status": "unavailable (test)"}
            return readiness.build(now, feed_dir=d, modeled_rows={}, floor=floor, ero=ero), now
        finally:
            sources.set_backend(sources.NullBackend())

    def test_default_everything_modeled_and_capped(self):
        import sources
        out, _ = self._build(sources.NullBackend())
        for bid, r in out["basins"].items():
            self.assertEqual(r["wetness"]["tier"], sources.MODELED, bid)
            self.assertEqual(r["ceiling"], "WATCH", bid)
            self.assertEqual(r["sensors"]["deployed"], [], bid)
            self.assertNotIn(r["outlook_level"], ("WARNING", "EMERGENCY"), bid)  # forecast cap
        self.assertIsNone(out["basins"]["CC-MOUTH-2340"]["trip_in"])          # no ladder by decision

    def test_trip_inches_are_ordered_and_monotone_in_wetness(self):
        import readiness
        for bid in NON_CAMPUS + ["CC-WCU-2260"]:
            dry, wetter = readiness.trip_inches(bid, 0.3), readiness.trip_inches(bid, 0.8)
            for t in (dry, wetter):
                if t["WARNING"] is not None and t["WATCH"] is not None:
                    self.assertGreaterEqual(t["WARNING"], t["WATCH"], bid)
                if t["EMERGENCY"] is not None and t["WARNING"] is not None:
                    self.assertGreaterEqual(t["EMERGENCY"], t["WARNING"], bid)
            self.assertLess(wetter["WATCH"], dry["WATCH"], bid)   # wetter ground trips sooner

    def test_measured_soil_flips_tag_and_moves_trip(self):
        import sources
        from datetime import timedelta
        from datetime import datetime, timezone
        now = datetime(2026, 10, 3, 14, 0, tzinfo=timezone.utc)
        be = sources.DictBackend()
        be.put(sources.Reading(88.0, sources.MEASURED, "NOAH SPD-01 TEROS", now - timedelta(minutes=20), sources.Q_SOIL), "CC-SPD-1830")
        be.put(sources.Reading(2.31, sources.MEASURED, "NOAH SPD-01 radar", now - timedelta(minutes=4), sources.Q_STAGE), "CC-SPD-1830")
        out, _ = self._build(be)
        spd, cox = out["basins"]["CC-SPD-1830"], out["basins"]["CC-COX-097"]
        self.assertEqual(spd["wetness"]["tier"], sources.MEASURED)
        self.assertEqual(cox["wetness"]["tier"], sources.MODELED)          # untouched basin
        self.assertIn("measured stage", spd["ceiling"])                     # confirmation unlocked
        self.assertEqual(cox["ceiling"], "WATCH")
        self.assertIn("stage_ft", spd["sensors"]["deployed"])
        base, _ = self._build(sources.NullBackend())
        self.assertLess(spd["trip_in"]["WATCH"], base["basins"]["CC-SPD-1830"]["trip_in"]["WATCH"])

    def test_stale_sensor_falls_back_and_says_why(self):
        import sources
        from datetime import datetime, timezone, timedelta
        now = datetime(2026, 10, 3, 14, 0, tzinfo=timezone.utc)
        be = sources.DictBackend()
        be.put(sources.Reading(88.0, sources.MEASURED, "NOAH COX-01 TEROS", now - timedelta(hours=9), sources.Q_SOIL), "CC-COX-097")
        out, _ = self._build(be)
        cox = out["basins"]["CC-COX-097"]
        self.assertEqual(cox["wetness"]["tier"], sources.MODELED)
        self.assertIn("stale", cox["wetness"]["note"])

    def test_file_backend_is_the_deployment_contract(self):
        import sources, noah_readings, json, tempfile
        from datetime import datetime, timezone, timedelta
        from pathlib import Path
        now = datetime.now(timezone.utc)
        p = Path(tempfile.mkdtemp()) / "readings.json"
        p.write_text(json.dumps({"readings": [
            {"basin": "CC-SPD-1830", "quantity": "soil_moisture_pct", "value": 61.0,
             "ts": (now - timedelta(minutes=10)).isoformat(), "source": "NOAH SPD-01"}]}))
        r = noah_readings.FileBackend(p).latest(sources.Q_SOIL, "CC-SPD-1830")
        self.assertIsNotNone(r); self.assertEqual(r.tier, sources.MEASURED); self.assertEqual(r.value, 61.0)
        self.assertIsNone(noah_readings.FileBackend(p).latest(sources.Q_SOIL, "CC-COX-097"))
        self.assertIsNone(noah_readings.FileBackend(p / "missing.json").latest(sources.Q_SOIL, "CC-SPD-1830"))

    def test_floor_holds_top_rung_inside_corridor_and_segment_test(self):
        import readiness
        # a decayed storm already inside the box holds WATCH_PENDING
        r = readiness.eval_storm(dict(cls="Tropical Depression", lat=35.0, lon=-83.9, heading=20, points=[]), None)
        self.assertEqual(r["floor"], "WATCH_PENDING"); self.assertTrue(r["inside"])
        # Helene-shaped forecast: points straddle the box; segment test must catch it
        st = dict(cls="Hurricane", lat=30.1, lon=-83.6, heading=10,
                  points=[dict(lat=31.3, lon=-83.3, tau=6, status="HU"),
                          dict(lat=34.4, lon=-83.2, tau=12, status="TS"),   # on the line, not west of it
                          dict(lat=36.8, lon=-84.9, tau=18, status="TD")])  # north of the box
        r = readiness.eval_storm(st, None)
        self.assertTrue(r["met"]); self.assertEqual(r["floor"], "WATCH_PENDING")

    def test_bench_and_test_rows_never_resolve_as_measured(self):
        import sources, noah_readings, json, tempfile
        from datetime import datetime, timezone, timedelta
        from pathlib import Path
        now = datetime.now(timezone.utc)
        p = Path(tempfile.mkdtemp()) / "readings.json"
        p.write_text(json.dumps({"readings": [
            {"basin": "CC-WCU-2260", "quantity": "temp_c", "value": 22.4, "ts": (now - timedelta(minutes=5)).isoformat(),
             "source": "BME280 bench Belk", "test": True},
            {"basin": "BENCH-BELK", "quantity": "press_hpa", "value": 951.2, "ts": (now - timedelta(minutes=5)).isoformat(),
             "source": "BME280 bench Belk"},
            {"basin": "CC-SPD-1830", "quantity": "temp_c", "value": 21.0, "ts": (now - timedelta(minutes=5)).isoformat(),
             "source": "BME280 SPD-01"}]}))
        be = noah_readings.FileBackend(p)
        self.assertIsNone(be.latest(sources.Q_TEMP_C, "CC-WCU-2260"))        # test flag
        self.assertIsNone(be.latest(sources.Q_PRESS_HPA, "BENCH-BELK"))      # bench basin
        r = be.latest(sources.Q_TEMP_C, "CC-SPD-1830")                        # the real one
        self.assertIsNotNone(r); self.assertEqual(r.tier, sources.MEASURED)
        sources.set_backend(be)
        try:
            g = sources.resolve(sources.Q_TEMP_C, "CC-SPD-1830", None, now=now)
            self.assertEqual(g.tier, sources.MEASURED)
            bad = sources.gate(sources.Reading(1500.0, sources.MEASURED, "x", now, sources.Q_PRESS_HPA), now)
            self.assertFalse(bad.valid)                                        # range guard
        finally:
            sources.set_backend(sources.NullBackend())

    # ---- wake-up call: alarms → mode ------------------------------------------------
    def test_quiet_when_nothing_rings(self):
        import sources
        out, _ = self._build(sources.NullBackend(), floor_level="NONE", qpf=(0.3, 0.8))
        self.assertEqual(out["mode"], "QUIET")
        self.assertEqual(out["alarms"], [])
        self.assertEqual(out["cadence"], __import__("readiness").CADENCE["QUIET"])
        self.assertTrue(any("excessive-rainfall" in n for n in out["notes"]))   # absent source is named

    def test_corridor_floor_sets_mode_and_names_the_alarm(self):
        import sources
        out, _ = self._build(sources.NullBackend(), floor_level="ANALOG", qpf=(0.3, 0.8))
        self.assertEqual(out["mode"], "ATTENTION")
        self.assertEqual([a["name"] for a in out["alarms"]], ["corridor"])
        out, _ = self._build(sources.NullBackend(), floor_level="WATCH_PENDING", qpf=(0.3, 0.8))
        self.assertEqual(out["mode"], "STORM")

    def test_wpc_ero_is_the_broad_alarm(self):
        import sources
        def ero(dn, day=2):
            days = [dict(day=i, dn=(dn if i == day else 0), label="x", pct=(70 if dn == 4 else 15)) for i in range(1, 6)]
            return dict(status="ok", tier="gov_estimate", days=days, max_dn=dn, max_day=day)
        out, _ = self._build(sources.NullBackend(), floor_level="NONE", ero=ero(2), qpf=(0.3, 0.8))
        self.assertEqual(out["mode"], "ATTENTION")            # Slight: start looking
        self.assertEqual(out["alarms"][0]["name"], "wpc_ero")
        out, _ = self._build(sources.NullBackend(), floor_level="NONE", ero=ero(4, day=3), qpf=(0.3, 0.8))
        self.assertEqual(out["mode"], "STORM")                # High on day 3: sample fast
        self.assertIn("day 3", out["alarms"][0]["detail"])
        for r in out["basins"].values():                       # a wake-up call never touches posture
            self.assertEqual(r["ceiling"], "WATCH")
            self.assertNotIn(r["outlook_level"], ("WARNING", "EMERGENCY"))

    def test_wetness_trend_rings_on_rising_ground_not_on_level(self):
        import readiness
        from datetime import datetime, timezone, timedelta
        now = datetime(2026, 10, 3, 14, 0, tzinfo=timezone.utc)
        def run(ws):
            state = {}
            t = None
            for i, w in enumerate(ws):
                t = now - timedelta(days=len(ws) - 1 - i)
                t = readiness.wetness_trend(state, {"CC-WCU-2260": {"wetness": {"w": w}}}, t)
            return t
        self.assertTrue(run([0.45, 0.55, 0.65, 0.75])["ringing"])        # +0.1/day, above floor
        self.assertFalse(run([0.75, 0.75, 0.75, 0.75])["ringing"])       # wet but level
        self.assertFalse(run([0.20, 0.30, 0.40, 0.50])["ringing"])       # rising but still dry
        self.assertIsNone(run([0.7]))                                     # no history yet

    def test_mode_since_persists_across_cycles(self):
        import sources, json, tempfile
        from datetime import datetime, timezone, timedelta
        from pathlib import Path
        d = Path(tempfile.mkdtemp())
        t0 = datetime(2026, 10, 3, 14, 0, tzinfo=timezone.utc)
        a, _ = self._build(sources.NullBackend(), floor_level="ANALOG", feed_dir=d, now=t0, qpf=(0.3, 0.8))
        b, _ = self._build(sources.NullBackend(), floor_level="ANALOG", feed_dir=d, now=t0 + timedelta(minutes=30), qpf=(0.3, 0.8))
        self.assertEqual(a["mode_since"], b["mode_since"])                # same mode: clock keeps running
        self.assertEqual(b["prev_mode"], "ATTENTION")
        c, _ = self._build(sources.NullBackend(), floor_level="NONE", feed_dir=d, now=t0 + timedelta(hours=1), qpf=(0.3, 0.8))
        self.assertEqual(c["mode"], "QUIET"); self.assertNotEqual(c["mode_since"], a["mode_since"])

    def test_wpc_ero_fetch_never_raises(self):
        import wpc_ero
        from unittest import mock
        with mock.patch.object(wpc_ero, "_query", side_effect=OSError("no network")):
            r = wpc_ero.fetch()
        self.assertTrue(r["status"].startswith("unavailable"))
        self.assertEqual(r["max_dn"], 0)
        feats = lambda dn: {"features": [{"attributes": {"dn": dn, "outlook": "x", "issue_time": None, "start_time": None, "end_time": None}}] if dn else []}
        with mock.patch.object(wpc_ero, "_query", side_effect=[feats(1), feats(3), feats(0), feats(0), feats(0)]):
            r = wpc_ero.fetch()
        self.assertEqual((r["status"], r["max_dn"], r["max_day"]), ("ok", 3, 2))
        self.assertEqual(r["days"][1]["label"], "Moderate")

    def test_forecast_margin_alarm_rings_when_forecast_rain_reaches_the_trip_line(self):
        import sources
        out, _ = self._build(sources.NullBackend(), floor_level="NONE", qpf=(0.5, 4.0))
        self.assertEqual(out["mode"], "ATTENTION")                          # p90 only
        self.assertEqual(out["alarms"][0]["name"], "forecast_margin")
        out, _ = self._build(sources.NullBackend(), floor_level="NONE", qpf=(4.0, 6.0))
        self.assertEqual(out["mode"], "STORM")                              # p50 reaches WATCH
        self.assertTrue(all(r["ceiling"] == "WATCH" for r in out["basins"].values()))
