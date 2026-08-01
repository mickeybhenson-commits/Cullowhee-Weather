# Cullowhee Creek — live per-basin flood forecast

This documents `forecast.py`, the layer that turns the validated engine into a
running forecast for the watershed **and each of its eight basins**.

## What was missing

The science core and the live data connectors both already existed, but nothing
joined them:

| Piece | State before |
|---|---|
| `basins.py`, `flood_rating.py`, `cwm_model.py` | working, validated against Hurricane Helene |
| `feeds.py` live connectors (USGS, NWS alerts, FFG, NWM, FIMAN) | working |
| `feed_runner.get_modeled_stage_ft()` | `return None` — an unfilled `TODO` |
| `test_model.py` | **absent from the repo** |
| `live_rainfall.py`, `outlook_engine.py`, `pages/1_Test_Model.py` | dead on import (`import test_model`) |
| `wetness.py`, `outlook_engine.py` | dead on import (`from flood_rating import posture`, a name that no longer exists) |

The consequence was visible in the published feed: `feed/state.json` read
`"source_tier": "modeled", "source_note": "no source"` with one stale site and
no per-basin forecast anywhere. In `streamlit_app.py` the live-forcing hook was
wrapped in a bare `except Exception: pass`, so the broken import degraded
silently and the app fell back to priming.

## The chain

```
live rainfall ─┬─ QPF: rolling 24-h MAXIMUM over the forecast window
               │    (matches the 24-h SCS Type II design hyetograph the engine
               │     integrates — a 3-day sum poured into a 24-h storm would
               │     invent intensity no forecast called)
               └─ antecedent: 30-day decayed API ─→ wetness index w ∈ [0,1]

w + CN2        ─→ continuous curve number                        wetness.py
QPF + CN       ─→ SCS hyetograph → NRCS-CN runoff → unit
                  hydrograph → raw peak Q                        cwm_model.py
raw peak Q     ─→ per-basin regression calibration → return
                  period → POSTURE + USGS 90% PI band            flood_rating.py
calibrated Q   ─→ baseflow-inclusive total stage                 wetness.py
QPF × wetness  ─→ 3×3 input-uncertainty ensemble                 flood_ensemble.py
basins.py Tc   ─→ lead time vs the 120-min requirement           lead_time.py
```

## Accuracy anchor

A 4.80-inch 24-hour storm at median wetness reproduces the USGS StreamStats
regression **10-year** flow across all eight basins:

| Basin | modeled | regression 10-yr |
|---|---:|---:|
| CC-UP-503 | 671 | 705 |
| CC-TIL-705 | 925 | 927 |
| CC-MS-1100 | 1,332 | 1,330 |
| CC-SPD-1830 | 2,013 | 2,010 |
| CC-COX-097 | 186 | 186 |
| CC-LB-171 | 295 | 294 |
| CC-WCU-2260 | 2,380 | 2,380 |
| CC-MOUTH-2340 | 2,455 | 2,450 |

Only the campus is exact by construction (the design depth is derived from it);
the other seven are independent, which is what makes the per-basin calibration
credible rather than curve-fitted. `test_forecast.py` pins this down.

## Posture basis differs by reach — deliberately

* **WCU campus (`CC-WCU-2260`)** — field-**validated** stage ladder, 7/9/11 ft
  (11 ft = water in the road). The only reach with a surveyed receptor and the
  only genuinely out-of-bank one. This is also the value published as the
  modeled stage, because `publish_feed.LOCAL_LEVELS` and `flood_engine.THRESH`
  are those same three numbers.
* **The six tributary/mainstem reaches** — **discharge return period** against
  the USGS regression (WATCH ≥ 2-yr, WARNING ≥ 10-yr, EMERGENCY ≥ 100-yr; Cox
  and Long Branch drop WATCH to 1.5-yr). Their stage thresholds are still
  bankfull placeholders and the rectangular Manning rating collapses above
  bankfull, so frequency classification sidesteps an invalid stage scale.
* **The mouth (`CC-MOUTH-2340`)** — out of scope. It floods by backwater from
  the Tuckasegee, which the creek's own rating cannot represent. Its creek-side
  frequency is reported for context and excluded from the watershed roll-up.

## Failure behaviour

`run()` never raises and never invents a number. On any fetch or compute
failure it returns `{"ok": False, "error": ...}`; `modeled_stage_ft()` returns
`None`; the publisher emits `CONDITION_TXT="No Data"`. A missing forecast is a
legitimate answer — a plausible fake one on a public URL is the failure mode
this design exists to avoid.

## Outputs

* `feed/forecast.json` — every basin (posture, return period, confidence band,
  ensemble, stage, lead time) plus the watershed roll-up. Written every publish
  run by `feed_runner.publish_forecast()`.
* `feed/state.json` — now also carries `watershed_posture`.
* `pages/2_Basin_Forecast.py` — the eight-basin view, live or scenario.

## Known inconsistency, surfaced not hidden

`basins.py` `tc_min` and the engine's hydrograph `Tc` agree on six of eight
reaches. **CC-MS-1100** (86 vs 63) and **CC-SPD-1830** (91 vs 62) disagree, and
both are reaches whose `tc_src` records a competing NRCS-wet estimate. The
engine value is **not** silently reconciled: the per-basin calibration anchors
were fit at it and the Helene back-test validates against them, so changing it
would invalidate a validated engine. Every basin record carries both `tc_min`
and `tc_model_min` plus a `tc_consistent` flag, and
`test_forecast.TestPerBasinChain.test_tc_consistency_is_reported_not_hidden`
pins the discrepancy so it cannot drift — or vanish — unnoticed. Resolving it
means re-deriving Tc and re-fitting those two calibrations together.

## Running it

```bash
python forecast.py                  # live run (needs network)
python forecast.py --demo 4.8 0.5   # offline: force QPF=4.8 in, wetness=0.50
python -m unittest test_forecast -v # 35 tests
streamlit run streamlit_app.py      # app; see "Basin Forecast" in the sidebar
```

## Authority

Shadow mode. **NWS (WFO GSP) is the warning authority** and **NCEM FIMAN is the
authoritative gage record.** This is a Jackson County forecast overlay intended
to sit alongside FIMAN, not replace it. Forecast QPF under-calls orographic
mountain rainfall, so every posture here is a floor, not a ceiling.
