# Slope monitor — the automated Sentinel-1 pass

The ground layer of NOAH & SKYE. Sentinel-1 radar interferometry reads
millimetre-scale creep across the Cullowhee Creek watershed every ~12 days and
keeps three pages current with no manual steps:

* [`slope_monitor.html`](../slope_monitor.html) — the full report
* [`slope_map.html`](../slope_map.html) — the Leaflet map
* [`slope_3d.html`](../slope_3d.html) — motion draped on the QL1 lidar terrain

Everything below describes the **automation**. The science — SBAS inversion,
atmospheric filtering, detection, inverse-velocity forecasting, hydrologic
fusion — lives in `src/` and is unchanged by it.

---

## The workflow

`.github/workflows/slope-monitor.yml`, two jobs.

### 1. `check` — the daily question (scheduled runs only)

Cron cannot say "every 12 days", so the workflow runs **daily at 10:07 UTC**
and asks one cheap question first: does ASF list a path-48 ascending scene over
the AOI that is not yet in `state/epochs.json`?

Scene search is free and needs no login, so eleven days out of twelve the
answer is no and the whole run is over in well under two minutes, having
touched nothing. The job installs three packages, not the full requirements
file, for the same reason.

### 2. `update` — the real pass

Runs only when `check` found a new scene, or on a manual dispatch.

1. **Self-test first.** `tests/test_slope_ci.py` drives synthetic HyP3 products
   through the entire chain — warp, cache, SBAS, detect, screen, page
   rewrite — and `verdicts.py`'s policy self-test runs inside it. A published
   slope page is a safety artefact; the run does not get to touch one until the
   plumbing has proved itself on this commit. No network needed.
2. **Submit** the new interferogram pairs to ASF HyP3 (nearest + skip-one, the
   same `discover.build_pairs` rules) and wait — budget 3.5 h, the Actions job
   is capped at 6 h. A pair whose earlier HyP3 product has expired is
   resubmitted; a pair whose job failed is recorded and not retried forever.
3. **Download only what is new**, warp it into the pair cache, **delete the
   rasters in the same step**.
4. **Re-invert the whole cached network** — SBAS is over the full pair set, not
   just the new edge — then detect, forecast, and fuse with the hydrologic
   layer exactly as `run_operational.py` + `fuse.py` do.
5. **Rewrite the data regions** of the three pages (see below).
6. **Commit** pages + `state/`, with rebase-and-retry: `main` is written every
   30 minutes by the feed job, so a plain push after a multi-hour run loses the
   race.
7. **Notify** via ntfy when the combined level is WATCH or higher.

---

## The pair cache

`state/pair_cache.npz` — committed to the repo, and the reason this works at
all.

A HyP3 INSAR_GAMMA product is ~100 MB of rasters per pair, and the stack will
hold ~200 pairs. That archive can never live here. But every run needs *all*
the pairs, because the inversion is over the whole network.

So the cache keeps the only part that is ever needed again: each pair's LOS
displacement and coherence, already warped onto the ~80 m analysis grid
(180 × 136, derived from the config bbox by `stack.analysis_grid`), plus one
DEM. That is ~24 k pixels per layer instead of tens of millions.

* Keyed by (reference scene, secondary scene), so a resumed bootstrap never
  re-downloads a pair it already holds.
* Stores the bbox and grid shape it was built on. Widen the AOI in
  `config.yaml` and the cache is **rejected as stale** rather than silently
  mixing two grids — `cache.load` returns an empty cache and says why.
* Written as float32; if it ever exceeds 90 MB it is rewritten as float16,
  which halves it at ~0.06 mm resolution at 100 mm — far below the ~4 mm
  leaf-off noise floor. Above 95 MB the workflow fails rather than pushing a
  blob GitHub will reject at 100 MB.

`state/epochs.json` is the processed-epoch ledger: which scenes are folded in,
which pairs are cached, which failed, and a short run history. It is what makes
`check` cheap, and it is the pipeline's memory across runs.

`state/alert_bulletin.json`, `state/combined_bulletin.json` and
`state/last_pass.json` are the machine-readable outputs of the latest pass.

---

## How the pages are regenerated

The pages are hand-designed and stay that way. `src/render.py` never rewrites
one from a template — it swaps marker-delimited regions:

```html
<!--SLOPE:DATA-->
  const DATA = { ... };
<!--/SLOPE:DATA-->
```

On the first rebuild the markers do not exist, so each region also carries an
anchor pattern matching the hand-written block it replaces; the generated text
goes in wrapped in fresh markers. Every run after that is a plain substitution,
and a region whose anchor stops matching **raises** rather than leaving stale
numbers on a live page.

Everything outside the markers — styles, the chart engine, the Leaflet setup,
the inlined three.js, the lidar `__HGRID__`/`__GEO__` blocks — is byte-for-byte
untouched. `tests/test_slope_ci.py` proves that by rendering two different
passes and comparing everything outside the regions.

| page | generated regions |
| --- | --- |
| `slope_monitor.html` | POSTURE, TILES, MAPCARD, CLUSTERHEAD, CHARTS, PROSE, DATA, VERDICT, ROWS, CHARTCFG |
| `slope_map.html` | PANELSUB, PANELFOOT, CLUSTERS, TS, VELURI, SUBBASINS, BOUNDS, VERDICTMAP, CLSOF, POPUPVERDICT |
| `slope_3d.html` | DATA3D, HEADER3D |

`slope_3d.html` did not exist before this workflow. Its scaffolding is built
once by `tools/build_slope_3d.py`, which lifts the inlined three.js bundle, the
baked lidar height grid and the sub-basin projection out of
`noah_skye_3d.html` — those describe the terrain, not a satellite pass, and no
scheduled run rewrites them.

---

## Verdicts: what CI is and is not allowed to say

**This is the safety-relevant rule in the whole system.**

The pages carry per-cluster verdicts. The ones there before this workflow
("seasonal artifact", "none is a credible landslide yet") were written by an
analyst looking at the series. A scheduled job cannot write those and must not
pretend to.

So `src/verdicts.py` computes a **screening class** from metrics only, and says
so. Every verdict on every page is stamped
**"automated screening — pending analyst review"**.

| class | rule |
| --- | --- |
| `candidate` | \|net motion\| ≥ 25 mm **and** ≥ 85 % of the last 8 steps agree with the net direction **and** motion is present in the leaf-off (Nov–Feb) epochs, if the record has any |
| `suspect artifact` | a single ≥ 8 mm step between adjacent epochs accounts for the record, **or** the fitted velocity's sign contradicts the series' net direction |
| `low-confidence detection` | everything else |

Two phrases in that policy needed an operational reading, and both are
documented on the functions that implement them:

* *"motion present in leaf-off epochs"* — sum the steps landing on a Nov–Feb
  epoch, signed by the net direction; the clause passes if that sum is
  positive, and does not apply if the record contains no such step. A
  consequence worth knowing: a cluster whose motion started in leaf-on season,
  in a record that already contains a winter, **cannot be auto-promoted to
  candidate** until it has survived a winter under it. That is the policy
  working as written.
* *"onset coincides with a single ≥ 8 mm step"* — the biggest step clears
  8 mm, removing it leaves under the 25 mm sustained-motion bar, **and** it
  carries at least half the record. The last two conditions are what keep the
  test off real creep that merely contains one larger step.

Candidates are split `credible` / `weaker case` for the map styling by one more
metric: whether the slope held at least half its overall rate through leaf-off.

**Nothing safety-critical depends on this class.** ADVISORY / WATCH / WARNING
come entirely from `detect.py`, `forecast.py` and `alert.py` against the tuned
thresholds in `config.yaml`, conditioned by `fuse.py`. The screener never
writes WARNING — the self-test asserts it.

`config.yaml`'s thresholds are validated on 12 months of real data. Do not
change them casually; the workflow does not.

---

## Running it by hand

```bash
pip install -r pipeline/requirements.txt

python pipeline/ci_update.py check      # is there a new scene? (no login needed)
python pipeline/ci_update.py update     # normal pass
python pipeline/ci_update.py bootstrap  # backfill the whole cache
python pipeline/ci_update.py rebuild    # re-analyse + re-render from the cache

python pipeline/tests/test_slope_ci.py  # the offline end-to-end self-test
python -m pipeline.src.verdicts         # just the screening policy
```

Useful flags: `--force` (run even with no new scene), `--max-pairs N`
(bootstrap in chunks), `--budget SECONDS` (HyP3 wait), `--no-pages` (write
`state/` only).

HyP3 needs `EARTHDATA_USERNAME` / `EARTHDATA_PASSWORD` in the environment (or a
`~/.netrc` entry for `urs.earthdata.nasa.gov`). Scene search and `rebuild` need
neither.

### To force a run

Actions → **slope monitor (Sentinel-1 InSAR)** → **Run workflow**:

* `mode: update` + `force: true` — re-run the current pass end to end.
* `mode: bootstrap` — backfill the cache. Set `max_pairs` (say 40) to work in
  chunks; each run resumes where the last stopped, and the summary says how
  many pairs are still missing. Repeat until it says none.
* `mode: rebuild` — re-render the pages from the cache with no network at all.
  This is what to use after editing a page's design or the screening code.

---

## The first bootstrap

The cache starts empty, so the first real run must backfill it: every pair
since `config.yaml`'s `start_date`. HyP3 products expire 14 days after the job
runs, so pairs already processed on Mickey's PC will mostly have to be
resubmitted — quota is ~10,000 credits/month against roughly one credit per
job, so this is not a constraint.

Dispatch `mode: bootstrap` with `max_pairs: 40` and repeat until the run
summary stops reporting missing pairs. Until at least three pairs are cached
the run writes nothing and leaves the pages alone.

## The Windows task

Mickey's PC runs `auto_update.bat` on a 12-day schedule. It writes only to his
local folder, so it is redundant rather than conflicting. Once this workflow
has completed a bootstrap and one ordinary pass, that task can go:

```
schtasks /delete /tn "Cullowhee LEWS satellite update" /f
```
