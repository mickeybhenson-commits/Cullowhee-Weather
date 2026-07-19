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
import confluence_status as cs

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
        # memo §3: Helene band edges ~19 (WARNING) and 500 (EMERGENCY)
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

    def test_mouth_out_of_scope(self):
        a = fr.assess(5000, "CC-MOUTH-2340")
        self.assertEqual(a["posture"], "N/A")


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

    def test_four_reaches_corrected(self):
        rows = bt.run()
        fixed = []
        rank = {"NORMAL": 0, "WATCH": 1, "WARNING": 2, "EMERGENCY": 3}
        for r in rows:
            if r["bid"] in ("CC-WCU-2260", "CC-MOUTH-2340"):
                continue
            if rank[r["eng_posture"]] > rank[r["eng_stage_posture"]]:
                fixed.append(r["bid"])
        self.assertEqual(set(fixed),
                         {"CC-UP-503", "CC-TIL-705", "CC-MS-1100", "CC-SPD-1830"})

    def test_campus_emergency(self):
        rows = {r["bid"]: r for r in bt.run()}
        self.assertEqual(rows["CC-WCU-2260"]["eng_posture"], "EMERGENCY")


class TestConfluence(unittest.TestCase):
    def test_backwater_mapping(self):
        # NWS TKRN7 categories: action 13 / minor 16 / moderate 19
        self.assertEqual(cs.backwater_posture(5.0)[0], "NORMAL")
        self.assertEqual(cs.backwater_posture(13.0)[0], "WATCH")
        self.assertEqual(cs.backwater_posture(16.0)[0], "WARNING")
        self.assertEqual(cs.backwater_posture(19.0)[0], "EMERGENCY")
        self.assertEqual(cs.backwater_posture(24.0)[0], "EMERGENCY")

    def test_takes_worse_mechanism(self):
        helene_peak = cwm.assess(cs.CONFLUENCE_BID, 10, 0.25)["qp_raw"]
        # creek flood, river calm -> creek drives EMERGENCY
        r1 = cs.confluence_status(model_peak_q_cfs=helene_peak, gage_ht_ft=5.0)
        self.assertEqual(r1["confluence_posture"], "EMERGENCY")
        self.assertEqual(r1["driver"], "creek-runoff")
        # creek calm, river minor -> river drives WARNING
        calm = cwm.assess(cs.CONFLUENCE_BID, 1.0, 0.3)["qp_raw"]
        r2 = cs.confluence_status(model_peak_q_cfs=calm, gage_ht_ft=17.0)
        self.assertEqual(r2["confluence_posture"], "WARNING")
        self.assertEqual(r2["driver"], "river-backwater")

    def test_normal_when_both_calm(self):
        calm = cwm.assess(cs.CONFLUENCE_BID, 1.0, 0.3)["qp_raw"]
        r = cs.confluence_status(model_peak_q_cfs=calm, gage_ht_ft=5.21)
        self.assertEqual(r["confluence_posture"], "NORMAL")
        self.assertEqual(r["driver"], "none")

    def test_receptor_check(self):
        # gauge WSE = datum 2111.45 + 18 = 2129.45; home floor 2130 -> dry, 0.55 ft freeboard
        rc = cs.receptor_check(18.0, 2130.0)
        self.assertFalse(rc["receptor_wet"])
        self.assertAlmostEqual(rc["freeboard_ft"], 0.55, places=2)
        rc2 = cs.receptor_check(20.0, 2130.0)   # WSE 2131.45 > floor -> wet
        self.assertTrue(rc2["receptor_wet"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
