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
  2026-08-15  bias.html — a published page fetching feed/bias_report.json, which nothing
              in this repo writes. The deployed unit generates the report to a path on
              the ledger host (/var/lib/noah/bias_report.json) and no step copies it into
              feed/. Found by walking page assets, because this test only ever walked
              PYTHON imports: an orphan wearing a different file extension was outside
              its boundary. That is the same blind spot this file exists to close, in the
              file type this project publishes to the public.

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
6. Every feed/ asset a published page fetches actually exists in the repo. A page is
   an entry point too, and a page whose data file nobody writes is the same defect as
   an orphaned module — the page just fails silently in a visitor's browser instead of
   raising in CI.
7. Every test_*.py is named by a workflow. consistency-tests.yml lists its suites by
   hand, so a new test file runs nowhere — and this file cannot notice on its own,
   because it seeds its own closure from every test_*.py and a new suite therefore
   makes ITSELF look connected.

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

# feed/ is committed (it is not in .gitignore — only feed_demo/ is), so a fresh
# checkout carries these files and "does it exist" is a valid question in CI.
FEED_ASSET_RE = re.compile(r"feed/[A-Za-z0-9_][A-Za-z0-9_.-]*\.[A-Za-z0-9]+")

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

# Declared missing page assets. Same contract as DECLARED: a reason and a closing
# condition, and the test fails once the asset appears so the waiver gets deleted.
SITE_ASSETS_DECLARED = {
    "feed/bias_report.json": (
        "bias.html fetches it. Nothing in this repo writes it. ledger/bias_report.py "
        "generates the report on the ledger host and deploy/qpf-bias-report.service "
        "sends it to /var/lib/noah/bias_report.json — a path GitHub Pages cannot serve "
        "— so the daily timer can run forever with no visible effect. This is the "
        "publish half of the same gap as DECLARED['ledger.bias_report']: that entry "
        "says the ledger has no host, this one says the report has no route to the "
        "page even once it does. "
        "CLOSES WHEN: the ledger has a persistent home AND the report lands in feed/ "
        "(--out into a checkout that a workflow commits, or an rsync into the served "
        "tree). See noah_bias_ledger_never_ran_2026-08-12.md."),
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
    "sync_engine_html",             # argparse CLI: reports basins.py-vs-engine-HTML drift,
                                    # --write fixes it. Report-only by default and it edits
                                    # a 713 KB deployed page, so it is deliberately NOT
                                    # wired to a workflow — a human reads the diff first.
                                    # It landed 2026-08-15 without this entry and left CI
                                    # red until 5603ceb+1; the omission was caught by this
                                    # very test, which is the mechanism working.
    "verify_browser_engines",       # loads live.html and rain_to_trip.html in headless
                                    # Chromium and runs THEIR assessBasin. Until 2026-08-16
                                    # nothing in this repo had ever executed either JS
                                    # engine: the registry test compares their embedded
                                    # NUMBERS, and the 4,977-storm agreement test compares
                                    # two PYTHON engines. Needs Chromium, so it is a
                                    # workstation tool rather than a CI suite. RUN IT AFTER
                                    # sync_engine_html.py --write, which edits those files.
    "verify_surveyed_thresholds",   # reconciles basins.py's SURVEYED ladders against
                                    # the 3DEP cut they claim to come from. Its evidence
                                    # (scripts/xs_out/summary.csv) was committed in
                                    # fe01cdf, so this RUNS ON A RUNNER now. It is a TOOL
                                    # rather than a suite only because it needs no test
                                    # harness — not because it cannot run in CI. It exits
                                    # 1 on a mismatch, so it drops straight into a step.
    "ledger.verify",        # argparse CLI: --status/--score/--propose/--selftest
    "nisar_slope_motion",   # argparse CLI: reads ~2.3 GB NISAR L2 GUNW .h5 files from ASF and
                            # reports LOS motion on the NC 107 slope. Needs the files and h5py, so
                            # it is a workstation tool; it landed 2026-09-03 without this entry.
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
    "h5py", "matplotlib", "matplotlib.pyplot",   # nisar_slope_motion.py (workstation tool)
    # requirements.txt (the console)
    "streamlit", "streamlit_autorefresh", "streamlit_folium", "folium",
    "requests", "pandas", "numpy", "plotly", "pydeck",
    "google",                       # google-cloud-firestore
    # batch / analysis tools, optional by design
    "geopandas", "rasterio", "shapely", "shapefile", "pyproj", "scipy", "xarray",
    "eccodes", "pystac_client", "planetary_computer",
    # verify_browser_engines.py drives headless Chromium. Deliberately NOT in
    # requirements.txt: the console never needs a browser, and adding one would put a
    # ~150 MB download in front of every Streamlit boot. It is the same shape as the
    # GRIB and geospatial names above — a batch/analysis tool that guards its own
    # import and exits with install instructions rather than assuming the package.
    "playwright",
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


def _workflow_text():
    """Every workflow YAML concatenated, for NAME-presence questions only.

    Deliberately not _workflow_entrypoints(): that parses `python foo.py`, and
    consistency-tests.yml runs half its suites through a shell loop —
    `for t in test_a test_b ...; do python "$t.py"; done` — which no such regex
    can see. Asking "is this suite named anywhere in CI" is answerable; asking
    "is it invoked" would need a shell interpreter.
    """
    if not os.path.isdir(WORKFLOWS):
        return ""
    out = []
    for fn in sorted(os.listdir(WORKFLOWS)):
        if fn.endswith((".yml", ".yaml")):
            with open(os.path.join(WORKFLOWS, fn), encoding="utf-8",
                      errors="replace") as f:
                out.append(f.read())
    return "\n".join(out)


def _page_assets():
    """{feed asset: [pages naming it]} across every published page at the repo root.

    Discovered, not listed. Naming the pages by hand is how bias.html came to fetch a
    file nothing writes without anything noticing.

    Matches the literal string anywhere in the file rather than parsing href/src, which
    would miss every asset a page builds in JavaScript — and all four of these are
    fetched from JS, none from markup. The direction of that looseness is the safe one
    here: a page that merely MENTIONS feed/x.json in a comment gets x.json checked for
    existence, which is a harmless extra assertion, not a missed orphan.
    """
    out = {}
    for fn in sorted(os.listdir(HERE)):
        if not fn.endswith(".html"):
            continue
        try:
            with open(os.path.join(HERE, fn), encoding="utf-8", errors="replace") as f:
                src = f.read()
        except OSError:
            continue
        for m in FEED_ASSET_RE.findall(src):
            out.setdefault(m, []).append(fn)
    return out


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


def test_every_test_file_is_named_by_a_workflow():
    """A suite nothing runs is the same defect as a module nothing imports.

    consistency-tests.yml names its remaining suites BY HAND —
    `for t in test_flood_network_upwind test_gov_gauges ...` — not by glob. So a
    test file added to this repo runs nowhere, and nothing notices: this file
    seeds its reachability closure from every test_*.py, which means a new test
    file makes ITSELF look connected while running in no CI job at all.

    That is the hand-written-list trap that `engine_html()` and `_page_assets()`
    both avoid by discovering instead of naming. The workflow cannot discover
    (it is protected against remote edits and a glob there is its own risk), so
    the list stays hand-written and this makes it self-policing: add a suite,
    CI goes red until the suite is wired.
    """
    tests = sorted(n.split(".")[-1] for n in _modules()
                   if n.split(".")[-1].startswith("test_"))
    text = _workflow_text()
    assert text, (
        f"no workflow files found under {os.path.relpath(WORKFLOWS, HERE)} — this "
        "check cannot answer whether the suites run, so it is failing rather than "
        "passing vacuously.")
    missing = [t for t in tests if t not in text]
    assert not missing, (
        "these test suites are named by no workflow, so they run nowhere:\n  "
        + "\n  ".join(missing)
        + "\n\nAdd each to the `for t in ...` list in consistency-tests.yml (or give "
          "it its own step). A test that never runs is worse than no test: it reads "
          "as coverage.")


def test_every_page_asset_exists():
    """A published page fetching a file nobody writes fails only in a visitor's browser.

    And it fails QUIETLY: bias.html caught its own 404 and rendered "there is honestly
    nothing to score" — a confident claim about the ledger's contents made by a page
    that could not read the ledger at all. That is the conflation noah_feed_check.py
    was written to correct on the console side ("did we FAIL TO READ IT, or did we read
    it and find NOTHING THERE? Those are opposite states"). Nothing was enforcing the
    same rule on the public site.
    """
    assets = _page_assets()
    missing = {a: p for a, p in assets.items()
               if a not in SITE_ASSETS_DECLARED
               and not os.path.exists(os.path.join(HERE, a))}
    assert not missing, (
        "published pages fetch feed assets that do not exist:\n  "
        + "\n  ".join(f"{a}   fetched by {', '.join(sorted(set(p)))}"
                      for a, p in sorted(missing.items()))
        + "\n\nEither wire a producer, or add a SITE_ASSETS_DECLARED entry with a "
          "closing condition. And check what the page RENDERS when the fetch fails: a "
          "missing file must not be reported as an empty result. Those are opposite "
          "states and only one of them is a claim the page is entitled to make.")


def test_declared_page_assets_are_still_missing():
    """A waiver that outlives its cause reads as reviewed while guarding nothing."""
    live = sorted(a for a in SITE_ASSETS_DECLARED
                  if os.path.exists(os.path.join(HERE, a)))
    assert not live, (
        "these assets are SITE_ASSETS_DECLARED as unproduced but now exist — the "
        "publish step shipped:\n  " + "\n  ".join(live)
        + "\n\nDelete them from SITE_ASSETS_DECLARED so the test starts guarding the "
          "wiring instead of excusing it.")


def test_page_assets_were_actually_scanned():
    """If the scan finds nothing, this check is silently a no-op and every page asset
    is unguarded while the suite still reports green. Fail loudly instead."""
    assets = _page_assets()
    pages = [f for f in os.listdir(HERE) if f.endswith(".html")]
    assert pages, "no .html at the repo root — the page scan has nothing to walk."
    assert assets, (
        f"scanned {len(pages)} page(s) and found no feed/ asset references at all. "
        "Pages fetched feed/external.json, feed/outlook.json, feed/fiman_watch.csv and "
        "feed/bias_report.json on 2026-08-15, so this means the pattern has stopped "
        "matching and this check now guards nothing. Fix FEED_ASSET_RE; do not delete "
        "this test.")


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

    assets = _page_assets()
    print(f"{len(assets)} feed asset(s) fetched by published pages · "
          f"{len(SITE_ASSETS_DECLARED)} declared unproduced")
    for a in sorted(assets):
        ok = os.path.exists(os.path.join(HERE, a))
        mark = "ok" if ok else "MISSING"
        print(f"  {mark:<8}{a:<28} <- {', '.join(sorted(set(assets[a])))}")

    if not fails and DECLARED:
        print("\nBUILT AND NOT CONNECTED — declared, not fixed:")
        for n in sorted(DECLARED):
            print(f"  * {n}\n      {DECLARED[n]}")

    if not fails and SITE_ASSETS_DECLARED:
        print("\nPUBLISHED AND NOT PRODUCED — declared, not fixed:")
        for a in sorted(SITE_ASSETS_DECLARED):
            print(f"  * {a}\n      {SITE_ASSETS_DECLARED[a]}")

    raise SystemExit(1 if fails else 0)
