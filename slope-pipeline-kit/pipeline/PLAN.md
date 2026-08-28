# Slope Monitor automation — build plan for a repo-attached Claude session

Context: this folder is the validated Sentinel-1 InSAR landslide pipeline for the
Cullowhee Creek Watershed (the "ground layer" of NOAH & SKYE). It has been running
manually on Mickey's PC; this plan turns it into a scheduled GitHub Actions
workflow that keeps `slope_monitor.html`, `slope_map.html`, and `slope_3d.html`
current with zero manual steps. Read this whole file before writing code.

## What already exists

* `pipeline/src/` — the working pipeline (validated on 12 months of real data):
  discover (ASF search) → HyP3 submit/download → SBAS inversion + atmospheric
  filtering (`stack.py`) → per-pixel kinematics (`timeseries.py`) → detection
  (`detect.py`) → inverse-velocity forecasting (`forecast.py`) → alerting
  (`alert.py`) → hydro conditioning (`hydro.py`, `fuse.py`).
* `pipeline/config.yaml` — AOI covers the FULL watershed (widened 2026-08-25);
  all thresholds are tuned and validated — do not change them casually.
* Repo secrets `EARTHDATA_USERNAME` / `EARTHDATA_PASSWORD` are set.
* The three site pages exist in the repo root and are live on GitHub Pages.
  Their static scaffolding (styles, three.js lib, lidar HGRID/GEO blocks) should
  be treated as templates: regenerate only the data-dependent parts.
* The repo already runs scheduled Actions (see the feed workflow) — follow its
  conventions. `NTFY_TOPIC` secret exists for push notifications.

## The workflow to build (.github/workflows/slope-monitor.yml)

Schedule: every 12 days is not expressible in cron; run DAILY at 10:00 UTC and
exit early unless a new Sentinel-1 scene (path 48 ASCENDING over the AOI)
exists that is not yet in the processed-epoch ledger. Also allow
workflow_dispatch for manual runs.

Steps per run (implement as `pipeline/ci_update.py` orchestrator):
1. `asf_search` for scenes since config start_date; compare against
   `pipeline/state/epochs.json` (ledger committed to the repo). No new scene →
   exit 0 quietly.
2. New scene → build the new interferogram pairs (nearest + skip-one, same
   rules as `discover.build_pairs`), submit HyP3 jobs, poll until done
   (hyp3_sdk watch; budget ≤ 3.5 h — Actions jobs cap at 6 h).
3. Download ONLY the new products. DO NOT try to keep the raster archive in the
   repo (~100 MB per pair). Instead maintain the incremental cache:
   * `pipeline/state/pair_cache.npz` — every pair's LOS displacement + coherence
     already warped to the analysis grid (~80 m, grid derived from config bbox;
     see `stack.load_hyp3_stack` for the warp). Committed to the repo (tens of
     MB; if it outgrows 90 MB, switch to zstd-compressed float16).
   * Each run warps the new pairs, appends them to the cache, deletes the raw
     downloads, then runs the SBAS inversion over the FULL cached pair set
     (inversion of ~200 pairs on the ~180x136 grid takes minutes — fine in CI).
   * Bootstrap: the first real run must backfill the cache by downloading the
     full HyP3 archive once (jobs already succeeded under Mickey's account may
     have expired after 14 days — resubmit expired pairs; quota is ample).
     Consider doing the bootstrap as a manually dispatched run.
4. Run detection + forecasting + hydro fusion exactly as
   `run_operational.py` + `fuse.py` do; write bulletins to `pipeline/state/`.
5. Regenerate the three pages IN PLACE (repo root), replacing only:
   * the embedded data JSON (`window.__SLOPE__` / `const DATA`) blocks,
   * the base64 map images (matplotlib, dark style — read the existing pages
     for the exact palette/settings),
   * stat tiles / dates / cluster tables.
   Preserve the pages' scaffolding byte-for-byte outside those regions. Use
   marker comments to delimit generated regions on the first rebuild so later
   runs are clean substitutions.
6. Commit + push: pages + `pipeline/state/*`. Commit message like
   `slope: 2026-09-06 pass — level WATCH, 2 candidates`.
7. If the combined level is WATCH or higher, POST a notification via ntfy
   using the `NTFY_TOPIC` secret (mirror however the feed workflow uses it).

## Verdict policy (IMPORTANT — this is a safety-relevant design rule)

The published pages currently carry per-cluster analyst verdicts (e.g.
"seasonal artifact", "candidate"). CI must NOT hallucinate analyst judgment.
Auto-generate cautious verdicts from metrics only:
* `candidate` — |net motion| ≥ 25 mm AND last-8-step direction agreement ≥ 85%
  AND motion present in leaf-off epochs (Nov–Feb) if the record includes them;
* `suspect artifact` — onset coincides with a single ≥8 mm step between
  adjacent epochs, or velocity sign contradicts the series' net direction;
* otherwise `low-confidence detection`.
Label every verdict "automated screening — pending analyst review" on the
pages. Never auto-publish the word WARNING for a cluster unless the pipeline's
own escalation (accel + persistence + forecast gates in detect/forecast/alert)
produced it.

## Known context for continuity

* Two candidates as of the 2026-08-13 pass: cluster at 35.2641 N, 83.1857 W
  ("Mtn. Lower" basin, ~120 ac, +51 mm/yr-scale, moved through winter —
  the strong one) and 35.2955 N, 83.2069 W (Cox Branch, ~10 ac, onset May
  2026 — weaker). Cluster IDs are NOT stable across runs; match areas by
  location, not ID, when writing "what changed" text.
* Sub-basin roster: `cullowhee_subbasins.geojson` (repo root). Tag clusters by
  point-in-polygon, smallest containing basin wins.
* Seasonal reality: leaf-on (May–Oct) noise floor is ~2x the leaf-off floor.
  The noise-by-epoch chart on slope_monitor.html must stay — it is the honest
  context for every detection.
* Mickey's PC also runs `auto_update.bat` on a 12-day Windows schedule. That's
  a redundant belt-and-suspenders path, not a conflict: it writes only to his
  local folder. Once this workflow is proven, tell him he can delete the
  Windows task (schtasks /delete /tn "Cullowhee LEWS satellite update" /f).

## Definition of done

1. `workflow_dispatch` bootstrap run completes: cache built, pages regenerated,
   committed, Pages deploy verified live.
2. A no-new-scene daily run exits quietly in under 2 minutes.
3. README section added under `pipeline/` documenting the workflow, the cache,
   and how to force a run.
