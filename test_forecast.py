"""
test_forecast.py - tests for the live per-basin forecast pipeline (forecast.py).

Covers the forcing extraction (pure, on a synthetic Open-Meteo response), the
per-basin chain, the watershed roll-up, the publisher hook, and the
degradation behaviour that matters most in this system: when the data is not
there, the answer is "no forecast", never a plausible-looking number.

No number is asserted here that is not traceable to basins.py.

  python -m unittest test_forecast -v
"""

import datetime
import unittest

import cwm_model as cwm
import forecast as F
import wetness as wet
from basins import BASINS, routed_order

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 8, 1, 0, 0, tzinfo=UTC)


def synthetic_response(burst_in=6.0, burst_at_h=30, burst_len_h=24,
                       drizzle_in_per_day=0.1, n_points=8, now=NOW):
    """Open-Meteo-shaped response: `drizzle` every past day, then a single
    `burst_in` total spread evenly over `burst_len_h` hours starting
    `burst_at_h` hours from now."""
    times, precip = [], []
    start = now - datetime.timedelta(days=F.PAST_DAYS)
    for h in range(F.PAST_DAYS * 24 + F.FORECAST_DAYS * 24):
        t = start + datetime.timedelta(hours=h)
        fh = (t - now).total_seconds() / 3600.0
        times.append(t.strftime("%Y-%m-%dT%H:00"))
        if fh < 0:
            precip.append(drizzle_in_per_day / 24.0)
        elif burst_at_h <= fh < burst_at_h + burst_len_h:
            precip.append(burst_in / burst_len_h)
        else:
            precip.append(0.0)
    daily = [drizzle_in_per_day] * F.PAST_DAYS + [0.0] * F.FORECAST_DAYS
    loc = {"hourly": {"time": times, "precipitation": precip},
           "daily": {"precipitation_sum": daily}}
    return [dict(loc) for _ in range(n_points)]


def flat_forcing(qpf, w):
    return {b: {"qpf_in": qpf, "wetness": w} for b in routed_order()}


class TestRollingMax(unittest.TestCase):
    def test_picks_best_window(self):
        self.assertEqual(F.rolling_max([1, 2, 3, 10, 1], 2), (13, 2))

    def test_short_series_returns_whole(self):
        self.assertEqual(F.rolling_max([1, 2], 24), (3, 0))

    def test_empty(self):
        self.assertEqual(F.rolling_max([], 24), (0.0, 0))

    def test_none_treated_as_zero(self):
        self.assertEqual(F.rolling_max([None, 5.0, None], 1), (5.0, 1))


class TestForcing(unittest.TestCase):
    def setUp(self):
        self.f = F.forcing_from_response(synthetic_response(), now=NOW)

    def test_all_eight_basins(self):
        self.assertEqual(set(self.f), set(routed_order()))
        self.assertEqual(len(self.f), 8)

    def test_qpf_is_the_24h_burst_not_the_3day_total(self):
        # The burst is 6 in over exactly 24 h. QPF must be the rolling 24-h
        # maximum, which is the whole burst and nothing more.
        for bid, v in self.f.items():
            self.assertAlmostEqual(v["qpf_in"], 6.0, places=2, msg=bid)
            self.assertEqual(v["qpf_window_hr"], 24)

    def test_qpf_window_start_is_located(self):
        # burst opens 30 h after "now" = 2026-08-02T06:00Z
        for v in self.f.values():
            self.assertEqual(v["qpf_start_utc"], "2026-08-02T06:00")

    def test_antecedent_api_matches_closed_form(self):
        # 30 days of steady p in/day -> API = p * (1-k^30)/(1-k)
        k = wet.API_K
        expected = 0.1 * (1 - k ** 30) / (1 - k)
        for v in self.f.values():
            self.assertAlmostEqual(v["api_in"], round(expected, 2), places=2)

    def test_wetness_from_api_rung(self):
        for v in self.f.values():
            self.assertEqual(v["wetness_src"], "api30")
            self.assertGreaterEqual(v["wetness"], 0.0)
            self.assertLessEqual(v["wetness"], 1.0)

    def test_forecast_rain_cannot_leak_into_antecedent(self):
        # A huge burst in the FORECAST window must not raise the antecedent API.
        dry = F.forcing_from_response(synthetic_response(burst_in=0.0), now=NOW)
        wetr = F.forcing_from_response(synthetic_response(burst_in=12.0), now=NOW)
        for bid in routed_order():
            self.assertAlmostEqual(dry[bid]["api_in"], wetr[bid]["api_in"], places=6,
                                   msg=f"{bid}: forecast rain leaked backwards")

    def test_soil_percentile_outranks_api(self):
        f = F.forcing_from_response(synthetic_response(), now=NOW,
                                    soil_pct={"CC-UP-503": 0.9})
        self.assertEqual(f["CC-UP-503"]["wetness_src"], "soil_percentile")
        self.assertAlmostEqual(f["CC-UP-503"]["wetness"], 0.9, places=6)
        self.assertEqual(f["CC-MS-1100"]["wetness_src"], "api30")


class TestPerBasinChain(unittest.TestCase):
    """The accuracy anchor: the chain must land on the USGS regression."""

    def test_10yr_design_storm_reproduces_regression_10yr(self):
        # 4.80 in is the 24-h depth derived (test_model.design_depth_for) to put
        # the CAMPUS on its regression 10-yr flow at median wetness. The campus
        # is therefore exact by construction; the other reaches are independent
        # and must still land near their own 10-yr, which is what makes the
        # per-basin calibration credible rather than curve-fitted.
        for bid in routed_order():
            r = F.forecast_basin(bid, 4.80, 0.5, with_ensemble=False)
            target = BASINS[bid]["reg_q"][0.10]
            err = abs(r["calib_q_cfs"] - target) / target
            tol = 0.02 if bid == "CC-WCU-2260" else 0.06
            self.assertLess(err, tol,
                            f"{bid}: {r['calib_q_cfs']} cfs vs 10-yr {target} "
                            f"({err:.1%} off)")

    def test_return_period_tracks_the_flow(self):
        for bid in routed_order():
            r = F.forecast_basin(bid, 4.80, 0.5, with_ensemble=False)
            self.assertIsNotNone(r["rp_best_yr"])
            self.assertGreaterEqual(r["rp_best_yr"], 8)
            self.assertLessEqual(r["rp_best_yr"], 12)

    def test_more_rain_never_lowers_discharge(self):
        for bid in routed_order():
            qs = [F.forecast_basin(bid, q, 0.5, with_ensemble=False)["calib_q_cfs"]
                  for q in (1.0, 3.0, 6.0, 10.0)]
            self.assertEqual(qs, sorted(qs), f"{bid} not monotone in QPF")

    def test_wetter_soil_never_lowers_discharge(self):
        for bid in routed_order():
            qs = [F.forecast_basin(bid, 4.0, w, with_ensemble=False)["calib_q_cfs"]
                  for w in (0.0, 0.25, 0.5, 0.75, 1.0)]
            self.assertEqual(qs, sorted(qs), f"{bid} not monotone in wetness")

    def test_no_rain_is_normal_everywhere(self):
        for bid in routed_order():
            r = F.forecast_basin(bid, 0.0, 0.5, with_ensemble=False)
            self.assertEqual(r["posture"], "NORMAL", bid)
            self.assertEqual(r["runoff_in"], 0.0)

    def test_baseflow_alone_never_breaches_watch(self):
        for bid in routed_order():
            r = F.forecast_basin(bid, 0.0, 0.5, with_ensemble=False)
            st = r["stage_total_ft"]
            if st is not None:
                thr = BASINS[bid]["thr_ft"]
                if thr:
                    self.assertLess(st, thr[0], f"{bid} baseflow breaches WATCH")

    def test_campus_uses_validated_stage_others_use_frequency(self):
        campus = F.forecast_basin("CC-WCU-2260", 4.80, 0.5, with_ensemble=False)
        self.assertTrue(campus["thr_validated"])
        self.assertIn("validated stage", campus["basis"])
        for bid in routed_order():
            if bid in ("CC-WCU-2260", "CC-MOUTH-2340"):
                continue
            r = F.forecast_basin(bid, 4.80, 0.5, with_ensemble=False)
            self.assertIn("frequency", r["basis"], bid)
            self.assertFalse(r["thr_validated"], bid)

    def test_ensemble_attached_and_brackets_the_call(self):
        r = F.forecast_basin("CC-WCU-2260", 4.80, 0.5, with_ensemble=True)
        self.assertIn(r["posture"], r["ensemble_dist"])
        # members are 9 thirds rounded to 3 dp, so the sum is 1.0 +- rounding
        self.assertAlmostEqual(sum(r["ensemble_dist"].values()), 1.0, places=2)

    def test_helene_forcing_puts_campus_in_emergency(self):
        # Helene design-storm stress test: 10 in on drought-dry antecedent.
        # basins.py/backtest_helene.py hold this as the validated anchor.
        r = F.forecast_basin("CC-WCU-2260", 10.0, 0.25, with_ensemble=False)
        self.assertEqual(r["posture"], "EMERGENCY")
        self.assertGreaterEqual(r["stage_total_ft"], 11.0)

    def test_tc_consistency_is_reported_not_hidden(self):
        # basins.py tc_min and the engine's hydrograph Tc agree on six of eight
        # reaches. The two that disagree are exactly the two whose basins.py
        # tc_src records an ambiguous Tc. This test pins that down so the
        # discrepancy cannot silently drift or silently disappear.
        mismatched = {bid for bid in routed_order()
                      if not F.forecast_basin(bid, 2.0, 0.5,
                                              with_ensemble=False)["tc_consistent"]}
        self.assertEqual(mismatched, {"CC-MS-1100", "CC-SPD-1830"})
        # Both carry a second, competing Tc estimate in their provenance, so
        # neither is a settled number that the engine is contradicting.
        for bid in mismatched:
            self.assertIn("NRCS-wet", BASINS[bid]["tc_src"], bid)


class TestWatershedRollup(unittest.TestCase):
    def test_all_basins_present(self):
        out = F.forecast_all(flat_forcing(4.80, 0.5), with_ensemble=False)
        self.assertEqual(set(out["basins"]), set(routed_order()))

    def test_rollup_is_the_worst_in_scope_posture(self):
        out = F.forecast_all(flat_forcing(4.80, 0.5), with_ensemble=False)
        ws = out["watershed"]
        in_scope = [b for b in out["basins"]
                    if BASINS[b].get("role") != "out_of_scope"]
        worst = max((out["basins"][b]["posture"] for b in in_scope),
                    key=F.SEVERITY.index)
        self.assertEqual(ws["posture"], worst)
        for b in ws["driving_basins"]:
            self.assertEqual(out["basins"][b]["posture"], worst)

    def test_mouth_excluded_from_rollup(self):
        out = F.forecast_all(flat_forcing(4.80, 0.5), with_ensemble=False)
        self.assertNotIn("CC-MOUTH-2340", out["watershed"]["driving_basins"])
        # ...but still reported
        self.assertIn("CC-MOUTH-2340", out["basins"])

    def test_warning_point_is_the_campus(self):
        out = F.forecast_all(flat_forcing(4.80, 0.5), with_ensemble=False)
        self.assertEqual(out["watershed"]["warning_point"], "CC-WCU-2260")
        self.assertEqual(out["watershed"]["warning_point_posture"],
                         out["basins"]["CC-WCU-2260"]["posture"])

    def test_lead_limited_set_matches_registry(self):
        out = F.forecast_all(flat_forcing(1.0, 0.3), with_ensemble=False)
        # every reach except the campus and the mouth is lead-limited
        self.assertEqual(set(out["watershed"]["lead_limited_basins"]),
                         {b for b in routed_order()
                          if b not in ("CC-WCU-2260", "CC-MOUTH-2340")})

    def test_shadow_mode_and_authority_disclosed(self):
        ws = F.forecast_all(flat_forcing(4.8, 0.5),
                            with_ensemble=False)["watershed"]
        self.assertTrue(ws["shadow_mode"])
        self.assertIn("NWS", ws["authority_note"])
        self.assertIn("orographic", ws["qpf_bias_note"])

    def test_escalates_with_storm_size(self):
        mild = F.forecast_all(flat_forcing(1.0, 0.3),
                              with_ensemble=False)["watershed"]["posture"]
        big = F.forecast_all(flat_forcing(10.0, 0.9),
                             with_ensemble=False)["watershed"]["posture"]
        self.assertLessEqual(F.SEVERITY.index(mild), F.SEVERITY.index(big))
        self.assertEqual(big, "EMERGENCY")


class TestPublisherHookAndDegradation(unittest.TestCase):
    def test_modeled_stage_reads_the_campus(self):
        out = F.forecast_all(flat_forcing(4.80, 0.5), with_ensemble=False)
        out["ok"] = True
        self.assertEqual(F.modeled_stage_ft(out),
                         out["basins"]["CC-WCU-2260"]["stage_total_ft"])

    def test_modeled_stage_is_none_when_forecast_failed(self):
        self.assertIsNone(F.modeled_stage_ft({"ok": False, "basins": {}}))

    def test_run_degrades_without_inventing_a_number(self):
        def boom(*a, **kw):
            raise OSError("network down")
        orig = F.fetch_weather
        F.fetch_weather = boom
        try:
            out = F.run()
            self.assertFalse(out["ok"])
            self.assertIn("network down", out["error"])
            self.assertEqual(out["basins"], {})
            self.assertIsNone(out["watershed"])
            self.assertIsNone(F.modeled_stage_ft(out))
        finally:
            F.fetch_weather = orig

    def test_end_to_end_on_a_synthetic_response(self):
        """fetch -> forcing -> forecast -> publisher hook, network stubbed."""
        orig = F.fetch_weather
        F.fetch_weather = lambda *a, **kw: synthetic_response(burst_in=7.5)
        try:
            out = F.run(now=NOW, with_ensemble=False)
            self.assertTrue(out["ok"], out.get("error"))
            self.assertEqual(len(out["basins"]), 8)
            for bid, r in out["basins"].items():
                self.assertAlmostEqual(r["qpf_in"], 7.5, places=2, msg=bid)
                self.assertIn(r["posture"], F.SEVERITY + ["N/A"])
            stage = F.modeled_stage_ft(out)
            self.assertIsNotNone(stage)
            self.assertGreater(stage, 0.0)
        finally:
            F.fetch_weather = orig


class TestEngineConsistency(unittest.TestCase):
    """forecast.py must not drift from the authoritative engine."""

    def test_matches_flood_rating_directly(self):
        import flood_rating as fr
        for bid in routed_order():
            r = F.forecast_basin(bid, 5.0, 0.6, with_ensemble=False)
            direct = fr.assess(r["qp_raw_cfs"], bid)
            self.assertEqual(r["posture"], direct["posture"], bid)
            self.assertEqual(r["calib_q_cfs"], direct["calib_q"], bid)

    def test_uses_registry_basin_set(self):
        self.assertEqual(set(F.BASIN_POINTS), set(BASINS))

    def test_cn_matches_wetness_module(self):
        for bid in routed_order():
            r = F.forecast_basin(bid, 3.0, 0.42, with_ensemble=False)
            expect = wet.cn_from_wetness(cwm.BASINS[bid]["CN2"], 0.42)
            self.assertAlmostEqual(r["cn"], round(expect, 1), places=1, msg=bid)


if __name__ == "__main__":
    unittest.main(verbosity=2)
