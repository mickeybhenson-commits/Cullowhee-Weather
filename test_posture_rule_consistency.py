"""Posture-CLASSIFICATION consistency across the three engines.

WHY THIS EXISTS
---------------
`test_registry_engine_consistency.py` locks the PARAMETERS (thr_ft, DA, Tc) across
basins.py / cwm_model.py / live.html. It does not lock the RULES that turn a number
into a posture, and two approved safety changes have already escaped through that gap:

  2026-08-03  surveyed LiDAR threshold ladders committed to basins.py (f4026bb)
              -> never propagated to cwm_model.py or live.html.
              Caught 2026-08-12, nine days later, only because someone looked.

  2026-07-15  "Flashiest lead-limited reaches (Cox, Long Branch) drop WATCH to 1.5-yr"
              approved and implemented in flood_rating.category_from_rp()
              -> cwm_model._cat_from_rp() and live.html catFromRP() took no basin
              argument at all, so for four weeks neither COULD express it.
              SHIPPED 2026-08-13: both classifiers gained a bid parameter and the
              basin set is now cross-checked in all three engines below.

Both escapes are in the under-warning direction, and the second one lands on the two
basins with the least lead time in the watershed. Two occurrences of one pattern is a
missing control, not bad luck. This is the control.

WHAT IT CHECKS
--------------
1. The three classifiers agree on posture across a dense sweep of return periods,
   for every basin -- except where a divergence is DECLARED below with a reason.
2. Declared divergences are still real. A stale waiver is worse than no waiver: it
   reads as "reviewed" while hiding a live disagreement. If a declared divergence
   has been fixed, this test fails and tells you to delete the entry.
3. The campus stays stage-classified and is not silently moved onto the frequency
   path.

    python test_posture_rule_consistency.py

Exit 0 = consistent. Exit 1 = something diverged that nobody declared.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import basins  # noqa: E402
import cwm_model  # noqa: E402
import flood_rating  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LIVE_HTML = os.path.join(HERE, "live.html")

# --------------------------------------------------------------------------- #
# Declared divergences. Each entry must carry WHY and WHAT CLOSES IT.
# An entry here does NOT mean the divergence is acceptable -- it means it is known
# and tracked. Delete the entry when the divergence is fixed; the test will tell
# you to.
# EMPTY, and that is the point. The two WATCH_1_5YR entries that lived here from
# 2026-07-15 to 2026-08-13 were deleted when the fix shipped -- this test demanded
# it, via test_declared_divergences_are_still_real. Do not add an entry here to make
# a red run green; an entry is a promise that someone decided the divergence is
# tolerable AND wrote down what closes it.
DECLARED = {}

# Return periods to sweep. Dense around every cutoff that exists in any engine.
SWEEP = ([0.5, 1.0, 1.2, 1.4, 1.49, 1.5, 1.51, 1.6, 1.8, 1.9, 1.99, 2.0, 2.01, 2.5]
         + [3, 5, 8, 9.5, 9.99, 10.0, 10.01, 12, 25, 50, 90, 99.9, 100.0, 100.1, 200])

FREQ_CLASSIFIED = [b for b in cwm_model.ORDER if b not in cwm_model.STAGE_BASED]


def classifier_pages():
    """Every page at the repo root carrying its own `function catFromRP`.

    Naming live.html was not enough. rain_to_trip.html — the page that answers "how
    much rain trips a WATCH here?" — held a FOURTH copy of this classifier, still on
    the flat 2-yr cutoff four weeks after the 1.5-yr WATCH was approved, and nothing
    ever looked at it because nothing looked past the one filename.
    """
    out = []
    for fn in sorted(os.listdir(HERE)):
        if not fn.endswith(".html"):
            continue
        try:
            src = open(os.path.join(HERE, fn), encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        if "function catFromRP" in src:
            out.append((fn, src))
    return out


def _live_cat_from_rp(src=None):
    """Extract a page's catFromRP cutoffs so the JS is checked, not assumed."""
    if src is None:
        with open(LIVE_HTML, encoding="utf-8", errors="replace") as f:
            src = f.read()
    m = re.search(r"function\s+catFromRP\s*\(([^)]*)\)\s*\{(.*?)\}", src, re.S)
    assert m, "catFromRP not found in live.html"
    args, body = m.group(1), m.group(2)
    takes_bid = len([a for a in args.split(",") if a.strip()]) > 1
    cuts = {}
    for level in ("EMERGENCY", "WARNING"):
        c = re.search(r"T\s*>=\s*([0-9.]+)\s*\)\s*return\s*\"" + level + r"\"", body)
        assert c, f"cutoff for {level} not found in catFromRP"
        cuts[level] = float(c.group(1))

    # WATCH is per-basin since 2026-08-13. Accept either shape so this test keeps
    # working if the ternary is ever refactored back to a constant -- but read the
    # ACTUAL numbers out of the JS either way. Assuming them is how the JS drifts.
    tern = re.search(r"T\s*>=\s*\(\s*[A-Za-z0-9_]+\.indexOf\(\s*bid\s*\)\s*>=\s*0"
                     r"\s*\?\s*([0-9.]+)\s*:\s*([0-9.]+)\s*\)\s*\)\s*return\s*\"WATCH\"", body)
    if tern:
        cuts["WATCH_LOW"] = float(tern.group(1))     # flashy reaches
        cuts["WATCH"] = float(tern.group(2))         # everyone else
    else:
        c = re.search(r"T\s*>=\s*([0-9.]+)\s*\)\s*return\s*\"WATCH\"", body)
        assert c, "no WATCH cutoff found in catFromRP (constant or per-basin)"
        cuts["WATCH"] = cuts["WATCH_LOW"] = float(c.group(1))

    # And the basin set the JS actually applies it to.
    a = re.search(r"WATCH_1_5YR\s*=\s*\[([^\]]*)\]", src)
    live_set = set(re.findall(r'"([^"]+)"', a.group(1))) if a else set()
    return takes_bid, cuts, live_set


def _live_posture(T, cuts, bid=None, live_set=frozenset()):
    if T is None:
        return "N/A"
    if T >= cuts["EMERGENCY"]:
        return "EMERGENCY"
    if T >= cuts["WARNING"]:
        return "WARNING"
    if T >= (cuts["WATCH_LOW"] if bid in live_set else cuts["WATCH"]):
        return "WATCH"
    return "NORMAL"


# --------------------------------------------------------------------------- #
def test_html_classifiers_are_parseable():
    pages = classifier_pages()
    assert any(fn == "live.html" for fn, _ in pages), (
        f"live.html has no catFromRP — found {[fn for fn, _ in pages]}. The discovery "
        "pattern has stopped matching, so every HTML check below asserts nothing.")
    bad = []
    for fn, src in pages:
        takes_bid, cuts, _ = _live_cat_from_rp(src)
        if cuts["EMERGENCY"] != 100.0:
            bad.append(f"{fn}: EMERGENCY cutoff is {cuts['EMERGENCY']}")
        if cuts["WARNING"] != 10.0:
            bad.append(f"{fn}: WARNING cutoff is {cuts['WARNING']}")
        if cuts["WATCH"] != 2.0:
            bad.append(f"{fn}: default WATCH cutoff is {cuts['WATCH']}")
        if cuts["WATCH_LOW"] != 1.5:
            bad.append(f"{fn}: flashy WATCH cutoff is {cuts['WATCH_LOW']}")
        if not takes_bid:
            bad.append(f"{fn}: catFromRP takes no basin argument, so it CANNOT express "
                       "the 1.5-yr WATCH — the 2026-07-15 defect, in a copy")
    assert not bad, "HTML classifiers are wrong:\n  " + "\n  ".join(bad)


def test_classifiers_agree_or_are_declared():
    """The load-bearing check. Sweep every RP against all three engines."""
    pages = classifier_pages()
    undeclared, declared_hit = [], set()
    for fn, src in pages:
      takes_bid, cuts, live_set = _live_cat_from_rp(src)
      for bid in FREQ_CLASSIFIED:
        for T in SWEEP:
            fr = flood_rating.category_from_rp(T, bid)
            cw = cwm_model._cat_from_rp(T, bid)
            lv = _live_posture(T, cuts, bid, live_set)
            if fr == cw == lv:
                continue
            key = ("WATCH_1_5YR", bid)
            if key in DECLARED and {fr, cw, lv} <= {"NORMAL", "WATCH"}:
                declared_hit.add(key)
                continue
            undeclared.append(
                f"{bid} RP={T}: flood_rating={fr} cwm_model={cw} {fn}={lv}")
    assert not undeclared, (
        "Posture classifiers disagree and it is NOT declared:\n  "
        + "\n  ".join(undeclared[:20])
        + ("\n  ... and more" if len(undeclared) > 20 else "")
        + "\n\nEither fix the engines or add a DECLARED entry saying why not.")
    return declared_hit


def test_declared_divergences_are_still_real():
    """A stale waiver reads as 'reviewed' while hiding nothing. Delete it."""
    hit = test_classifiers_agree_or_are_declared()
    stale = set(DECLARED) - hit
    assert not stale, (
        "These divergences are DECLARED but no longer occur — the fix has shipped.\n  "
        + "\n  ".join(f"{k[0]} / {k[1]}" for k in sorted(stale))
        + "\n\nDelete them from DECLARED so the test starts guarding the fix.")


# Declared front-end divergences. Same contract as DECLARED above: a reason, and
# what closes it.
FRONT_END_DECLARED = {
    "CC-WCU-2260": (
        "BASEFLOW. cwm_model.stage_total() and live.html's stageTotalFromQ() both "
        "rate (calib_q + qb) and then apply an observed clear-day floor; "
        "flood_rating.assess() rates calib_q alone and has no mention of baseflow "
        "anywhere in the file. wetness.py states the rule and says it was verified "
        "2026-07-10: 'Both ratings map TOTAL discharge to TOTAL depth. Baseflow "
        "therefore enters as Qb added to the calibrated storm peak BEFORE rating.' "
        "At the campus qb = 45.2 cfs, worth +0.079 ft at the 7.0 ft WATCH line, and "
        "the two engines disagree across rainfall 3.98-8.58 in at w=0.4. "
        "flood_rating is the AUTHORITATIVE engine and it is the one omitting it, so "
        "the divergence runs in the UNDER-warning direction on the only reach with a "
        "field-validated ladder. Not fixed here: adding baseflow to flood_rating "
        "changes campus warning behaviour and is Mickey's call. "
        "CLOSES WHEN: flood_rating rates (calib_q + baseflow_q(bid)) and applies "
        "STAGE_FLOOR_FT, or the rule in wetness.py is withdrawn in writing."),
}


def test_declared_front_end_divergences_are_still_real():
    """A waiver that outlives its cause reads as reviewed while guarding nothing."""
    test_design_storm_front_end_agrees_with_the_authoritative_engine()
    hit = getattr(test_design_storm_front_end_agrees_with_the_authoritative_engine,
                  "hit", set())
    stale = sorted(set(FRONT_END_DECLARED) - hit)
    assert not stale, (
        "declared as diverging but they now agree - the fix shipped:\n  "
        + "\n  ".join(stale) + "\n\nDelete them from FRONT_END_DECLARED.")


def test_design_storm_front_end_agrees_with_the_authoritative_engine():
    """cwm_model.assess() is the runnable design-storm front end; flood_rating.assess()
    is authoritative. For the same storm they must reach the same posture.

    Until 2026-08-13 `cwm_model.assess()["posture"]` carried the STAGE-LADDER answer for
    EVERY basin — the one flood_rating describes for non-campus reaches as riding "the
    placeholder out-of-bank stage", "retained as a CROSS-CHECK, not the call". Nothing in
    the repo read that key (backtest_helene, flood_ensemble and test_improvements all take
    qp_raw or calib_q and re-classify through flood_rating), so nothing was wrong in
    production. But the obvious key name returned the non-obvious answer, which is how a
    reader gets a confidently wrong number, and there was no test standing between the two.

    The ladder is still returned as `stage_posture`, and is asserted below to still BE the
    ladder — so this test fails if the two are ever quietly collapsed into one.
    """
    depths = [round(0.25 * i, 2) for i in range(2, 81)]      # 0.5 .. 20.0 in
    wets = [0.0, 0.15, 0.25, 0.4, 0.5, 0.6, 0.75, 0.9, 1.0]
    bad, hit = [], set()
    for bid in cwm_model.ORDER:
        if cwm_model.BASINS[bid].get("rating") == "none":
            continue                      # the mouth: no creek rating, handled elsewhere
        for d in depths:
            for w in wets:
                m = cwm_model.assess(bid, d, w)
                want = flood_rating.assess(m["qp_raw"], bid)["posture"]
                if m["posture"] == want:
                    continue
                if bid in FRONT_END_DECLARED:
                    hit.add(bid)
                    continue
                bad.append(f"{bid} rain={d} w={w}: cwm_model.assess "
                           f"{m['posture']} != flood_rating {want}")
    test_design_storm_front_end_agrees_with_the_authoritative_engine.hit = hit
    assert not bad, (
        f"The design-storm front end disagrees with the authoritative engine on "
        f"{len(bad)} of {len(depths)*len(wets)*7} storms:\n  " + "\n  ".join(bad[:10])
        + ("\n  ... and more" if len(bad) > 10 else "")
        + "\n\nAnything reading cwm_model.assess()['posture'] is reading a posture the "
          "deployed system would not issue.")

    # and the ladder must remain available, and remain the ladder
    m = cwm_model.assess("CC-COX-097", 6.0, 0.5)
    assert "stage_posture" in m, "stage_posture was dropped; the cross-check is gone"
    assert m["stage_posture"] == cwm_model.posture(m["stage"], "CC-COX-097"), (
        "stage_posture is no longer the stage-ladder answer — the two postures have "
        "been collapsed, and the cross-check that catches a bad rating is gone.")


def test_campus_stays_stage_classified():
    assert cwm_model.STAGE_BASED == {"CC-WCU-2260"}, (
        f"STAGE_BASED is {cwm_model.STAGE_BASED}. The campus is the only reach with a "
        "field-validated ladder (7/9/11 ft, '11 ft = water in the road'). Moving any "
        "other reach onto stage puts it on the rectangular Manning rating, which "
        "collapses above bankfull — the exact thing flood_rating §2 corrected.")
    assert basins.BASINS["CC-WCU-2260"]["thr_src"].startswith("VALIDATED")


def test_the_1p5_watch_is_shipped_in_all_three():
    """Shipped 2026-08-13. This is now a REGRESSION guard, not a gap report.

    Behavioural, not textual: it asserts what each engine DOES at RP 1.7 -- above
    1.5, below 2.0 -- which is the only band where the rule is visible. A textual
    check would pass on a classifier that mentions 1.5 and never reaches it."""
    pages = classifier_pages()

    assert flood_rating.WATCH_1_5YR == {"CC-COX-097", "CC-LB-171"}, (
        f"flood_rating.WATCH_1_5YR is {flood_rating.WATCH_1_5YR}. Changing which "
        "reaches get the early WATCH is a safety decision; record it before editing.")
    assert cwm_model.WATCH_1_5YR == flood_rating.WATCH_1_5YR, (
        f"cwm_model {cwm_model.WATCH_1_5YR} != flood_rating {flood_rating.WATCH_1_5YR}")
    for fn, src in pages:
        _, _c, lset = _live_cat_from_rp(src)
        assert lset == flood_rating.WATCH_1_5YR, (
            f"{fn} {sorted(lset)} != flood_rating {sorted(flood_rating.WATCH_1_5YR)} "
            "-- that page applies the early WATCH to a different set of basins.")

    for bid in flood_rating.WATCH_1_5YR:
        assert flood_rating.category_from_rp(1.7, bid) == "WATCH", bid
        assert cwm_model._cat_from_rp(1.7, bid) == "WATCH", (
            f"cwm_model is NORMAL at RP 1.7 on {bid} -- the 1.5-yr WATCH regressed.")
        for fn, src in pages:
            _, cuts, lset = _live_cat_from_rp(src)
            assert _live_posture(1.7, cuts, bid, lset) == "WATCH", (
                f"{fn} is NORMAL at RP 1.7 on {bid} -- the 1.5-yr WATCH regressed.")

    for bid in (b for b in FREQ_CLASSIFIED if b not in flood_rating.WATCH_1_5YR):
        assert flood_rating.category_from_rp(1.7, bid) == "NORMAL", bid
        assert cwm_model._cat_from_rp(1.7, bid) == "NORMAL", (
            f"{bid} got the early WATCH and is not in WATCH_1_5YR -- the rule leaked.")
        for fn, src in pages:
            _, cuts, lset = _live_cat_from_rp(src)
            assert _live_posture(1.7, cuts, bid, lset) == "NORMAL", f"{fn} {bid}"


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            fails += 1
            print(f"FAIL  {name}\n      {e}")
    print(f"\n{fails} failure(s)")
    # consistency-tests.yml greps this exact header into the job summary, so that a
    # green run cannot read as "nothing left to fix". It must therefore print
    # whenever ANY declaration is open — the header used to be gated on DECLARED
    # alone, and DECLARED is empty since the 1.5-yr WATCH shipped, so a live gap
    # declared anywhere else would have been silent on every run.
    if DECLARED or FRONT_END_DECLARED:
        print("\nOPEN, DECLARED, NOT FIXED — these are live gaps, not clean bills:")
        for (rule, bid), why in sorted(DECLARED.items()):
            print(f"  * {rule} / {bid}\n      {why}")
        for bid, why in sorted(FRONT_END_DECLARED.items()):
            print(f"  * FRONT_END / {bid}\n      {why}")
    raise SystemExit(1 if fails else 0)
