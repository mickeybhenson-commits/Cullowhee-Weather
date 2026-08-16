#!/usr/bin/env python3
"""
verify_browser_engines.py — run the JavaScript the public actually executes.

    python verify_browser_engines.py

WHY THIS EXISTS
---------------
The flood engine exists three times: `cwm_model.py`, and again in JavaScript embedded in
`live.html` and `rain_to_trip.html` so the public map computes postures in the visitor's
browser with nothing running server-side. That is a good property. What it costs is that
the code most people actually run is the code least tested.

Until 2026-08-16 **nothing in this repo had ever executed either JS engine.** The existing
controls both stop short of it:

  * `test_registry_engine_consistency.py` compares the NUMBERS embedded in the HTML — thr,
    DA, calib — against `basins.py`. Stored constants, not computed answers. Every literal
    can be correct while the function using them folds.
  * `test_design_storm_front_end_agrees_with_the_authoritative_engine` sweeps 4,977 storms,
    but between `cwm_model.assess` and `flood_rating.assess` — two PYTHON engines. It never
    loads a page.

So the browser copies were verified by inspection of their inputs and by nothing else. This
loads each page in headless Chromium, calls its own `assessBasin(bid, qpf, wetness)`, and
asserts two things across the full sweep:

  1. MONOTONICITY — more rain, or wetter ground, must never lower the posture. This is the
     invariant `posture_rules.check_monotone` pins on the Python side and cannot reach here.
  2. AGREEMENT — the browser must reach the same posture as `cwm_model.assess` for the same
     storm. A fold present in BOTH Python and JS would pass a Python-only test; this is the
     only check that would still catch it, because monotonicity is asserted independently.

First run, 2026-08-16: 7,040 evaluations across both pages, 0 reversals, 0 disagreements.

WHY IT IS NOT A CI SUITE
------------------------
It needs Chromium. Adding Node and a ~150 MB browser download to a Python-only workflow is a
real cost for a check that has so far found nothing, and `.github/workflows/` is protected
against remote writes besides. So it is a workstation tool with a clear trigger:

    RUN THIS AFTER TOUCHING EITHER ENGINE HTML — in particular after
    `sync_engine_html.py --write`, which edits those files by design.

If that trigger is not honoured this becomes another unrun control, which is the failure
this repo keeps finding. Promoting it to CI is one workflow step plus a browser install.

SAFETY
------
Every external request is BLOCKED. The engine is pure arithmetic and must not need the
network; if a page cannot compute a posture offline, that is itself worth knowing.
`live.html` raises `ReferenceError: L is not defined` under that blocking because Leaflet is
CDN-hosted — expected and harmless: function declarations in earlier script blocks are
already defined, which is exactly what this calls.

A page where `assessBasin` is missing is a FAILURE, not a skip. So is finding no engine HTML
at all. A silent skip here would report "browser engines fine" while checking nothing.

Requires: pip install playwright  (the Chromium binary comes from PLAYWRIGHT_BROWSERS_PATH
or `playwright install chromium`).
"""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

RANK = {"N/A": 0, "NORMAL": 1, "WATCH": 2, "WARNING": 3, "EMERGENCY": 4}

# Sweeps mirror test_posture_rules.py's Python-side monotonicity tests exactly, so the two
# can never disagree about what was covered.
RAIN_LO, RAIN_HI, RAIN_STEP = 0.1, 12.0, 0.05
WET_LO, WET_HI, WET_STEP = 0.0, 1.0, 0.005

SWEEP_JS = """
(cfg) => {
  if (typeof assessBasin !== 'function') return {fatal: 'assessBasin is not defined'};
  if (typeof BASINS !== 'object' || !BASINS) return {fatal: 'BASINS is not defined'};
  const out = {rain: {}, wet: {}, basins: Object.keys(BASINS)};
  for (const bid of out.basins) {
    out.rain[bid] = []; out.wet[bid] = [];
    for (let P = cfg.rlo; P <= cfg.rhi + 1e-9; P = +(P + cfg.rstep).toFixed(6))
      out.rain[bid].push([+P.toFixed(4), assessBasin(bid, P, 0.5).posture]);
    for (let w = cfg.wlo; w <= cfg.whi + 1e-9; w = +(w + cfg.wstep).toFixed(6))
      out.wet[bid].push([+w.toFixed(4), assessBasin(bid, 5.0, w).posture]);
  }
  return out;
}
"""


def engine_html():
    """Every page at the repo root carrying its own BASINS literal — discovered, not
    named, matching sync_engine_html.engine_html(). Naming live.html by hand is how
    rain_to_trip.html went unchecked for ten days."""
    out = []
    for fn in sorted(os.listdir(HERE)):
        if not fn.endswith(".html"):
            continue
        try:
            with open(os.path.join(HERE, fn), "rb") as f:
                raw = f.read()
        except OSError:
            continue
        if re.search(rb'"CC-[A-Z]+-\d+"\s*:\s*\{[^}]*\bDA\s*:', raw):
            out.append(fn)
    return out


def sweep(page_file):
    from playwright.sync_api import sync_playwright
    url = "file://" + os.path.join(HERE, page_file)
    with sync_playwright() as pw:
        exe = os.environ.get("CHROMIUM_PATH")
        b = pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()
        try:
            ctx = b.new_context()
            # Pure arithmetic must not need the network.
            ctx.route("**", lambda r: r.continue_() if r.request.url.startswith("file:")
                      else r.abort())
            page = ctx.new_page()
            errs = []
            page.on("pageerror", lambda e: errs.append(str(e).split("\n")[0][:90]))
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(700)
            res = page.evaluate(SWEEP_JS, {
                "rlo": RAIN_LO, "rhi": RAIN_HI, "rstep": RAIN_STEP,
                "wlo": WET_LO, "whi": WET_HI, "wstep": WET_STEP})
            return res, errs
        finally:
            b.close()


def reversals(series):
    bad, prev = [], None
    for x, p in series:
        if prev is not None and RANK.get(p, 0) < RANK.get(prev[1], 0):
            bad.append((prev, (x, p)))
        prev = (x, p)
    return bad


def main():
    try:
        import playwright  # noqa: F401
    except ImportError:
        raise SystemExit(
            "playwright is not installed, so the browser engines were NOT checked.\n"
            "Exiting non-zero on purpose: a silent skip would report the deployed\n"
            "JavaScript as fine while running none of it.\n\n"
            "    pip install playwright && playwright install chromium")

    import cwm_model                                        # noqa: E402

    pages = engine_html()
    if not pages:
        raise SystemExit(
            "no engine-bearing HTML found — the BASINS-literal pattern has stopped "
            "matching, so this tool is silently a no-op. Fix the pattern; do not "
            "ignore this.")

    print("=" * 82)
    print("BROWSER ENGINE VERIFICATION — running the JavaScript the public executes")
    print("=" * 82)
    total_rev = total_dis = total_n = 0

    for fn in pages:
        res, errs = sweep(fn)
        if res.get("fatal"):
            raise SystemExit(f"{fn}: {res['fatal']} — the page carries a BASINS literal "
                             f"but no callable engine. Either the entry point was "
                             f"renamed (update SWEEP_JS) or this page is not what it "
                             f"looks like. Not skipping.")
        nrev = ndis = n = 0
        shown = []
        for kind, py in (("rain", lambda b, x: cwm_model.assess(b, x, 0.5)["posture"]),
                         ("wet", lambda b, x: cwm_model.assess(b, 5.0, x)["posture"])):
            for bid, series in res[kind].items():
                n += len(series)
                for prev, cur in reversals(series):
                    nrev += 1
                    if len(shown) < 5:
                        shown.append(f"REVERSAL {bid} {kind}: {prev[0]:g} -> {prev[1]}, "
                                     f"but {cur[0]:g} -> {cur[1]}")
                for x, p in series:
                    q = py(bid, x)
                    if q != p:
                        ndis += 1
                        if len(shown) < 5:
                            shown.append(f"DISAGREE {bid} {kind}={x:g}: browser {p}, "
                                         f"cwm_model {q}")
        total_rev += nrev
        total_dis += ndis
        total_n += n
        flag = "ok" if not (nrev or ndis) else "FAIL"
        print(f"\n  {flag}  {fn}")
        print(f"        {n:,} evaluations over {len(res['basins'])} basins")
        print(f"        monotonicity reversals            : {nrev}")
        print(f"        disagreements with cwm_model      : {ndis}")
        if errs:
            print(f"        page errors (expected, CDN blocked): {errs[0]}")
        for s in shown:
            print(f"        {s}")

    print()
    print("=" * 82)
    if total_rev or total_dis:
        print(f"{total_rev} reversal(s) and {total_dis} disagreement(s) in "
              f"{total_n:,} evaluations.")
        print("The browser is what the public runs. Fix the JS, or fix Python and")
        print("re-propagate — but do not assume the Python answer is the deployed one.")
        return 1
    print(f"{total_n:,} evaluations across {len(pages)} page(s): monotone everywhere, "
          f"and identical to cwm_model.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
