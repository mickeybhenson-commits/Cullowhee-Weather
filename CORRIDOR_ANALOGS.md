# Corridor analogs — the ANALOG rung and the SEQUENCE flag

Added 2026-09-05. Built from NOAA/NHC HURDAT2 Atlantic best tracks (1851–2025 release of 2026-02-27).

## What changed

**The readiness ladder gains a rung below ELEVATED.**

| floor | evidence | earliest it can fire |
|---|---|---|
| NONE | — | — |
| **ANALOG** *(new)* | of the historical storms that were where this storm is now, moving the way it is moving, at ≥ TS strength, at least 25 % went on to cross the corridor gate within 5 days (n ≥ 8, HURDAT2 1900–2025) | days before an NHC forecast track commits to the corridor |
| ELEVATED | forecast track crosses the gate within 72 h | ~3 days |
| WATCH_PENDING | forecast track crosses the gate within 48 h | ~2 days |

ANALOG is forecast-tier evidence like everything above it: it can never raise a posture beyond WATCH, and it is weaker than a forecast track. It is a heads-up, not a threat.

**SEQUENCE** is a separate flag: the corridor was crossed by a named storm within the last 14 days. The deadly WNC events came in pairs — two storms in July 1916; Frances then Ivan nine days later in 2004 (Peeks Creek); Helene's own predecessor rain. The second storm falls on ground that is already wet. The flag is meant to lower rain-to-trip thresholds through the wetness term; it raises no posture by itself.

**The gate test now runs on track segments, not only on points.** Helene's 12Z best-track point on 2024-09-27 sat on −83.2° exactly and its 18Z point was already north of 36.5° N — a point-in-box test never sees that crossing. NHC forecast points are 12–24 h apart, so the same gap exists in live evaluation. `segmentGateTau()` / `segment_crosses_gate()` interpolate between consecutive points.

**What-if replays now use real HURDAT2 tracks** for Helene 2024, Fred 2021, Ivan 2004, Frances 2004, Opal 1995 and July 1916, starting six days before each storm's gate crossing, with post-tropical winds ≥ 34 kt treated as TS-equivalent. The replay latches the floor once the corridor test is met and releases it only after the storm has passed through the box. While a replay runs, the right-hand panel shows the replayed storm (tagged SIMULATION) and the live rain-outlook strip dims with a note; live data is never touched.

## Files

| file | role |
|---|---|
| `corridor_analogs.py` | builds `feed/corridor_analogs.json` from `data/hurdat_se.json`; also exports `analog_lookup()`, `analog_floor()`, `sequence_flag()`, `segment_crosses_gate()` for the backend |
| `data/hurdat_se.json` | HURDAT2 subset: storms since 1900 with a position in the Southeast box (lat 30–38, lon −92 to −78); six-hourly + landfall points, lat 8–45, lon −100 to −55 |
| `feed/corridor_analogs.json` | 2.5° × 2.5° × 8-sector analog grid `[n, hits]`, the 122 corridor storms with full tracks and first gate crossing, and the parameters |
| `storm_watch.html` | reads the feed; ANALOG rung, segment gate test, real replay tracks, sequence chip, sim panel |
| `feed/corridor_events.json` *(optional, backend-written)* | `{"events":[{"name":"HELENE","date":"2024-09-27"}]}` — recent gate crossings for the SEQUENCE flag |

Rebuild after a new HURDAT2 release:

    python corridor_analogs.py data/hurdat_se.json -o feed/corridor_analogs.json

(`data/hurdat_se.json` is produced from the raw HURDAT2 text in a browser console; the filter is documented in the module docstring. A small standalone converter is a reasonable next step.)

## Spot checks (from the build's self-report)

    Gulf, 26N 87W, heading NNE          n= 23 hits= 12 frac=0.52 -> ANALOG
    E of Florida, 27N 79W, heading NW   n= 26 hits=  9 frac=0.35 -> ANALOG
    Bahamas, 24N 74W, heading NNW       n= 18 hits=  1 frac=0.06 -> NONE
    W Gulf, 25N 94W, heading N          n= 11 hits=  9 frac=0.82 -> ANALOG

Replay results (headless, 3-h steps): every one of the six storms lights ANALOG before ELEVATED except where the forecast track commits first; Helene lights ANALOG at T+9 h from the Caribbean (29 % of 14), ELEVATED at T+24 h, WATCH_PENDING at T+48 h.

## Backend adoption (synoptic_watch.py on the VM — not in this repo)

The page is a display mirror. Until the backend adopts the rung, the badge labels ANALOG as *advisory, display only*. To adopt:

1. `from corridor_analogs import analog_lookup, analog_floor, sequence_flag, segment_crosses_gate` and load `feed/corridor_analogs.json["grid"]`.
2. Replace the point-in-box test with the segment test on consecutive forecast points.
3. When no forecast crossing is found and the storm is ≥ TS, call `analog_lookup(lat, lon, movementDir)`; `analog_floor()` gives NONE or ANALOG.
4. Latch: once ELEVATED/WATCH_PENDING is met, hold it until the storm has passed through the gate box, even if it has decayed below TS.
5. Append each gate crossing to `feed/corridor_events.json`; feed `sequence_flag()` into the wetness term of the rain-to-trip engine (decision for Mickey: by how much — a first proposal is to evaluate the storm at the measured/estimated wetness *plus* the first storm's rain, which is what the API already does if the predecessor rain is in the 30-day window).

## Caveats

- 2.5° cells and 8 sectors are coarse; the pooling ladder (cell → ring → ring + adjacent sectors) reports which level it used, and the storm list prints it. Do not quote a fraction without its n.
- HURDAT2 best tracks are post-season reanalysis; a live storm's position and heading come from NHC's CurrentStorms.json, which is what the lookup uses.
- The 25 % threshold and 5-day horizon are first settings, not fitted. A backtest across all 364 Southeast storms (hits vs. false alarms by threshold) is the obvious next experiment and is straightforward with the data in the feed.
