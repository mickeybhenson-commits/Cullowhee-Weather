# NOAH — Data Source Decisions

*Standing decisions on what data NOAH uses. Read before adding or re-exploring a source. Updated July 2026.*

## Rule: government + own instrumentation only

Live warning inputs come from **government/institutional gauges** (USGS, NOAA HADS, interagency RAWS, NWS AWOS) and **WCU's own sensors**. Private/hobby stations (Ambient Weather Network, personal Tempest/Davis, etc.) are **out of scope** for warnings.

Why: hobby stations are uncalibrated, unevenly and often poorly sited (the AWN map showed neighboring stations 20°+ apart — a siting tell), and can't be relied on to stay online or maintained. For a warning system that's a liability, not an asset. *(Explored the Ambient path June–July 2026; the account API returned no stations and the network is unfit for warning use. Retired. Any keys pasted in chat should be rotated.)*

## Live real-time gauge arc (the storm-approach sentinels)

Confirmed real-time, in `gov_gauges.GAUGES`:

| Dir | Station | Network | Notes |
|---|---|---|---|
| S | Highlands 1NW (HDSN7) | HADS/Synoptic | escarpment; first-hit for S/SE surges |
| SW | Raingage at Franklin (USGS 351205083213545) | USGS | real-time, **no key**; prevailing/Helene track |
| SW | Franklin 1N (FNKN7) | HADS/Synoptic | backup on SW |
| W | Cow Mountain RAWS (COWN7) | HADS/Synoptic | Nantahala/Cherokee side |
| W | Fontana Dam (FONN7) | HADS/Synoptic | W/WNW |
| SE | Brevard 2NE (BVDN7) | HADS/Synoptic | matches Lake Toxaway sentinel |
| SE | Guion Farm RAWS (GUIN7) | HADS/Synoptic | near Brevard |

Local anchor: Jackson Co. Airport AWOS **K24A** (IEM), already in `live_rainfall.py`.

## Coweeta = calibration, NOT live

Coweeta Hydrologic Lab is the best hydrologic record in WNC, but it is **not real-time**. Its recording-rain-gauge and gauged-watershed data are curated archives released on delay (USFS RDS; Coweeta LTER via EDI; NOAA COOP `USC00312102` is daily only). There is **no confirmable real-time telemetered Coweeta RAWS** on HADS/Synoptic — an earlier guessed ID (`CWTN7`) was removed.

Decision: use Coweeta's **archive to calibrate the Helene backtest and QPF-bias thresholds**, not as a live feed. Its real-time SW coverage is already provided by the USGS Franklin gage (~10 mi away, same track). Do not add a live Coweeta gauge.

## Open / planned

- **WCU soil-moisture + groundwater feeds** → wire into `sources.resolve()` (highest-value remaining build; land-state was decisive in Helene per WCU's own 60%-to-storage finding).
- **MRMS radar QPE** (IEM) → measured spatial rain per basin; the biggest rain upgrade available.
- **PWAT** (KGSP sounding + gridded) → extreme-storm outlook modifier.
- **NC DEQ Coweeta Groundwater Monitoring station** → check if near-real-time when building the groundwater feed.
- **Calibration** of upwind-WATCH rate/direction thresholds and the `storm_correction` cap/inflow map against `backtest_helene.py`.

See `NOAH_environmental_gauges.md` for the full non-rain roster.
