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


DA_WAIVED = {
    "CC-UP-503": "calib=(1.449,0.815) was fitted against DA=5.35; changing DA is a "
                 "re-fit, not an edit. See noah_system_audit_2026-08-11.md H2.",
}


def test_drainage_area_matches_registry():
    bad = []
    for bid, eng in cwm_model.BASINS.items():
        reg = basins.BASINS[bid]
        if abs(reg["da_sqmi"] - eng["DA"]) > 0.05 and bid not in DA_WAIVED:
            bad.append(f"{bid}: registry {reg['da_sqmi']} != engine {eng['DA']}")
    assert not bad, ("Undeclared drainage-area divergence:\n  " + "\n  ".join(bad) +
                     "\nIf intentional, add it to DA_WAIVED with the reason.")


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
