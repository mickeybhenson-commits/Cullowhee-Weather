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

    def test_mouth_posts_creek_side_not_na(self):
        # The mouth is rating="none" (no valid stage rating: TVA's mile-0 sections
        # are Tuckasegee-backwater controlled). It is NOT postureless. assess()
        # returns the CREEK half of the confluence - its own §2 discharge
        # frequency - and confluence_status adds the backwater half.
        #
        # This must never go back to "N/A": confluence_status combines the two
        # sides with max() over _RANK, where "N/A" scores -1, i.e. BELOW NORMAL.
        # An "N/A" creek side would therefore be masked by a quiet river and a
        # creek-driven flood at the mouth would vanish. See the safety assertion
        # in TestConfluenceMouth.test_creek_side_cannot_be_masked_by_quiet_river.
        a = fr.assess(5000, "CC-MOUTH-2340")
        self.assertEqual(a["rating"], "none")
        self.assertNotEqual(a["posture"], "N/A")
        self.assertIn(a["posture"], ("NORMAL", "WATCH", "WARNING", "EMERGENCY"))
        # creek-only: no PI band, and the basis/confidence must say backwater is
        # excluded so no caller mistakes this for the operational posture.
        self.assertIsNone(a["rp_band"])
        self.assertIn("backwater not included", a["confidence"])
        self.assertIn("confluence_status", a["basis"])
        # The distinction the old expectation collapsed: the STAGE cross-check is
        # "N/A" at the mouth (out_of_bank_10yr = 3.02, no valid rating, thr_ft None)
        # while the OPERATIVE posture is a real discharge-frequency call.
        self.assertIsNone(a["depth_ft"])
        self.assertEqual(a["stage_posture"], "N/A")
        self.assertNotEqual(a["posture"], a["stage_posture"])
        self.assertFalse(a["thr_validated"])


class TestConfluenceMouth(unittest.TestCase):
    """The mouth's two entry points must agree, and the combination must never
    under-call the creek side."""

    def test_entry_points_agree(self):
        # flood_rating.assess and confluence_status.creek_posture run the same
        # chain (calibrate_peak -> rp_from_q -> category_from_rp). If they ever
        # drift, the console and the engine disagree about the same node.
        for q in (500, 2000, 5000, 12000):
            a = fr.assess(q, "CC-MOUTH-2340")
            cat, rp, cq = cs.creek_posture(q)
            self.assertEqual(a["posture"], cat, f"posture drift at {q} cfs")
            self.assertEqual(a["rp_best"], rp, f"return-period drift at {q} cfs")
            self.assertEqual(a["calib_q"], cq, f"calibrated-Q drift at {q} cfs")

    def test_creek_side_cannot_be_masked_by_quiet_river(self):
        # THE safety property. Tuckasegee well below its NWS action stage (13 ft)
        # => river NORMAL; the creek is running a ~12-yr flow. The confluence must
        # still post the creek's call, driven by runoff.
        r = cs.confluence_status(model_peak_q_cfs=5000, gage_ht_ft=5.2)
        self.assertEqual(r["river"]["posture"], "NORMAL")
        self.assertEqual(r["creek"]["posture"], "WARNING")
        self.assertEqual(r["confluence_posture"], "WARNING")
        self.assertEqual(r["driver"], "creek-runoff")

    def test_confluence_is_worse_of_both_sides(self):
        rank = cs._RANK
        for q in (500, 5000, 12000):
            creek_cat, _, _ = cs.creek_posture(q)
            for gh in (5.2, 13.5, 16.5, 19.5):          # normal/action/minor/moderate
                r = cs.confluence_status(model_peak_q_cfs=q, gage_ht_ft=gh)
                river_cat = r["river"]["posture"]
                self.assertEqual(
                    rank[r["confluence_posture"]],
                    max(rank[creek_cat], rank[river_cat]),
                    f"confluence under-called at q={q}, gage={gh}")

    def test_na_ranks_below_normal(self):
        # Documents WHY the mouth must not return "N/A" - this is the ranking
        # that would swallow it.
        self.assertLess(cs._RANK.get("N/A", -1), cs._RANK["NORMAL"])


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
