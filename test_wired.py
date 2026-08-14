"""Is every finished component connected to anything that runs?

WHY THIS EXISTS
---------------
Every other test in this repo asks whether the code is CORRECT. By 2026-08-13 seven
separate components were found that were correct, careful, and **wired to nothing**:

  2026-07-15  the 1.5-yr WATCH, implemented in flood_rating.py, unreachable from the
              two deployed classifiers because neither took a basin argument
  2026-08-03  the surveyed LiDAR ladders, eight days in basins.py before the engines
  (undated)   test_registry_engine_consistency.py — correct on the day the drift
              appeared, never invoked by CI
  (undated)   mrms_live.py — complete basin-averaged QPE, passes its own self-test,
              imported by no module. WIRED 2026-08-13 by ledger/fetch_stage.py, which
              now logs a per-basin mrms_in beside the point-forecast rain_in. This
              test caught the stale waiver the moment that landed and demanded it be
              deleted — which is the whole mechanism working. Note the floor/guarantee
              distinction below: mrms_live is now MEASURED and logged, it is not yet
              FORCING. Reachability does not say a result is used.
  (undated)   the entire QPF-bias ledger — fetch_mrms.py, three forecast fetchers and
              bias_report.py, called by no workflow, with no database host to write to
  (undated)   confluence_panel.py and confluence_status.py — the BACKWATER model for
              the mouth, where the homes are, plus its console card. basins.py said the
              mouth "hands backwater to confluence_status" and flood_rating.py said the
              operational posture came from "confluence_status + live USGS
              03508050/TKRN7"; nothing imported either one, so that handoff had never
              happened. WIRED 2026-08-13 — streamlit_app.py renders the card, and both
              declarations were deleted because this test demanded it.

None of those were sloppy. Each has good internal discipline: bias_report refuses to
print a statistic with fewer than 8 paired windows, mrms_live omits a basin it cannot
measure rather than reporting zero, flood_rating stamps the mouth
confidence="creek-only (backwater not included here)" rather than pretending.
The failure is uniformly at the BOUNDARY.

A test suite catches code that is wrong. Nothing here caught code that was right and
idle. This is that control.

WHAT IT CHECKS
--------------
1. Every module at the repo root and in ledger/ is reachable from something that runs:
   a GitHub workflow, a Streamlit page, another reachable module, or a test.
2. Anything unreachable is DECLARED below with a reason and a closing condition.
3. A DECLARED entry that has since become reachable fails, so the waiver gets deleted
   rather than quietly outliving its cause.
4. Every workflow's python invocations name files that exist.
5. TOOLS and DECLARED name modules that still exist.

WHAT IT DOES NOT CHECK
----------------------
Whether a reachable module is reached on the LIVE path as opposed to a monthly batch
job, and whether the thing it computes is actually used downstream. mrms_live could be
imported by one line that discards the result and this test would pass. Reachability is
a floor, not a guarantee.

It also does not read python inside a workflow heredoc (`python - <<'PY'`). Today the
only one, in publish-feed.yml, imports json and sys and nothing from this repo. If a
heredoc ever imports a repo module, that module will look orphaned here.

ONE HONEST CAVEAT ABOUT "REACHABLE VIA A TEST"
----------------------------------------------
This test seeds the closure from every test_*.py, on the principle that a module a test
imports is at least exercised. But exercised is not deployed. A module whose ONLY
importer is a test computes nothing that any live path consumes — and as of 2026-08-13
no workflow runs any test here at all, so that seed is currently notional twice over.

Three modules are in that state and are printed on every run under TEST-ONLY REACHABLE:
posture_rules (the "absent corroboration must never downgrade a posture" rule, stated
once and imported by no engine), lead_time (§4 lead-limited flagging — Cox Branch Tc=29
against LEAD_REQ_MIN=120), and flood_ensemble (§3 posture distribution over QPF ±25%
and wetness ±0.15, i.e. whether a call is firm or marginal).

It is a report, not a failure — a module used only by tests can be legitimate — but it
must not be silent. A workflow invoking a test file does NOT move a module out of this
category: running the test is still not deploying the module.

    python test_wired.py

Exit 0 = every module is reachable or declared. Exit 1 = something is orphaned.
"""

import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG_DIRS = ["", "ledger"]
WORKFLOWS = os.path.join(HERE, ".github", "workflows")
PAGES = os.path.join(HERE, "pages")

# --------------------------------------------------------------------------- #
# Declared orphans. Each entry needs WHY it is not wired and WHAT WOULD CLOSE IT.
# An entry here does not mean "acceptable" — it means known and tracked. Delete it
# when the module gets wired; this test will tell you to.
DECLARED = {
    "ledger.fetch_mrms": (
        "Truth side of the QPF-bias ledger. Writes basin-mean MRMS into SQLite via "
        "ledger_db. No workflow calls it and no database host exists — .gitignore "
        "correctly excludes *.db and Actions runners are ephemeral, so a database made "
        "in a workflow dies with it. "
        "CLOSES WHEN: the ledger database has a persistent home. See "
        "noah_bias_ledger_never_ran_2026-08-12.md."),
    "ledger.bias_report": ("as ledger.fetch_mrms — consumes the same database."),
    "ledger.fetch_forecast": ("as ledger.fetch_mrms — forecast side of the same ledger."),
    "ledger.fetch_nws_qpf": ("as ledger.fetch_mrms."),
    "ledger.fetch_weathernext": ("as ledger.fetch_mrms."),
    "landuse_cn": (
        "Referenced only in two COMMENTS in merge_subbasins.py ('landuse_cn.py expects "
        "...', 'ready for landuse_cn.py') -- never imported. Either the CN-from-landcover "
        "path was superseded by the fixed CN2 values now in basins.py, or it is a step "
        "of the pipeline nobody runs. "
        "CLOSES WHEN: something imports it, or it is deleted and the two comments in "
        "merge_subbasins.py go with it."),
}

# Modules that are entry points by nature: a human runs them, or they are one-shot
# tools. Being unimported is correct for these, so they are not orphans.
#
# Curated by judgement, NOT by "has an if __name__ == '__main__' guard". That heuristic
# would be wrong in the direction that hides things: confluence_status, landuse_cn and
# mrms_live all carry a main guard for demo/self-test purposes, and all three are
# components meant to be CONSUMED, not run. Auto-trusting the guard would have silenced
# three of the nine declarations above.
TOOLS = {
    "backtest_helene", "helene_mrms_reconstruct", "helene_solve_wetness",
    "fetch_helene_forcing", "merge_subbasins", "bfe_to_thresholds",
    "cucn7_backfill", "noah_feed_check", "streamlit_app",
    "ledger.verify",        # argparse CLI: --status/--score/--propose/--selftest
}


# Every non-stdlib package this repo imports from outside itself. This is a MANIFEST,
# not a convenience: anything imported that is neither stdlib, nor a module in this repo,
# nor named here is presumed to be a repo module THAT HAS GONE MISSING, and the test says
# so. Adding a real dependency costs one line, which is the point — a new external
# dependency should be a visible decision, not a silent one.
#
# It is deliberately wider than requirements.txt. requirements.txt is what the Streamlit
# app needs to boot; the geospatial and GRIB names below belong to batch and analysis
# tools that are expected to be absent on a bare runner and guard their own imports.
THIRD_PARTY = {
    # requirements.txt (the console)
    "streamlit", "streamlit_autorefresh", "streamlit_folium", "folium",
    "requests", "pandas", "numpy", "plotly", "pydeck",
    "google",                       # google-cloud-firestore
    # batch / analysis tools, optional by design
    "geopandas", "rasterio", "shapely", "shapefile", "pyproj", "scipy", "xarray",
    "eccodes", "pystac_client", "planetary_computer",
}


def _modules():
    """{modname: path} for every module this test governs."""
    out = {}
    for d in PKG_DIRS:
        root = os.path.join(HERE, d) if d else HERE
        if not os.path.isdir(root):
            continue
        for fn in sorted(os.listdir(root)):
            if not fn.endswith(".py") or fn.startswith("_"):
                continue
            name = fn[:-3]
            out[f"{d}.{name}" if d else name] = os.path.join(root, fn)
    return out


def _imports(path):
    """Candidate module names imported by this file, however deeply nested the
    import statement is. Deliberately includes imports inside functions and inside
    try/except: a deferred import is still a wiring.

    Emits BOTH forms, because the ledger is a package and can be wired either way:

        import mrms_live                    -> "mrms_live"
        from ledger.fetch_mrms import x     -> "ledger", "ledger.fetch_mrms"
        from ledger import fetch_mrms       -> "ledger", "ledger.fetch_mrms"

    Resolution rules differ by form (see _resolve): a bare name matches any module
    with that short name; a DOTTED name must match a module path exactly. That
    asymmetry is deliberate — `from basins import lead_time` would otherwise be read
    as wiring the lead_time MODULE when it is really importing a symbol that happens
    to share the name."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            tree = ast.parse(f.read())
    except SyntaxError:
        return set()
    got = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                got.add(a.name.split(".")[0])
                if "." in a.name:
                    got.add(a.name)
        elif isinstance(n, ast.ImportFrom):
            if n.module:
                got.add(n.module.split(".")[0])
                if "." in n.module:
                    got.add(n.module)
                for a in n.names:               # from ledger import fetch_mrms
                    if a.name != "*":
                        got.add(f"{n.module}.{a.name}")
    return got


def _resolve(cand, mods, by_short):
    """Module names a single import candidate could refer to."""
    if "." in cand:
        return [cand] if cand in mods else []
    return by_short.get(cand, [])


def _workflow_entrypoints():
    """{script path as written in the yml} across every workflow."""
    out = set()
    if not os.path.isdir(WORKFLOWS):
        return out
    for fn in sorted(os.listdir(WORKFLOWS)):
        if not fn.endswith((".yml", ".yaml")):
            continue
        with open(os.path.join(WORKFLOWS, fn), encoding="utf-8", errors="replace") as f:
            src = f.read()
        for m in re.finditer(r"python[3]?\s+([A-Za-z0-9_./-]+\.py)", src):
            out.add(m.group(1))
    return out


def _by_short(mods):
    d = {}
    for name in mods:
        d.setdefault(name.split(".")[-1], []).append(name)
    return d


def _closure(mods, by_short, seed):
    seen, stack = set(), list(seed)
    while stack:
        cur = stack.pop()
        if cur in seen or cur not in mods:
            continue
        seen.add(cur)
        for imp in _imports(mods[cur]):
            for cand in _resolve(imp, mods, by_short):
                if cand not in seen:
                    stack.append(cand)
    return seen


def _seeds(mods, by_short, include_tests=True):
    seed = set()
    for script in _workflow_entrypoints():
        stem = os.path.splitext(script)[0].replace("/", ".").replace("\\", ".")
        if not include_tests and stem.split(".")[-1].startswith("test_"):
            continue        # a workflow invoking a test is still only a test
        if stem in mods:
            seed.add(stem)
        else:                                   # "ledger/fetch_stage.py" -> ledger.fetch_stage
            for cand in by_short.get(stem.split(".")[-1], []):
                seed.add(cand)
    for name in mods:                           # human-run tools are entry points
        if name in TOOLS or name.split(".")[-1] in TOOLS:
            seed.add(name)
        if include_tests and name.split(".")[-1].startswith("test_"):
            seed.add(name)
    if os.path.isdir(PAGES):                    # Streamlit pages are entry points
        for fn in sorted(os.listdir(PAGES)):
            if fn.endswith(".py"):
                for imp in _imports(os.path.join(PAGES, fn)):
                    for cand in _resolve(imp, mods, by_short):
                        seed.add(cand)
    return seed


def _reachable():
    """Transitive closure from every entry point."""
    mods = _modules()
    by_short = _by_short(mods)
    return mods, _closure(mods, by_short, _seeds(mods, by_short, include_tests=True))


def _test_only_reachable():
    """Modules connected to nothing except a test file. Reported, not asserted —
    see the caveat in the module docstring. While CI runs no tests, these are as
    idle in production as a declared orphan."""
    mods = _modules()
    by_short = _by_short(mods)
    with_tests = _closure(mods, by_short, _seeds(mods, by_short, True))
    no_tests = _closure(mods, by_short, _seeds(mods, by_short, False))
    tests = {n for n in mods if n.split(".")[-1].startswith("test_")}
    return sorted(with_tests - no_tests - tests)


# --------------------------------------------------------------------------- #
def test_every_import_resolves():
    """Does every import name actually exist — as stdlib, as a repo module, or as a
    declared dependency?

    WHY: `pages/1_Test_Model.py` imported `test_model`, which moved to the private
    Cullowhee-Engine repo in 9c720eb. wetness.py, outlook_engine.py and live_rainfall.py
    were all re-sourced onto cwm_model when that happened; the Streamlit page was missed,
    and it raised ModuleNotFoundError the moment anyone opened it. Found 2026-08-13.

    This file already asserted that a WORKFLOW naming a script gets a script that exists.
    It never asserted the same of an IMPORT, which is the same defect through the other
    door — and the reachability walk hid it, because an import that resolves to nothing
    contributes no edge and so looks exactly like an import of a third-party package.

    Scans pages/ as well as the governed modules: a page is an entry point, and a broken
    entry point is a broken console tab.
    """
    mods = _modules()
    by_short = _by_short(mods)
    known = set(sys.stdlib_module_names) | THIRD_PARTY | {"__future__"}
    missing = {}
    dirs = [d for d in PKG_DIRS] + ["pages"]
    for d in dirs:
        root = os.path.join(HERE, d) if d else HERE
        if not os.path.isdir(root):
            continue
        for fn in sorted(os.listdir(root)):
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            rel = os.path.join(d, fn) if d else fn
            for imp in _imports(path):
                head = imp.split(".")[0]
                if head in known or _resolve(imp, mods, by_short) or head in by_short:
                    continue
                missing.setdefault(imp, []).append(rel)
    assert not missing, (
        "these imports resolve to nothing — not stdlib, not a module in this repo, not a "
        "declared dependency:\n  "
        + "\n  ".join(f"{m}   imported by {', '.join(sorted(set(w)))}"
                      for m, w in sorted(missing.items()))
        + "\n\nEither the module left the repo and its importer was not re-sourced (this "
          "is what happened to test_model), or it is a new external dependency that "
          "belongs in THIRD_PARTY above and in the relevant requirements file.")


def test_workflow_entrypoints_exist():
    """A workflow naming a script that is not there fails only when it next runs."""
    missing = [s for s in sorted(_workflow_entrypoints())
               if not os.path.exists(os.path.join(HERE, s))]
    assert not missing, ("workflows invoke scripts that do not exist:\n  "
                         + "\n  ".join(missing))


def test_every_module_is_reachable_or_declared():
    mods, seen = _reachable()
    orphans = sorted(set(mods) - seen - set(DECLARED))
    assert not orphans, (
        "these modules are reachable from nothing that runs — no workflow, no page, "
        "no other reachable module, no test:\n  "
        + "\n  ".join(orphans)
        + "\n\nEach is either dead code to delete, a tool to add to TOOLS, or a finished "
          "component that was never connected. The third case is the one that has cost "
          "this project seven times. Add a DECLARED entry with a closing condition, or "
          "wire it. Do not add it to TOOLS just because it has a __main__ guard — "
          "read the note above TOOLS.")


def test_declared_orphans_are_still_orphans():
    """A waiver that outlives its cause reads as reviewed while guarding nothing."""
    mods, seen = _reachable()
    stale = sorted(n for n in DECLARED if n in seen)
    assert not stale, (
        "these are DECLARED as unwired but are now reachable — the connection shipped:\n  "
        + "\n  ".join(stale)
        + "\n\nDelete them from DECLARED so the test starts guarding the wiring.")


def test_declared_orphans_still_exist():
    """And a waiver for a module that was deleted is just noise."""
    mods, _ = _reachable()
    gone = sorted(n for n in DECLARED if n not in mods)
    assert not gone, ("DECLARED names modules that no longer exist:\n  "
                      + "\n  ".join(gone) + "\n\nDelete these entries.")


def test_tools_still_exist():
    """Same discipline for TOOLS: a stale name silently widens the waiver, because
    any future module that happens to take the dead name inherits its exemption."""
    mods = _modules()
    shorts = {n.split(".")[-1] for n in mods}
    gone = sorted(t for t in TOOLS if t not in mods and t not in shorts)
    assert not gone, ("TOOLS names modules that no longer exist:\n  "
                      + "\n  ".join(gone) + "\n\nDelete these entries.")


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

    mods, seen = _reachable()
    only_test = _test_only_reachable()
    print(f"\n{len(seen)}/{len(mods)} modules reachable · "
          f"{len(DECLARED)} declared unwired · {len(only_test)} test-only · "
          f"{fails} failure(s)")

    if only_test:
        wf = _workflow_entrypoints()
        ci = any(os.path.basename(s).startswith("test_") for s in wf)
        print("\nTEST-ONLY REACHABLE — a test imports these; nothing on a production")
        print("path does. " + ("CI does run tests, so they are exercised — but no"
                               if ci else
                               "No workflow runs any test either, so no")
              + " deployed code")
        print("consumes what they compute:")
        for n in only_test:
            print(f"  * {n}")

    if not fails and DECLARED:
        print("\nBUILT AND NOT CONNECTED — declared, not fixed:")
        for n in sorted(DECLARED):
            print(f"  * {n}\n      {DECLARED[n]}")

    raise SystemExit(1 if fails else 0)
