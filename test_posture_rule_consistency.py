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
              -> cwm_model._cat_from_rp() and live.html catFromRP() do not take a
              basin argument at all, so they CANNOT express it. Still open.

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
DECLARED = {
    ("WATCH_1_5YR", "CC-COX-097"): (
        "flood_rating drops WATCH to 1.5-yr (approved 2026-07-15); "
        "cwm_model/live.html classifiers take no basin argument and cannot "
        "express it. UNDER-WARNING on a 29-minute basin. "
        "CLOSES WHEN: catFromRP/_cat_from_rp gain a bid parameter."),
    ("WATCH_1_5YR", "CC-LB-171"): (
        "as CC-COX-097; 36-minute basin. "
        "CLOSES WHEN: catFromRP/_cat_from_rp gain a bid parameter."),
}

# Return periods to sweep. Dense around every cutoff that exists in any engine.
SWEEP = ([0.5, 1.0, 1.2, 1.4, 1.49, 1.5, 1.51, 1.6, 1.8, 1.9, 1.99, 2.0, 2.01, 2.5]
         + [3, 5, 8, 9.5, 9.99, 10.0, 10.01, 12, 25, 50, 90, 99.9, 100.0, 100.1, 200])

FREQ_CLASSIFIED = [b for b in cwm_model.ORDER if b not in cwm_model.STAGE_BASED]


def _live_cat_from_rp():
    """Extract live.html's catFromRP cutoffs so the JS is checked, not assumed."""
    with open(LIVE_HTML, encoding="utf-8", errors="replace") as f:
        src = f.read()
    m = re.search(r"function\s+catFromRP\s*\(([^)]*)\)\s*\{(.*?)\}", src, re.S)
    assert m, "catFromRP not found in live.html"
    args, body = m.group(1), m.group(2)
    takes_bid = len([a for a in args.split(",") if a.strip()]) > 1
    cuts = {}
    for level in ("EMERGENCY", "WARNING", "WATCH"):
        c = re.search(r"T\s*>=\s*([0-9.]+)\s*\)\s*return\s*\"" + level + r"\"", body)
        assert c, f"cutoff for {level} not found in catFromRP"
        cuts[level] = float(c.group(1))
    return takes_bid, cuts, body


def _live_posture(T, cuts):
    if T is None:
        return "N/A"
    if T >= cuts["EMERGENCY"]:
        return "EMERGENCY"
    if T >= cuts["WARNING"]:
        return "WARNING"
    if T >= cuts["WATCH"]:
        return "WATCH"
    return "NORMAL"


# --------------------------------------------------------------------------- #
def test_live_html_classifier_is_parseable():
    takes_bid, cuts, _ = _live_cat_from_rp()
    assert cuts["EMERGENCY"] == 100.0, f"live.html EMERGENCY cutoff is {cuts['EMERGENCY']}"
    assert cuts["WARNING"] == 10.0, f"live.html WARNING cutoff is {cuts['WARNING']}"
    assert cuts["WATCH"] in (1.5, 2.0), f"live.html WATCH cutoff is {cuts['WATCH']}"


def test_classifiers_agree_or_are_declared():
    """The load-bearing check. Sweep every RP against all three engines."""
    takes_bid, cuts, _ = _live_cat_from_rp()
    undeclared, declared_hit = [], set()
    for bid in FREQ_CLASSIFIED:
        for T in SWEEP:
            fr = flood_rating.category_from_rp(T, bid)
            cw = cwm_model._cat_from_rp(T)
            lv = _live_posture(T, cuts)
            if fr == cw == lv:
                continue
            key = ("WATCH_1_5YR", bid)
            if key in DECLARED and {fr, cw, lv} <= {"NORMAL", "WATCH"}:
                declared_hit.add(key)
                continue
            undeclared.append(
                f"{bid} RP={T}: flood_rating={fr} cwm_model={cw} live.html={lv}")
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


def test_campus_stays_stage_classified():
    assert cwm_model.STAGE_BASED == {"CC-WCU-2260"}, (
        f"STAGE_BASED is {cwm_model.STAGE_BASED}. The campus is the only reach with a "
        "field-validated ladder (7/9/11 ft, '11 ft = water in the road'). Moving any "
        "other reach onto stage puts it on the rectangular Manning rating, which "
        "collapses above bankfull — the exact thing flood_rating §2 corrected.")
    assert basins.BASINS["CC-WCU-2260"]["thr_src"].startswith("VALIDATED")


def test_the_unshipped_rule_is_visible_not_silent():
    """The 1.5-yr WATCH is approved and not deployed. Keep that loud."""
    assert flood_rating.WATCH_1_5YR == {"CC-COX-097", "CC-LB-171"}, (
        "flood_rating.WATCH_1_5YR changed. If the set was edited, the DECLARED "
        "entries above and noah_watch_1p5_never_shipped_2026-08-12.md need updating.")
    for bid in flood_rating.WATCH_1_5YR:
        assert flood_rating.category_from_rp(1.7, bid) == "WATCH"
        assert cwm_model._cat_from_rp(1.7) == "NORMAL", (
            "cwm_model now agrees at RP 1.7 — the 1.5-yr WATCH may have shipped. "
            "If so, clear DECLARED and delete this assertion.")


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
    if not fails and DECLARED:
        print("\nOPEN, DECLARED, NOT FIXED — these are live gaps, not clean bills:")
        for (rule, bid), why in sorted(DECLARED.items()):
            print(f"  * {rule} / {bid}\n      {why}")
    raise SystemExit(1 if fails else 0)
