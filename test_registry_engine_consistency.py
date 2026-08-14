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
import sys

import basins
import cwm_model

HERE = os.path.dirname(os.path.abspath(__file__))
LIVE_HTML = os.path.join(HERE, "live.html")


def engine_html():
    """Every page at the repo root that carries its OWN copy of the BASINS literal.

    Checking `live.html` by name was not enough. On 2026-08-13 `rain_to_trip.html` —
    the page that answers "how much rain trips a WATCH here?" — was found still on
    DA 5.35 / calib (1.449, 0.815) for CC-UP-503 and still on a flat 2-yr WATCH
    cutoff, four weeks after the 1.5-yr WATCH was approved and hours after both were
    fixed in live.html. It had never been checked by anything, because nothing
    looked past the one filename.

    So this DISCOVERS engine copies instead of naming them, and a sixth copy is
    caught the day it appears rather than the day someone happens to look.
    """
    out = []
    for fn in sorted(os.listdir(HERE)):
        if not fn.endswith(".html"):
            continue
        path = os.path.join(HERE, fn)
        try:
            src = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        if re.search(r'"CC-[A-Z]+-\d+"\s*:\s*\{[^}]*\bDA\s*:', src):
            out.append((fn, src))
    return out


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


def test_html_thresholds_match_registry():
    """Every page carrying its own copy of the ladder. They drift too."""
    bad = []
    for fn, html in engine_html():
        for bid, reg in basins.BASINS.items():
            if reg["thr_ft"] is None:
                continue
            m = re.search(r'"' + re.escape(bid) + r'":\s*\{.{0,400}?thr:\[([0-9.,\s]+)\]',
                          html, re.S)
            if not m:
                bad.append(f"{fn}: {bid}: no thr:[...] found")
                continue
            got = tuple(round(float(x), 3) for x in m.group(1).split(","))
            want = tuple(round(float(x), 3) for x in reg["thr_ft"])
            if got != want:
                bad.append(f"{fn}: {bid}: {got} != registry {want}")
    assert not bad, ("HTML thresholds disagree with the registry:\n  "
                     + "\n  ".join(bad))


def test_at_least_one_engine_html_was_found():
    """If the discovery regex stops matching, every HTML check above passes
    vacuously and the drift they exist to catch becomes invisible."""
    found = [fn for fn, _ in engine_html()]
    assert "live.html" in found, (
        f"engine_html() did not find live.html — it found {found}. The BASINS-literal "
        "pattern has stopped matching, so every HTML check in this file is now "
        "asserting nothing.")


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
    pages = engine_html()
    bad = []
    for bid, eng in cwm_model.BASINS.items():
        reg = basins.BASINS[bid]
        if bid in DA_WAIVED:
            continue
        if abs(reg["da_sqmi"] - eng["DA"]) > 0.05:
            bad.append(f"{bid}: registry {reg['da_sqmi']} != cwm_model {eng['DA']}")
        for fn, html in pages:
            lv = _live_basin_field(html, bid, "DA")
            if lv is None:
                bad.append(f"{fn}: {bid}: no DA found")
            elif abs(reg["da_sqmi"] - lv) > 0.05:
                bad.append(f"{fn}: {bid}: registry {reg['da_sqmi']} != {lv}")
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
    pages = engine_html()
    bad = []
    for bid, eng in cwm_model.BASINS.items():
        reg = basins.BASINS[bid].get("calib")
        e = eng.get("calib")
        if reg is None or e is None:
            bad.append(f"{bid}: registry={reg} cwm_model={e} (one is missing)")
            continue
        if tuple(round(x, 4) for x in reg) != tuple(round(x, 4) for x in e):
            bad.append(f"{bid}: registry {tuple(reg)} != cwm_model {tuple(e)}")
        for fn, html in pages:
            lv = _live_basin_field(html, bid, "calib", arity=2)
            if lv is None:
                bad.append(f"{fn}: {bid}: no calib found")
            elif tuple(round(x, 4) for x in reg) != tuple(round(x, 4) for x in lv):
                bad.append(f"{fn}: {bid}: registry {tuple(reg)} != {lv}")
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


def test_ledger_wetness_matches_wetness_py():
    """`ledger/fetch_stage.py` carries its OWN copy of the API -> wetness transform.

    That copy is deliberate, and the reason is written above it in that file: the
    collector's one job is to never miss a sample, and an import of a repo-root
    module that fails on a runner costs a row. `mrms_live` is imported lazily and
    degrades to an empty column; wetness cannot degrade, because without it there
    is no model row at all.

    But a second copy of a safety-relevant number is exactly what cost this project
    four weeks on the 1.5-yr WATCH and eight days on the LiDAR ladders. A copy is
    only safe while something compares it. This is that something.

    Wetness is the most load-bearing state variable in the chain: it sets CN, which
    sets runoff, which sets the peak, which sets the posture. The ledger is the
    record the system will be SCORED against, so a ledger whose wetness drifted
    from the engines' would score the system against a model it never ran.

    EXACT equality is demanded, not a tolerance. Both sides are the same closed
    form on the same inputs; any difference at all is a divergence, not rounding.
    """
    sys.path.insert(0, os.path.join(HERE, "ledger"))
    import wetness
    import fetch_stage

    assert wetness.API_5DAY_EQUIV == fetch_stage.API_5DAY_EQUIV, (
        f"API_5DAY_EQUIV: wetness.py {wetness.API_5DAY_EQUIV!r} != "
        f"ledger {fetch_stage.API_5DAY_EQUIV!r}")
    assert (wetness.API_K == fetch_stage.API_K
            and wetness.API_DAYS == fetch_stage.API_DAYS), (
        f"decay constants differ: wetness.py ({wetness.API_K}, {wetness.API_DAYS}) "
        f"!= ledger ({fetch_stage.API_K}, {fetch_stage.API_DAYS})")

    series = [0.0, 0.31, 1.2, 0.0, 0.05, 2.4, 0.0, 0.9, 0.0, 0.0, 3.1]
    assert wetness.api_from_daily(series) == fetch_stage.api_from_daily(series), (
        f"api_from_daily diverged: wetness.py {wetness.api_from_daily(series)!r} "
        f"!= ledger {fetch_stage.api_from_daily(series)!r}")

    bad = []
    for month in range(1, 13):          # both seasons, and both breakpoints
        for i in range(0, 1201):        # API 0.00 .. 12.00 in, 0.01 steps
            api = i / 100.0
            a = wetness.wetness_from_api(api, month)
            b = fetch_stage.wetness_from_api(api, month)
            if a != b:
                bad.append(f"month {month:>2}  API {api:5.2f} in  "
                           f"wetness.py {a!r} != ledger {b!r}")
    assert not bad, (
        f"The ledger's wetness transform has drifted from wetness.py in "
        f"{len(bad)} of 14412 sampled points:\n  " + "\n  ".join(bad[:8])
        + ("\n  ... and more" if len(bad) > 8 else "")
        + "\n\nEvery decision row logged since the drift carries a wetness the "
          "engines would not have computed. Fix the copy in ledger/fetch_stage.py, "
          "or delete it and accept the import risk — but do not leave them unequal.")


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
