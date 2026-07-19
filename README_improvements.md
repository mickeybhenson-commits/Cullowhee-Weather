# NOAH / Cullowhee Creek — 2026-07 Model Improvement Set

Reviewed & approved by **Dr. Mickey B. Henson, Ph.D.** (Civil & Environmental
Engineering — Hydrology), 2026-07-15. This bundle implements the shippable-now
items from `NOAH_model_improvements.md` (§2, §3, §4) against the authoritative
engine, with a Helene back-test and unit tests. Every number is traceable to
`basins.py` (USGS StreamStats regression + prediction intervals, TVA rating) or
to the WCU Helene study ground truth — nothing is fabricated.

## What changed, and why

The Helene back-test exposed a **safety defect**: at the correct drought
antecedent, the deployed engine tagged four of eight basins as only **WARNING**
during Helene when every basin should have been **EMERGENCY**. The cause was that
the rectangular Manning rating collapses above bankfull (it returns ~4–5 ft of
stage where the FIS/HEC-RAS 100-yr is ~10.8 ft above the bed), and the seven
non-campus thresholds were `bankfull × (1.0, 1.5, 2.0)` placeholders riding that
broken stage scale. This set corrects it.

| # | Improvement | Where | Status |
|---|-------------|-------|--------|
| §2 | Non-campus reaches classify by **discharge return-period** (WATCH ≥2-yr, WARNING ≥10-yr, EMERGENCY ≥100-yr) from USGS regression flows, sidestepping the invalid out-of-bank stage. Campus keeps its **validated** TVA 7/9/11 ft stage. | `flood_rating.py` | **engine default** |
| §3 | Every non-campus posture carries a **confidence band** from the USGS 90% regression prediction interval, plus an **input-uncertainty ensemble** (QPF ±25%, wetness ±0.15). | `flood_rating.pi_band`, `flood_ensemble.py` | live |
| §4 | Per-basin **lead time** (Tc ≈ time-to-peak) and **lead-limited flags** against the 120-min operational requirement. | `lead_time.py` | live |

The data-blocked items (§5 of the memo — surveyed stage thresholds, real
cross-section ratings, orographic QPF) are unchanged and still handled by
`bfe_to_thresholds.py` when the HEC-RAS shapefiles arrive. The frequency
classification is the interim, and the surveyed thresholds will later add a
second, independent stage check.

## Files

| File | Role | Changed? |
|------|------|----------|
| `basins.py` | Source-tagged basin registry (reg_q, reg_pi, TVA, calibration). Added `tc_min`/`tc_src` per basin and `LEAD_REQ_MIN`. | **updated** |
| `flood_rating.py` | Authoritative posture engine. §2 frequency classification is now the default for non-campus reaches; §3 PI band added; legacy stage posture retained as `posture_stage()` cross-check. | **updated** |
| `cwm_model.py` | Runnable rainfall → runoff → unit-hydrograph → raw peak Q front-end. Drives the back-test and ensemble. | unchanged |
| `lead_time.py` | §4 lead-time flags + report. | **new** |
| `flood_ensemble.py` | §3 input-uncertainty ensemble. | **new** |
| `backtest_helene.py` | §1/§2 validation against Hurricane Helene. | **new** |
| `test_improvements.py` | Unit tests (18) for §2/§3/§4 + back-test. | **new** |
| `bfe_to_thresholds.py` | §5 surveyed-threshold emitter (runs on shapefile arrival). | unchanged |

`cwm_classify.py` from the prior drop is **superseded** — its frequency
classification and PI logic now live in `flood_rating.py`, which reads
`basins.py` `reg_pi` directly instead of carrying an embedded copy. It can be
removed from the repo.

## How to run

```bash
python flood_rating.py        # engine self-test (frequency + PI band, all 8 nodes)
python backtest_helene.py     # Helene validation: §1 ground truth + §2 defect/fix
python lead_time.py           # §4 lead-time table
python flood_ensemble.py      # §3 input-uncertainty ensemble at Helene forcing
python -m unittest test_improvements -v   # full test suite (18 tests)
```

`backtest_helene.py` exits 0 only if it reproduces the WCU ground truth (≈40%
runoff, campus EMERGENCY at 11.2 ft, ~150–190-yr flow) **and** raises exactly the
four under-warned reaches (UP-503, TIL-705, MS-1100, SPD-1830) from WARNING to
EMERGENCY — so it doubles as a CI gate.

## Integration note

`flood_rating.assess(model_peak_q_cfs, bid)` is the single entry point. It now
returns the operative `posture` (frequency-based for the seven non-campus
reaches, validated stage for the campus, `N/A` for the mouth), plus `rp_best`,
`rp_band`, `confidence`, and — as cross-checks — `depth_ft` and `stage_posture`.
Callers that previously read `posture` keep working; the value is now the safer
frequency call for non-campus reaches. Feed it the **raw** model peak (e.g.
`cwm_model.assess(...)["qp_raw"]`); the engine applies the per-basin regression
calibration itself.

## Reconstructed Helene forcing

QPF = 10 in (≈48-hr storm total, WCU study) on antecedent wetness = 0.25
(drought-dry). This reproduces the independent ground truth and is the anchor for
the back-test and ensemble.
