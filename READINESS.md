# Readiness chain — floor → wetness → inches-to-trip → forecast margin

Added 2026-09-05. `readiness.py` runs inside `feed_runner` every publish cycle and writes
`feed/readiness.json`; `storm_watch.html` renders it as the **Readiness** card under the
rain-outlook strip (opens itself when the floor is above NONE).

## The four steps, per basin

| step | question it answers | source today | source after NOAH | tag |
|---|---|---|---|---|
| 1 floor | is rain coming here? | NHC forecast track + HURDAT2 analogs (`corridor_analogs.py`), latched until the storm passes | same | — |
| 2 wetness | how much can the ground still absorb? | live_rainfall ladder (Open-Meteo soil percentile > API30) | TEROS profile in-basin | MOD → **MEAS** |
| 3 inches-to-trip | how much 24-h rain reaches WATCH / WARNING / EMERGENCY at *that* wetness? | `wetness.assess_wet` by bisection (same engine as the outlook feed) | same engine, measured wetness | inherits |
| 4 forecast margin | will the forecast rain reach the trip line? | WeatherNext-2 qpf24/qpf72 + NWS QPF24 (`feed/outlook.json`) | same, bias-checked against in-basin gauges | GOV |
| ceiling | may this reach go above WATCH? | FIMAN 25380 at Speedwell only | radar + pressure stage at every outlet | absent → **MEAS** |

Soil never changes the floor (that is the track's job); the floor never changes the
inches-to-trip (that is the soil's job). The margin is where they meet.

## Filling the gaps — no change to the chain

Every input goes through `sources.resolve()`, which returns the best available source and
its tier, gated for freshness and range. Backends are chained by `readiness.install_backends()`:

1. `noah_readings.FileBackend` — **the deployment contract.** The gateway (or the Notehub
   exporter, or a person with a USB stick during a field test) writes one file,
   `feed/noah/readings.json`:
   ```json
   {"written_utc":"2026-10-03T14:05:00Z","readings":[
     {"basin":"CC-SPD-1830","quantity":"stage_ft","value":2.31,"ts":"2026-10-03T14:00:00Z","source":"NOAH SPD-01 radar"},
     {"basin":"CC-SPD-1830","quantity":"soil_moisture_pct","value":61.0,"ts":"2026-10-03T13:45:00Z","source":"NOAH SPD-01 TEROS 20 cm"}]}
   ```
   Quantities are `sources.Q_*` names. That is all a team has to produce for a basin to
   flip from MOD to MEAS on the card.
2. `sources.FirestoreBackend` — the ingest path; dormant until credentials and documents exist.
3. `fiman_source.FimanStageBackend` — the Speedwell state gage (Q_STAGE_GOV); live today.

A reading that is stale (`FRESH_S`) or out of range (`RANGE`) falls through to the next
source and the card's tooltip says why (`sensor rejected (stale: 32400s old > 21600s limit)`).
Nothing is ever rendered as calm because it is unknown.

## The wake-up call — alarms → mode (added 2026-09-05)

The storm track is not a posture; it is a reason to start looking. `readiness.build()`
now publishes a **mode** with the list of alarms that put it there, so the card and the
operator message both say *why*:

| alarm | source | rings ATTENTION | rings STORM |
|---|---|---|---|
| `corridor` | NHC track + HURDAT2 analogs (step 1) | ANALOG | ELEVATED, WATCH_PENDING |
| `wpc_ero` | WPC Excessive Rainfall Outlook days 1–5 over the watershed envelope (`wpc_ero.py`, GOV EST) | Marginal, Slight | Moderate, High |
| `wetness_trend` | campus wetness history kept in `feed/readiness_state.json` | rising ≥ 0.04/day for 3 days and now ≥ 0.6 | — |
| `forecast_margin` | to-WATCH minus forecast 24-h rain (step 4) | p90 reaches the line | p50 reaches the line |

Mode = the highest rung any alarm asks for: **QUIET** (nothing ringing) → **ATTENTION**
(start looking) → **STORM** (sample fast). The WPC outlook is the broad one — it covers
stalled fronts, training cells, upslope and predecessor rain, cut-off lows and tropical
remnants, so a system that only watched named storms is no longer blind to the rain types
that actually flood this creek. A mode changes **only how often the system looks**; it never
moves the posture ladder, never lowers a threshold and never lifts the WATCH cap. Output
fields: `mode`, `mode_since`, `prev_mode`, `alarms[{name,mode,detail}]`, `cadence`, `ero`,
`wetness_trend`. `notify_posture.check_mode()` sends one ntfy message per mode change
("FRESHET STORM — start looking" / "FRESHET QUIET — standing down") with the alarms, the
campus ground state and the cadence.

Recommended cadence per mode (`readiness.CADENCE`, minutes):

| mode | feed / FIMAN poll | node stage | node soil | rain |
|---|---|---|---|---|
| QUIET | 30 / 30 | 15 | 360 | on tip |
| ATTENTION | 15 / 15 | 10 | 60 | on tip |
| STORM | 15 / 15 | 5 | 60 | on tip; satellite backhaul reserved for stage exceedances |

The feed side follows this today by re-reading `readiness.json`; the node side is the
downlink clause in `BENCH_TO_SPEEDWELL.md` (the gateway reads the mode and re-programs its
nodes).

## Rules carried through

- Forecast evidence tops out at **WATCH**. `outlook_level` is the rung the p50 rain would
  reach, capped. Only a measured stage (green row) can confirm WARNING / EMERGENCY.
- The mouth has no ladder by decision (backwater from the Tuckasegee) → `trip_in: null`, `N/A`.
- `feed/corridor_events.json` is produced here (a storm inside the box is recorded once by
  name and date) and feeds the **SEQUENCE** flag: a crossing within 14 days means the next
  storm falls on wet ground. It is shown; it lowers no threshold by itself yet — the wetness
  term already carries the predecessor rain if it is inside the 30-day window, and whether
  to add an explicit sequence term is a decision for Mickey.

## Verification

`test_improvements.TestReadinessChain` (13 tests, in the CI suite): default everything
MODELED and capped; trip inches ordered and monotone in wetness; a measured soil reading
flips the tag, moves the trip line and unlocks the ceiling for that basin only; a stale
sensor falls back and says why; the file backend contract; the in-corridor hold and the
Helene-shaped segment test; the bench/test guard; QUIET when nothing rings; the corridor,
WPC-ERO, wetness-trend and forecast-margin alarms each set the mode they should and never
touch the ceiling; `mode_since` survives cycles; `wpc_ero.fetch` never raises. `test_wired.py` passes with `readiness.py`, `noah_readings.py`
and `corridor_analogs.py` reachable from `feed_runner`.

## Open

- A rain-versus-forecast alarm (MRMS observed vs QPF / flash-flood-guidance ratio) is the
  one alarm still missing from the table above; it needs an MRMS reader the repo does not
  have yet.
- The floor here is computed by `readiness.py` from NHC directly (the VM's
  `synoptic_watch.py` is not in this repo). Until the two are reconciled the card's floor
  and the badge's floor come from two implementations of the same rule; they agree by
  construction today (same gate, same segment test, same analog grid).
- Trip inches use the 24-h Type II shape (`assess_wet`); `rain_to_trip.html` uses the
  Helene hourly shape and user-chosen durations. The numbers differ by design; the card's
  are the conservative planning figure at today's wetness.
- `feed/readiness.json` and `feed/corridor_events.json` were seeded offline on 2026-09-05
  (floor UNKNOWN in the seed); `feed_runner` overwrites both on its next cycle.
