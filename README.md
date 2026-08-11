# NOAH — Cullowhee Creek flood warning

A headwater flood-warning system for Cullowhee Creek, Jackson County, North
Carolina. Eight sub-basins, modelled per reach, moving toward a real-time
sensor network.

**This is a research pilot, not an official warning source.** For official
watches and warnings use the National Weather Service (Greenville-Spartanburg)
and NC Emergency Management. Nothing here supersedes them.

---

## Scope

> Every bit of Cullowhee Creek and tributaries need to be protected. No lost lives.

The design target is the **whole channel network**, not one warning point.
Eight pour points are eight samples of a continuous creek — people live between
the samples. Under-warning and over-warning are not symmetric: a false alarm
costs credibility, a missed warning costs a life. Where a design choice trades
one against the other, the repo says so explicitly and defaults toward warning.

Full statement: `claude/NOAH_SCOPE_no_lost_lives.md` in the project docs.

## The two-tier rule

The single most important invariant in this system:

| tier | evidence | maximum posture |
|---|---|---|
| **Outlook** | soil wetness + rainfall forecast | **WATCH** |
| **Confirmation** | measured stream stage | WARNING / EMERGENCY |

A WARNING asserts that flooding *is happening*. "Heavy rain is forecast" is not
that claim. Forecast evidence earns a WATCH and no more; WARNING and EMERGENCY
require a measured rise. The forecast is not hidden when capped — it surfaces as
a dashed ring, an Outlook column, and a header readout.

Corollary, and the current binding constraint: **the watershed has one measured
stage** (NCEM FIMAN 25380 at Speedwell, datum untied and so used categorically).
It is the only input that can raise anything above WATCH anywhere.

A second invariant, learned the hard way: **absent data must render as absent,
never as severe.** A default that is also the worst case turns any upstream
outage into a false EMERGENCY.

## The eight sub-basins

Areas are cumulative — the Mouth contains all seven others, the Campus six.

| node | reach | DA mi² | lead (Tc, min) |
|---|---|---|---|
| `CC-COX-097` | Cox Branch (flashiest) | 0.97 | 29 |
| `CC-LB-171` | Long Branch | 1.71 | 36 |
| `CC-UP-503` | Upper Cullowhee (headwaters) | 5.03 | 40 |
| `CC-TIL-705` | Tilley Creek | 7.05 | 62 |
| `CC-MS-1100` | Mainstem above Speedwell | 11.0 | 63–86 * |
| `CC-SPD-1830` | Speedwell | 18.3 | 62–91 * |
| `CC-WCU-2260` | WCU campus (warning point) | 22.6 | 127 |
| `CC-MOUTH-2340` | Mouth (Tuckasegee confluence) | 23.4 | 147 |

\* open disagreement between the engine and the registry — see *Known limits*.

Six of eight are **lead-limited** (Tc under 120 min). That is the real
per-basin difference and the operationally useful one.

## How the model works

Same seven steps for every basin; only the parameters differ.

1. **Rain** — observed/forecast hyetograph (not a design storm)
2. **Wetness `w`** — 30-day antecedent precipitation index, or soil-moisture percentile
3. **Curve number** — `cnFromWetness(CN2, w)`, continuous between ARC-I and ARC-III
4. **Runoff** — NRCS curve-number equation
5. **Peak** — NRCS dimensionless unit hydrograph (PRF 484), convolved
6. **Calibration** — `Q = a·Qp^b`, fitted per basin to USGS StreamStats 10-yr and 100-yr
7. **Posture** — campus by surveyed stage (**7 / 9 / 11 ft**, field-validated:
   11 ft = water in the road); every other reach by discharge return period
   (≥2 yr WATCH, ≥10 yr WARNING, ≥100 yr EMERGENCY; Cox and Long Branch drop
   WATCH to 1.5 yr for short lead)

`basins.py` is the registry (data, source-tagged). `flood_rating.py` is the
engine. `live.html` carries a JS port of the same chain.

## Validation

Hurricane Helene, 2024-09-27, is the anchor event. Five surveyed NC Geodetic
Survey high-water marks on Cullowhee Creek itself (±0.05 ft) put the campus peak
at **~2,274 cfs, ~9-yr, ~8.4 ft** — with ~200-yr *rainfall* at a 0.41 runoff
ratio. Peak frequency and rain frequency are not the same thing here, and the
difference is the storm's 48-hour shape.

That result is why warning operations use the observed hyetograph and never a
design storm on a storm total.

## Telemetry (in build)

Sensor → Heltec V4 LoRa node → mesh (0–3 hops) → gateway → Blues
WiFi ▸ Cellular ▸ Skylo satellite → Notehub → Firestore. Engine, ledger and
live pages read Firestore as the single source of truth.

Planned: 16 stage nodes (inlet + outlet per sub-basin), 8 rain gauges, TEROS
multi-depth soil profiles, anemometer. Gateway power autonomy is part of the
spec — Helene took power and cell down together for weeks.

## Known limits

Stated plainly because a flood model that hides its weak points is worse than
one that doesn't.

- **One measured stage** in the whole watershed, datum untied. Everything above
  WATCH depends on it.
- **`CC-UP-503` runs on arithmetic thresholds** — the largest incremental area
  and the steepest basin, still awaiting a hand-drawn centerline.
- **`thr_ft` is a placeholder** for seven of eight reaches (bankfull ×1.0/1.5/2.0).
  Only the campus ladder is field-validated.
- **Tc disagrees** between the engine and `basins.py` for `CC-MS-1100` and
  `CC-SPD-1830`. The calibration was fitted against the engine's values.
- **The calibration is fitted off-shape.** `calib` is a two-point power law fitted
  on SCS Type II design storms, now applied to real-hyetograph peaks 2.5–4.6×
  smaller than either fitted point. See
  `experiments/wetness_vs_shape/noah_calib_fitted_offshape_2026-08-11.md`.
- **Basins move in lockstep** — forcing comes from point forecasts inside a
  23 mi² watershed. Basin-averaged radar QPE is the open fix.
- **Manning's *n* is uncalibrated**, and LiDAR sections predate Helene's channel
  change.

## Layout

| path | what |
|---|---|
| `basins.py` | sub-basin registry — areas, ratings, thresholds, provenance |
| `flood_rating.py` | posture engine (frequency + stage paths) |
| `flood_network.py` | routing and the two-tier posture rule |
| `live.html` | public map (GitHub Pages) |
| `streamlit_app.py` | operator panel |
| `scripts/` | LiDAR cross-section cutting, runbooks |
| `ledger/`, `feed/` | published state and feed history |
| `experiments/` | analyses that are not part of the operational path |

## Provenance

Source ladder throughout: **MEASURED > GOV_ESTIMATE > MODELED**. Regression
flows are USGS StreamStats (NC SIR 2023-5006); profiles are FEMA FRIS-RAS and
TVA 1983; high-water marks are NCGS and USACE. Every threshold carries a
`thr_src` tag saying whether it was surveyed, validated, or assumed.
