# NOAH — Environmental Gauge Roster (beyond rain)

*Reachable, public (or already-owned) sensor networks that predict bad storms / floods for the Cullowhee Creek watershed, and how each maps into the NOAH tiers. Compiled July 2026.*

Flood prediction asks two separate questions, and the useful signals split cleanly between them:

- **Atmospheric — "how extreme is the storm coming?"** (rain, PWAT, radar, steering wind)
- **Land state — "how much of that rain becomes a flood?"** (soil moisture, groundwater, antecedent streamflow)

NOAH's own Helene research is the reason to weight the second column heavily: only ~40% of Helene's rain left as discharge — **60% went into storage**. The watershed's *state before the storm* decided the outcome as much as the rain did. So the highest-leverage additions here are the land-state gauges, and the best of those you already own.

---

## Priority ranking (what actually changes a decision)

1. **WCU's own soil-moisture + groundwater network** — closest, richest, already yours. → measured soil/storage into `sources.resolve()`.
2. **MRMS radar QPE** — measured, gauge-corrected *spatial* rain over each basin. → measured storm rain into `compute_from_response()`.
3. **USCRN Asheville** — gold-standard soil moisture to de-bias the modeled bucket.
4. **PWAT (GSP sounding + gridded)** — the "extreme-storm inbound" outlook flag.
5. **USGS / NC-DEQ groundwater** — regional augmentation if the WCU wells aren't enough.

---

## 1. Soil moisture — *the* #1 predictor of whether rain floods

**NOAH tier:** measured replacement for the modeled water-balance bucket → `sources.resolve(Q_SOIL)`. Regional stations also serve as a calibration reference to de-bias the bucket the way `wetness.py` already uses a soil percentile.

| Source | Nearest to Cullowhee | What it gives | Access |
|---|---|---|---|
| **WCU field network** (5 soil-moisture sites + planned TEROS probes) | on-watershed | in-basin volumetric soil moisture — the real thing the bucket is standing in for | already yours (internal feed) |
| **USCRN Asheville 8 SSW** (NC Arboretum / Bierbaum) | ~40 mi E | research-grade soil moisture + temp at 5 depths (5/10/20/50/100 cm), hourly, triple-redundant | NCEI USCRN hourly product (public HTTP/FTP, no key) |
| **USCRN Asheville 13 S** (Mtn. Horticultural Crops Res. Ctr. / Backlund) | ~45 mi NE | same 5-depth soil moisture; also a QC'd precip reference | NCEI USCRN |
| **NC ECONet** (Mountain Research Station–Waynesville, UNCA-Asheville, others) | ~30 mi NE | station soil moisture + full met; denser regional coverage | NC State Climate Office data products/API (free; may need an account) |
| **NASA SMAP** (satellite) | grid over you | coarse (~9 km) surface soil moisture, 2–3 day revisit | NSIDC / public; a large-scale fallback only |

**Honest caveat:** the WCU probes are the true measured replacement; USCRN/ECONet are ~30–45 mi away at different elevations, so use them to correct the *trend/percentile* of the bucket, not as the literal basin value.

---

## 2. Groundwater / water-table depth — the direct measure of your "60% to storage"

A high antecedent water table = little storage left = the next storm runs off faster. This is the flashiness multiplier the rain gauges can't see, and NOAH's recession-curve data already shows larger basins drain slowly (storage matters longer).

**NOAH tier:** a new *antecedent-storage* input — either folded into basin wetness/CN, or an outlook priming term ("storage nearly full → escalate the runoff response for a given rain").

| Source | Nearest | What it gives | Access |
|---|---|---|---|
| **WCU wells** (40+, monitored since 2010) | on-watershed | real water-table depth across the study basins — the closest, longest, most relevant record, and it's yours | already yours (internal feed) |
| **USGS Groundwater Climate Response Network** (GA/NC/SC) + Piedmont–Mountains GW project | sparse in far-west NC | real-time well water levels | USGS Water Services (`waterservices.usgs.gov`, `gwlevels`/`iv`), no key — query by county/bbox to find the nearest active well |
| **NC DEQ-DWR groundwater network** | regional | state monitoring wells | ncwater.org |

**Honest caveat:** the groundwater→runoff relationship needs calibration before it moves a warning; your Helene recession curves are exactly the data to fit it. Start by *displaying* it, then wire it in once the relationship is fit.

---

## 3. Atmospheric moisture (PWAT) — the "extreme-storm inbound" early flag

Precipitable water — total water in the air column — spikes hours ahead of extreme-rain events when tropical/atmospheric-river moisture arrives. Helene ran on anomalously high PWAT.

**NOAH tier:** an **outlook modifier**, not a trigger. A high PWAT anomaly (e.g. > ~1.75", or > ~90th percentile for the season) should *lower the rain-outlook thresholds / raise the ceiling* — "load the model toward the high end because the atmosphere is primed." Pairs naturally with the upwind-gauge WATCH already built.

| Source | Nearest | What it gives | Access |
|---|---|---|---|
| **NWS radiosonde KGSP** (Greenville-Spartanburg, Greer SC) | ~80 mi SE | twice-daily sounding → PWAT, the standard measure | NWS/NCEI upper-air (public) |
| **Radiosonde KRNK** (Blacksburg VA) | ~150 mi NE | second sounding for the region | NWS/NCEI |
| **GPS-Met / SuomiNet** (GNSS-derived PWAT) | check for a site near GSP/Asheville | *continuous* PWAT (vs twice-daily balloons) | UCAR SuomiNet / NOAA GPS-Met |
| **Gridded PWAT** (SPC mesoanalysis / RAP, GOES-TPW) | grid over you | continuous, spatial — best operational option | SPC / NOAA public grids |

**Honest caveat:** a point sounding 80 mi away twice a day is coarse; the gridded PWAT (SPC/GOES) is the better operational feed. PWAT tells you the atmosphere *can* produce extreme rain, not that it *will* over your basins.

---

## 4. MRMS radar QPE — the best measured *spatial* rainfall (fixes gauge sparsity directly)

MRMS (Multi-Radar Multi-Sensor) fuses every nearby NEXRAD (KGSP Greer, KMRX Morristown) with gauges and models into a gauge-corrected, 1-km, sub-hourly rainfall grid. This is the measured rain *over each basin polygon* — the thing that most directly attacks the orographic-QPF-bias problem the whole system is built around.

**NOAH tier:** measured storm rain → area-average MRMS over each basin → `sources.resolve(Q_RAIN_STORM)` as MEASURED, feeding `compute_from_response()` directly. **Arguably the single biggest model upgrade in this whole list** — real rain per basin instead of a point gauge or a forecast.

| Product | What it gives | Access |
|---|---|---|
| **MRMS MultiSensor QPE** (1-, 24-hr, gauge-corrected) | 1-km spatial rainfall, updated every ~2 min | NOAA/NSSL + **Iowa Environmental Mesonet** archive & real-time (`mesonet.agron.iastate.edu`) — the same IEM service NOAH already uses for radar tiles + the K24A gauge |
| MRMS raster / OGC-WMS | grid clips per basin | IEM OGC/raster endpoints |

**Honest caveat:** radar QPE *also* under-catches in the mountains (beam blockage, bright-band, terrain below the beam) — but MRMS's multi-sensor, gauge-corrected pass is far better than raw radar, and your gov-gauge arc + K24A are exactly the ground truth to validate/bias-correct it against. Cross-check before trusting it as the basin rain source.

---

## Cross-cutting notes

- **Land-state beats atmosphere for *your* problem.** Soil moisture and groundwater (columns you already instrument) decided Helene per your own data. Prioritize wiring your WCU feeds into `sources.resolve()` over adding more atmospheric inputs.
- **MRMS is the exception** — it's atmospheric-side but it's *measured spatial rain*, and it fixes the exact QPF weakness the codebase keeps flagging.
- **Discipline:** every feed is one more thing to keep alive and calibrate. Add the ones that change a decision (soil, groundwater, MRMS), treat PWAT as a modifier, and don't ingest everything just because it exists.
- **All external feeds share the sandbox caveat:** USGS/NCEI/IEM are reachable from your infrastructure but were blocked from the build sandbox, so validate each with one live pull before trusting the numbers.
