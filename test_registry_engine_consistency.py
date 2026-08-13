"""
test_registry_engine_consistency.py — the registry and the deployed engine must agree.

`basins.py` is the source of truth for provenance: it records WHERE each number came
from (`thr_src`, `da_src`, `tc_src`). `cwm_model.py` and the `live.html` BASINS literal
are what actually run. On 2026-08-03 five reaches gained SURVEYED LiDAR threshold
ladders in the registry; nobody propagated them, and the divergence went unnoticed for
eight days because those thresholds are inert while only the campus classifies by
stage. They stop being inert the moment a LoRa stage node reports.

This test exists so that gap cannot open again silently.

    python -m pytest test_registry_engine_consistency.py -v
    python test_registry_engine_consistency.py        # also works, no pytest needed
"""
import os
import re

import basins
import cwm_model

HERE = os.path.dirname(os.path.abspath(__file__))
LIVE_HTML = os.path.join(HERE, "live.html")


def test_thresholds_match_registry():
    bad = []
    for bid, eng in cwm_model.BASINS.items():
        reg = basins.BASINS[bid]
        r, e = reg["thr_ft"], eng.get("thr")
        if r is None and e is None:
            continue
        if r is None or e is None:
            bad.append(f"{bid}: registry={r} engine={e} (one is None)")
            continue
        if tuple(round(x, 3) for x in r) != tuple(round(x, 3) for x in e):
            bad.append(f"{bid}: registry {tuple(r)} != engine {tuple(e)}"
                       f"   [{reg['thr_src'][:38]}]")
    assert not bad, (
        "Deployed thresholds disagree with the registry:\n  " + "\n  ".join(bad) +
        "\n\nThese become the Confirmation-tier trigger once stage sensors report."
    )


def test_live_html_thresholds_match_registry():
    """The public map carries its own copy of the ladder. It drifts too."""
    if not os.path.exists(LIVE_HTML):
        return
    html = open(LIVE_HTML, encoding="utf-8", errors="replace").read()
    bad = []
    for bid, reg in basins.BASINS.items():
        if reg["thr_ft"] is None:
            continue
        m = re.search(r'"' + re.escape(bid) + r'":\s*\{.{0,400}?thr:\[([0-9.,\s]+)\]',
                      html, re.S)
        if not m:
            bad.append(f"{bid}: no thr:[...] found in live.html")
            continue
        got = tuple(round(float(x), 3) for x in m.group(1).split(","))
        want = tuple(round(float(x), 3) for x in reg["thr_ft"])
        if got != want:
            bad.append(f"{bid}: live.html {got} != registry {want}")
    assert not bad, ("live.html thresholds disagree with the registry:\n  "
                     + "\n  ".join(bad))


# EMPTY since 2026-08-13. The single entry here waived CC-UP-503, whose DA was
# corrected 5.35 -> 5.03 in basins.py and cwm_model.py while calib stayed fitted
# against 5.35 -- a 4.90% UNDER-read on the largest incremental area in the
# watershed, and live.html still carried DA:5.35 outright. The refit closed it.
# A waiver is not a place to park a known under-read indefinitely.
DA_WAIVED = {}


def _live_basin_field(html, bid, field, arity=1):
    """Pull DA / Tc / calib straight out of live.html's BASINS literal.
    The literal is what the public map RUNS; a value that agrees in basins.py and
    cwm_model.py and not here is still wrong on the page people look at."""
    m = re.search(r'"' + re.escape(bid) + r'":\s*\{[^}]*?' + field +
                  (r':\s*\[([0-9.]+)\s*,\s*([0-9.]+)\]' if arity == 2
                   else r':\s*([0-9.]+)'), html)
    if not m:
        return None
    return (float(m.group(1)), float(m.group(2))) if arity == 2 else float(m.group(1))


def test_drainage_area_matches_registry():
    """Registry vs BOTH engines. live.html was unchecked until 2026-08-13, and it
    was the one place CC-UP-503 still carried the old 5.35."""
    if os.path.exists(LIVE_HTML):
        html = open(LIVE_HTML, encoding="utf-8", errors="replace").read()
    else:
        html = ""
    bad = []
    for bid, eng in cwm_model.BASINS.items():
        reg = basins.BASINS[bid]
        if bid in DA_WAIVED:
            continue
        if abs(reg["da_sqmi"] - eng["DA"]) > 0.05:
            bad.append(f"{bid}: registry {reg['da_sqmi']} != cwm_model {eng['DA']}")
        if html:
            lv = _live_basin_field(html, bid, "DA")
            if lv is None:
                bad.append(f"{bid}: no DA found in live.html")
            elif abs(reg["da_sqmi"] - lv) > 0.05:
                bad.append(f"{bid}: registry {reg['da_sqmi']} != live.html {lv}")
    assert not bad, ("Undeclared drainage-area divergence:\n  " + "\n  ".join(bad) +
                     "\nIf intentional, add it to DA_WAIVED with the reason -- and note "
                     "that changing DA without refitting calib is an under-read, not an edit.")


def test_da_waivers_are_still_real():
    """A waiver that outlived its cause reads as reviewed while guarding nothing."""
    stale = [b for b in DA_WAIVED
             if abs(basins.BASINS[b]["da_sqmi"] - cwm_model.BASINS[b]["DA"]) <= 0.05]
    assert not stale, (f"DA_WAIVED entries whose divergence is gone: {stale}. "
                       "Delete them so the test starts guarding the fix.")


def test_calib_matches_across_engines():
    """calib had NO cross-engine check at all until 2026-08-13. It is the single
    number that converts model peak to the regression scale, so a drift here moves
    every posture on that reach without moving anything visibly wrong."""
    html = (open(LIVE_HTML, encoding="utf-8", errors="replace").read()
            if os.path.exists(LIVE_HTML) else "")
    bad = []
    for bid, eng in cwm_model.BASINS.items():
        reg = basins.BASINS[bid].get("calib")
        e = eng.get("calib")
        if reg is None or e is None:
            bad.append(f"{bid}: registry={reg} cwm_model={e} (one is missing)")
            continue
        if tuple(round(x, 4) for x in reg) != tuple(round(x, 4) for x in e):
            bad.append(f"{bid}: registry {tuple(reg)} != cwm_model {tuple(e)}")
        if html:
            lv = _live_basin_field(html, bid, "calib", arity=2)
            if lv is None:
                bad.append(f"{bid}: no calib found in live.html")
            elif tuple(round(x, 4) for x in reg) != tuple(round(x, 4) for x in lv):
                bad.append(f"{bid}: registry {tuple(reg)} != live.html {lv}")
    assert not bad, ("Calibration disagrees across engines:\n  " + "\n  ".join(bad) +
                     "\n\ncalib maps model peak onto the StreamStats frequency curve. "
                     "A divergence here silently rescales every posture on that reach.")


def test_calib_still_reproduces_its_streamstats_anchors():
    """calib_anchors record the (model peak -> regression flow) pairs the power law
    was fitted through. If (a,b) no longer reproduces them, the pair was edited
    without refitting -- which is exactly what happened to CC-UP-503 when its DA
    was corrected in two places out of three."""
    bad = []
    for bid, reg in basins.BASINS.items():
        cal, anch = reg.get("calib"), reg.get("calib_anchors")
        if not cal or not anch:
            continue
        a, b = cal
        for model_q, reg_q in anch:
            got = a * model_q ** b
            err = (got - reg_q) / reg_q
            if abs(err) > 0.005:                      # 0.5%
                bad.append(f"{bid}: calib{tuple(cal)} on anchor {model_q} gives "
                           f"{got:.1f} cfs, anchor says {reg_q} ({err*100:+.2f}%)")
    assert not bad, ("Calibration no longer reproduces its own anchors:\n  "
                     + "\n  ".join(bad) +
                     "\n\nEither the pair was edited without a refit, or the anchors "
                     "were. Both are silent discharge errors.")


TC_KNOWN_DIVERGENT = {"CC-MS-1100", "CC-SPD-1830"}


def test_tc_divergence_is_declared():
    found = {bid for bid, eng in cwm_model.BASINS.items()
             if basins.BASINS[bid]["tc_min"] != eng["Tc"]}
    assert found == TC_KNOWN_DIVERGENT, (
        f"Tc divergence set changed.\n  expected: {sorted(TC_KNOWN_DIVERGENT)}"
        f"\n  found:    {sorted(found)}\n"
        "The calibration was fitted against the engine's values. If this is "
        "intentional, update the set and record why in the decision ledger — and note "
        "that resolving it is a model change that de-anchors calib for those basins."
    )


def test_surveyed_reaches_stay_surveyed():
    """Once a reach has a surveyed ladder, reverting it to arithmetic is a regression.
    Under the project standard a placeholder threshold is an unprotected reach."""
    SURVEYED_AS_OF_2026_08_03 = {
        "CC-MS-1100", "CC-TIL-705", "CC-SPD-1830", "CC-COX-097", "CC-LB-171",
    }
    lost = [b for b in SURVEYED_AS_OF_2026_08_03
            if not basins.BASINS[b]["thr_src"].startswith(("SURVEYED", "VALIDATED"))]
    assert not lost, (f"Reaches lost surveyed provenance: {lost}. "
                      "A placeholder threshold is an unprotected reach.")


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
    raise SystemExit(1 if fails else 0)
