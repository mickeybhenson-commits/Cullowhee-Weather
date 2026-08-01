# CLAUDE.md

Guidance for AI assistants working in this repository.

## What this is

**NOAH / SKYE — Cullowhee Creek Flood Warning System.** A flood-warning and
flood-modeling stack for Cullowhee Creek, a flashy mountain watershed in Jackson
County, North Carolina (Western Carolina University campus sits on it). Built
after Hurricane Helene (2024).

Two things follow from that, and they govern almost every design decision in the
codebase:

1. **It is a safety system.** A wrong number here is not a bug, it is an
   under-warning. The code is written defensively and comments say so.
2. **It is in SHADOW MODE.** There are (as of this writing) no NOAH in-stream
   sensors deployed. Status is *modeled* from forecast rainfall, cross-checked
   against government gauges. Every public surface carries a
   "DEVELOPMENT SYSTEM — NOT FOR EMERGENCY USE" banner and names NWS
   Greenville-Spartanburg (WFO GSP) as the warning authority and NCEM FIMAN as
   the authoritative gage record.

**Never remove or soften those disclaimers, and never invent a number.** The
codebase's strongest convention is that every quantity is traceable to a cited
source (USGS StreamStats regression, TVA 1983 FPM-83/51, a surveyed high-water
mark, or a live gauge API) or is explicitly tagged `[PLACEHOLDER]` / `[SET]` /
`[CONFIRM]`. If you cannot source a value, leave the marker in place rather than
filling it with something plausible.

## Layout

Flat Python package at the repo root (no `src/`, no `setup.py`, no packaging —
modules import each other by bare name).

```
.
├── streamlit_app.py         # operations console (Streamlit entry point)
├── pages/1_Test_Model.py    # Streamlit multipage: engine/design-storm view
│
│   ── DATA / REGISTRY ──
├── basins.py                # source-tagged sub-basin registry (THE data file)
├── data/                    # surveyed inputs: XS master, TVA profiles, Helene marks, VERTCON grid
├── cullowhee_subbasins.geojson
├── gribble_gap.json         # frozen pre-registration for the WCHRS flume calibration node
├── wchrs_public/            # WCHRS Gribble Gap discharge record (13 MB CSV)
│
│   ── HYDROLOGY ENGINE ──
├── flood_engine.py          # point physics: Manning Q, hysteresis, rate-of-rise, TR-55, EW probability
├── flood_network.py         # topology + travel-time routing + TWO-TIER posture
├── flood_rating.py          # authoritative rating/posture engine (reads basins.py)
├── cwm_model.py             # runnable rainfall→runoff→UH→peak Q front-end
├── wetness.py               # antecedent wetness (30-day API / soil percentile) + baseflow
├── flood_profile.py         # reach-based corridor state between nodes
├── orographic.py            # orographic lift potential (pre-rain leading indicator)
├── lead_time.py             # per-basin Tc vs the 120-min operational lead requirement
├── flood_ensemble.py        # 3×3 QPF × wetness input-uncertainty ensemble
├── confluence_status.py     # Cullowhee×Tuckasegee backwater node (different mechanism)
├── confluence_panel.py      # Streamlit card for the above
├── outlook_engine.py        # bridge: calibrated engine → flood_network Outlook tier
│
│   ── LIVE DATA IN ──
├── sources.py               # THE source resolver (MEASURED > GOV_ESTIMATE > MODELED) + gates
├── feeds.py                 # all external connectors (USGS, NWPS/NWM, MRMS, FFG, alerts, ECONet)
├── gov_gauges.py            # measured rain on the W/SW/S storm-approach arc (USGS + Synoptic)
├── gov_sources.py           # bridges gov gauges into posture (upward-only correction)
├── live_rainfall.py         # Open-Meteo QPF + antecedent state → engine forcing
├── fiman_source.py          # gated MEASURED stage from NCEM FIMAN gage 25380 (CUCN7)
├── fiman_watch.py           # logs how often that gage ACTUALLY reports
│
│   ── PUBLICATION OUT ──
├── feed_runner.py           # what the GitHub Action runs; holds cross-run state
├── publish_feed.py          # dumb formatter → FIMAN-schema GeoJSON
├── feed/                    # COMMITTED OUTPUT — the published feed + cross-run state
├── index.html               # GitHub Pages landing page
├── live.html, storm_watch.html, noah_skye_3d.html,
│   Cullowhee_Creek_live_status.html   # deployed static map/console pages
│
│   ── OFFLINE / WORKSTATION ──
├── scripts/                 # one-time GIS + reconciliation tools (heavy deps)
├── ledger/                  # QPF-bias ledger (SQLite, runs on a VM, not in CI)
├── deploy/                  # systemd units + litestream config for the ledger VM
├── backtest_helene.py       # Helene validation harness (doubles as a CI gate)
├── cucn7_backfill.py        # IEM HADS/SHEF archive pull for the CUCN7 gage
├── fetch_helene_forcing.py  # K24A archive pull for Helene forcing
├── bfe_to_thresholds.py     # emits SURVEYED thresholds when HEC-RAS shapefiles arrive
├── landuse_cn.py            # imagery → impervious % → composite CN per basin
├── merge_subbasins.py       # stitch StreamStats KMLs → cullowhee_subbasins.geojson
└── test_*.py                # tests (see "Testing")
```

Docs worth reading before touching the relevant area:

| File | Read before |
|---|---|
| `NOAH_data_source_decisions.md` | adding *any* data source — it records standing decisions and dead ends (e.g. private/hobby stations are permanently out of scope; Coweeta is calibration-only, not live) |
| `NOAH_environmental_gauges.md` | wiring non-rain sensors (soil moisture, groundwater, PWAT) |
| `README_improvements.md` / `CHANGELOG_improvements.md` | changing `flood_rating.py` or `basins.py` — documents the 2026-07 improvement set (§2 frequency classification, §3 PI band, §4 lead time) |
| `ledger/README_ledger.md` | anything under `ledger/`, `scripts/build_mrms_masks.py`, `scripts/backfill_ledger.py`, or `deploy/` |

## Core invariants — do not break these

### 1. The provenance ladder

Every input resolves through `sources.resolve()` with a priority tag:

```
MEASURED      physical sensor / gauge
GOV_ESTIMATE  official model or agency product (USGS regression, TVA, NWM)
MODELED       Open-Meteo / our own engine  ← the fallback today
```

Higher tiers win *only if they pass gates*: **fresh** (recent enough) and **in
range** (physically plausible). A dead gauge reading 0.00 during a flood must
lose to the model, and the rejection reason is recorded in `.note` so an
operator can see the sensor was present but rejected. When you add a source,
add its gates too.

### 2. Corrections from distant gauges are UPWARD-ONLY

`gov_sources.storm_correction_map()` scales a basin's *modeled* storm rain **up**
toward a measured upwind gauge when the forecast is under-calling, and **never
down**. A gauge 30 km away is a leading indicator, not the basin's rain;
substituting it directly would (a) read ~0 before the storm arrives and delete
the storm from the outlook, and (b) carry valley orographic under-catch. Direct
substitution via `GovGaugeBackend` is reserved for a *true in-basin* gauge.
`test_gov_sources.py` asserts this safety property — keep it green.

### 3. The two-tier posture rule

`flood_network.tiered_posture()`:

* **Outlook tier** (forecast, soil priming, orographic lift, measured upwind
  rain) is **capped at WATCH**.
* **Confirmation tier** (a measured stage rise) is the only path to WARNING or
  EMERGENCY.

The one exception now live: `fiman_source.py` — a *fresh* FIMAN reading is a real
measured in-watershed stage, so it may reach WARNING/EMERGENCY via FIMAN's own
condition text. Note it uses FIMAN's condition text, **not** our absolute stage
thresholds, because `CULLOWHEE_DATUM_VERIFIED` is still `0` (that gage's datum
has not been tied to a surveyed benchmark).

### 4. Posture basis per node

Set by `basins.py` `rating`, dispatched in `flood_rating.assess()`:

* **Campus `CC-WCU-2260`** (`rating="tva"`) — field-**validated** TVA stage
  rating, WATCH/WARNING/EMERGENCY at **7 / 9 / 11 ft**. Authoritative; don't
  change it.
* **Seven non-campus reaches** (`rating="rectangular"`) — classify by
  **discharge return-period** against USGS regression flows
  (WATCH ≥2-yr, WARNING ≥10-yr, EMERGENCY ≥100-yr; Cox and Long Branch drop
  WATCH to 1.5-yr because they are lead-limited). This exists because the
  rectangular Manning rating **collapses above bankfull** and the seven
  `thr_ft` values are `bankfull × (1.0, 1.5, 2.0)` placeholders riding that
  broken scale — which demonstrably under-warned four reaches in the Helene
  back-test. `depth_ft` / `stage_posture` are retained as cross-checks only.
* **Mouth `CC-MOUTH-2340`** (`rating="none"`) — out of scope for the creek's own
  rating; it floods by **backwater** from the Tuckasegee, handled separately in
  `confluence_status.py`, which posts `max(creek posture, backwater posture)`.

`flood_rating.assess(model_peak_q_cfs, bid)` is the single entry point. Feed it
the **raw** model peak (`cwm_model.assess(...)["qp_raw"]`) — the engine applies
the per-basin regression calibration itself.

### 5. Drainage areas are nested and must never be summed

`UP-503 → MS-1100 → SPD-1830 → WCU-2260 → MOUTH-2340`, with TIL, COX, LB joining
as tributaries. These are cumulative points down one mainstem. `downstream` in
`basins.py` encodes the order.

### 6. `live.html`'s JS engine is a PORT of `wetness.py`

If you change the antecedent-wetness / CN logic in Python, the embedded
JavaScript engine in `live.html` must be updated to match. `cwm_model.py` is the
runnable Python reconstruction of that same deployed JS engine. Three
implementations, one set of physics — keep them in sync or say clearly in the
commit that they have diverged.

### 7. `feed/` is committed output, not scratch

`feed_runner.py` deliberately persists cross-run state in git rather than a
database, because a cron job is stateless and two parts of `flood_engine.py`
need memory (`rate_of_rise_ft_hr` needs a time series; `classify_stage` needs
`prev_level` for the 0.5 ft de-escalation deadband). Files:

* `feed/history.json` — rolling stage series (trimmed to 180 min)
* `feed/state.json` — last emitted level, for hysteresis
* `feed/gages.geojson`, `feed/feed_meta.json` — the published feed
* `feed/fiman_watch.csv` — FIMAN reporting-reliability log (failures logged as rows *on purpose*)
* `feed/cucn7_*` — the CUCN7 archive backfill

Do not `.gitignore` these, and do not hand-edit them; they are regenerated every
30 minutes by CI.

### 8. Feed schema: `FX_` marks anything modeled

`publish_feed.py` mirrors NCEM FIMAN's `GAGES_ALL` field names for observation
columns (so the layer drops into Jackson County EM's existing Esri map with no
re-symbolization) and namespaces **every** modeled/forecast column with `FX_`.
Never publish a model output under an observation field name.

## Development workflows

Environment: **Python 3.11** (matches CI and `.devcontainer`).

```bash
pip install -r requirements.txt
```

`requirements.txt` covers only the live tier (Streamlit console + feed). The
offline/GIS tools need extra deps that are deliberately **not** in it —
`geopandas`, `rasterio`, `shapely`, `pyproj` (`landuse_cn.py`,
`merge_subbasins.py`, `scripts/*`), and `eccodes` (`ledger/fetch_mrms.py`, see
`ledger/requirements_ledger.txt`). Don't add them to the root
`requirements.txt`; CI would then install tens of megabytes it never uses.

### Run the console

```bash
streamlit run streamlit_app.py
```

Needs Google Cloud credentials for Firestore (project `ee-dashboard-477704`,
database `cullowhee`). The flood-engine imports are wrapped in try/except and the
app degrades rather than crashes when they fail — preserve that.

### Module self-tests

Nearly every module runs standalone and prints a self-test table. This is the
primary way to sanity-check a change:

```bash
python flood_rating.py       # posture for all 8 nodes: RP, posture, PI band, stage x-check
python flood_engine.py       # synthetic hydrograph self-test
python flood_network.py      # routing + tiered-posture self-test
python backtest_helene.py    # Helene validation; exits non-zero if it fails to reproduce
python lead_time.py          # per-basin lead-time table
python flood_ensemble.py     # posture distribution over the QPF × wetness grid
python wetness.py
python sources.py
python feeds.py              # per-connector status table — NEEDS REAL NETWORK EGRESS
python gov_gauges.py         # needs SYNOPTIC_TOKEN for the HADS/RAWS half
python live_rainfall.py
```

`feeds.py`, `gov_gauges.py`, and `live_rainfall.py` hit live public APIs. In a
sandbox with allowlisted egress they will report UNREACHABLE for everything —
that is the sandbox, not a regression. Don't "fix" it by stubbing the connectors.

### Testing

There is no pytest config and no test runner script. Two styles coexist:

```bash
python -m unittest test_improvements -v   # unittest (24 tests)
python test_gov_gauges.py                 # script-style; prints PASS/FAIL, exits 1 on failure
python test_gov_sources.py
python test_flood_network_upwind.py
```

All tests are **network-free** — they feed synthetic payloads shaped like the
real USGS/Synoptic responses. Keep it that way; a test that needs the internet
cannot gate a warning system.

**All suites currently pass.** Note `TestConfluenceMouth` in
`test_improvements.py`: it pins the mouth node's contract, which is easy to get
wrong. `CC-MOUTH-2340` is `rating="none"` but **not postureless** — `assess()`
returns the *creek* half of the confluence (its own §2 discharge frequency) and
`confluence_status.py` adds the Tuckasegee backwater half. It must never revert
to returning `"N/A"` for `posture`, because `confluence_status` combines the two
sides with `max()` over `_RANK`, where `"N/A"` scores **-1 — below `NORMAL`**. An
`"N/A"` creek side would be silently masked by a quiet river, and a creek-driven
flood at the mouth would disappear. The stage cross-check (`depth_ft`,
`stage_posture`) *is* legitimately N/A there; the operative posture is not.

### Missing modules referenced by the code

`test_model.py` is imported by `outlook_engine.py`, `live_rainfall.py`,
`wetness.py` (lazily, inside a function), and `pages/1_Test_Model.py` — but **it
is not in the repository**. Those import paths are currently broken. Likewise
`calibrate_lb171.py` (invoked by `fetch_helene_forcing.py`) is absent, and
`cwm_classify.py` was intentionally superseded by `flood_rating.py`. If you need
that functionality, say so rather than reconstructing `test_model.py` from
guesses — its `BASINS` dict and `run_case()` carry calibration values that must
match `basins.py`.

### Duplicated files

`bfe_to_thresholds.py`, `noah_skye_3d.html`, and `Cullowhee_Creek_live_status.html`
exist at both the repo root and under `scripts/`, and **the copies differ**. The
root copies are the ones GitHub Pages serves and the ones referenced by
`index.html`. Diff before editing, and edit the one that is actually deployed.

## CI / deployment

### `.github/workflows/publish-feed.yml`

Runs every 30 minutes (`*/30 * * * *`), on push to `main`, and on demand.

```
python feed_runner.py                    # env: CULLOWHEE_SHADOW_MODE=1, CULLOWHEE_DATUM_VERIFIED=0
python fiman_watch.py                    # continue-on-error: reliability logging must not break publishing
sanity check: refuse to publish an empty feed (site_count == 0 → hard fail)
commit feed/ as "feed: <ISO timestamp>" by feed-bot, pull --rebase --autostash, push
```

The empty-feed guard is a safety gate. Do not weaken it.

`feed_runner.get_modeled_stage_ft()` is still a **`TODO` stub returning `None`**.
That is correct behavior, not an oversight: returning `None` publishes
"No Data", and the header explicitly warns against returning a placeholder
constant — "a plausible fake number on a public URL is the failure mode this
whole design is trying to avoid."

### `.github/workflows/backfill-history.yml`

`workflow_dispatch` only, 350-minute timeout. Pulls the CUCN7 archive from IEM
day by day; resumable via `feed/cucn7_backfill_state.json`. Tunable with
`BACKFILL_START` / `BACKFILL_END` / `BACKFILL_MAX_MINUTES`.

### GitHub Pages

Served from the repo root (`.nojekyll` present). `index.html` is the landing page
and embeds the map pages in lazy-loaded iframes (`#liveMap`, `#stormWatch`),
linking out to `storm_watch.html` and `noah_skye_3d.html`. Note the iframes read
`--bannerH` so the dev banner offsets the embedded layouts — if you change a
banner height, change it in both the host page and the embedded one.

`feed/gages.geojson` is **not** consumed by any page in this repo. It is
published for an *external* consumer — Jackson County EM's existing Esri client,
alongside the state's FIMAN layer — which is exactly why its field names mirror
FIMAN's.

### The ledger VM (not CI)

The QPF-bias ledger runs on a separate VM under systemd timers
(`deploy/qpf-forecast.timer` 6-hourly, `deploy/qpf-mrms.timer` hourly), writing
`/var/lib/noah/qpf_ledger.db`, replicated by Litestream. Nothing in GitHub
Actions touches it. Its analysis conventions (wet-windows-only filtering, the
`pairs_6h` view, the N≥20 promotion guard before any correction reaches the live
outlook tier) are in `ledger/README_ledger.md` — follow them; they exist to stop
drizzle from burying the orographic signal.

## Environment variables

| Var | Used by | Default / note |
|---|---|---|
| `CULLOWHEE_SHADOW_MODE` | `publish_feed.py` | `"1"` — shadow mode ON |
| `CULLOWHEE_DATUM_VERIFIED` | `publish_feed.py` | `"0"` — FIMAN gage datum NOT tied to a benchmark |
| `CULLOWHEE_BASIN_ID` | `feed_runner.py` | `CULLOWHEE_CK` |
| `SYNOPTIC_TOKEN` | `gov_gauges.py`, `live_rainfall.py` | HADS/RAWS gauges; optional, degrades gracefully |
| `NOAH_SYNOPTIC_KEY`, `NOAH_SCO_KEY` | `feeds.py` | Synoptic open-access tier; NC SCO Cardinal/CLOUDS |
| `QPF_LEDGER_DB` | `ledger/ledger_db.py` | falls back to `/var/lib/noah/qpf_ledger.db` |
| `BACKFILL_START/END/MAX_MINUTES` | `cucn7_backfill.py` | backfill window + budget |

Never commit a token. `NOAH_data_source_decisions.md` already notes that keys
pasted into chat should be rotated.

## Style and conventions

* **Module docstrings are the documentation.** Every module opens with a long
  header explaining *why it exists*, what it supersedes, what is still open, and
  the honest caveats (orographic under-catch, tipping-bucket failure in intense
  precip, upstream-gauge geometry). Match that register — when you add a module
  or change behavior, update the header, including the caveats. Terse code with
  no header is out of place here.
* **Tag provenance inline.** `[MEASURED]`, `[GOV_ESTIMATE]`, `[MODELED]`,
  `[PLACEHOLDER]`, `[SET]`, `[CONFIRM]`, `[calibrate vs events]`. Leave open
  items flagged rather than silently resolved.
* Pure-logic modules (`flood_engine`, `flood_rating`, `lead_time`,
  `orographic`, `wetness`, `basins`) are **stdlib-only and network-free at
  import time**. Keep the I/O in `feeds.py` / `sources.py` / `gov_gauges.py`.
  This is what makes the engine unit-testable without sensors.
* Severity ladder is always `NORMAL < WATCH < WARNING < EMERGENCY` (plus `N/A`),
  with a consistent color map (`#1A7A52 / #C08A00 / #C2410C / #B42318`).
* Units are US customary throughout the engine (ft, cfs, mi², inches); MRMS is
  the exception and stores mm. Elevations are NAVD88 (the VERTCON grid in
  `data/vertcone.gtx` converts the TVA 1983 NGVD29 profiles).
* Optional heavy dependencies must degrade gracefully — `feeds.py` imports
  `xarray`/`cfgrib`/`fsspec` only for gridded loaders and keeps working without
  them. Follow that pattern.

## Git

* Branch work as instructed; `main` is the deployed branch and GitHub Pages
  source.
* **The history is dominated by `feed: <timestamp>` commits from `feed-bot`**
  (~38 of the last 400 in a quiet week; far more during storms). Don't rebase or
  rewrite across them, and don't be surprised when `git pull` needs a rebase —
  the workflow itself uses `git pull --rebase --autostash origin main`.
* Human commit messages are short, imperative, and lead with the subsystem:
  `ffg_at: never read the stretched render value; gate to plausible inches`,
  `Confirmation tier goes live: gated FIMAN 25380 (CUCN7) stage at Speedwell`.
* Never commit tokens, and never commit a change that would publish a fabricated
  value to the public feed.
