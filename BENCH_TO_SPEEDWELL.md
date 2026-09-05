# From the bench in Belk to FIMAN at Speedwell — what to write, and what happens

The readiness chain reads sensors through `sources.resolve()`. It does not care whether a
reading came from Firestore or from a file; it cares that the row has the five fields
below, that the timestamp is fresh, that the value is plausible, and that it is not a
test packet. This page is the whole contract for the BME280 bench test and the move to
Speedwell.

## Step 1 — bench test in Belk (BME280)

Write one document per reading to Firestore, project `ee-dashboard-477704`, database
`cullowhee`, collection `sensor_readings` (the defaults in `sources.FirestoreBackend`):

```json
{
  "basin":    "BENCH-BELK",
  "quantity": "temp_c",
  "value":    22.4,
  "ts":       "2026-09-08T14:05:00Z",
  "source":   "BME280 bench Belk 104",
  "test":     true
}
```

One document per quantity: `temp_c` (°C), `rh_pct` (0–100), `press_hpa` (station pressure,
not sea-level corrected). `ts` is ISO-8601 UTC (a Firestore Timestamp is fine too).

**Two guards, either is enough, use both:** `basin` starting with `BENCH` and `test: true`.
The resolver drops such rows unconditionally, so the bench can run for weeks writing real
Firestore documents without ever lighting a row on the public card. That is the node
contract's rule — a test packet must be impossible to mistake for real — applied at the
reader, not just the writer.

**How you know it arrived:** the documents are visible in the Firestore console, and
`python -c "import sources; print(sources.FirestoreBackend().latest('temp_c','BENCH-BELK'))"`
returns `None` *by design* (the guard). To see the raw row, query the collection directly
or temporarily write a second copy with `basin: "CC-SPD-1830"` and `test: true` — the guard
still drops it, but you can watch it land.

If Firestore credentials are not ready on the day, the file path works identically:
append the same rows to `feed/noah/readings.json` (`{"readings":[...]}`) — see `READINESS.md`.

## Step 2 — move to Speedwell (beside FIMAN 25380)

Same documents, two changes:

```json
{ "basin": "CC-SPD-1830", "quantity": "temp_c", "value": 21.0,
  "ts": "2026-09-15T13:00:00Z", "source": "NOAH SPD-01 BME280" }
```

`basin` becomes the real sub-basin id and the `test` flag goes away. Nothing else changes
anywhere. On the next feed cycle (≤ 30 min) the Speedwell row on the readiness card shows
`env` among its reporting sensors, and the same path is then open for the instruments that
matter to the posture: `stage_ft` (80 GHz radar — this unlocks WARNING/EMERGENCY for the
reach), `soil_moisture_pct` (TEROS — this moves the inches-to-trip line), `rain_1h`.

## What the reader enforces (so a bad packet cannot hurt)

| check | rule | on failure |
|---|---|---|
| freshness | `temp_c/rh_pct/press_hpa` ≤ 60 min; `soil` ≤ 6 h; `stage_ft` ≤ 12 min; `rain_1h` ≤ 20 min | falls to the next source; card tooltip says "stale: N s old" |
| range | temp −35…50 °C · RH 0…100 · pressure 850…1060 hPa · stage 0…40 ft · soil 0…100 % | falls back; tooltip says "out of range" |
| test | `test: true` or basin `BENCH*` | ignored, silently, always |
| future timestamp | > 60 s ahead of the reader's clock | rejected ("timestamp in the future") — check the node's RTC |

Basin ids: `CC-COX-097  CC-LB-171  CC-UP-503  CC-TIL-705  CC-MS-1100  CC-SPD-1830  CC-WCU-2260  CC-MOUTH-2340`.
Quantity names: `stage_ft  soil_moisture_pct  rain_1h  rain_storm  rain_5day  wind_speed_mph  wind_dir_deg  temp_c  rh_pct  press_hpa`.

## Downlink — the node listens for the mode

Uplink is the file/Firestore contract above. Downlink is one number. Each feed cycle
`feed/readiness.json` carries `mode` (`QUIET` / `ATTENTION` / `STORM`) and `cadence`:

```json
{ "mode": "ATTENTION", "mode_since": "2026-10-03T13:00:00Z",
  "cadence": { "feed": 15, "fiman": 15, "node_stage": 10, "node_soil": 60, "node_rain": "on tip" } }
```

The gateway (Starlink primary, cellular backup, GEO secondary) reads that file — or the
same document mirrored to Firestore `system/readiness` — once per cycle and re-programs
its LoRa nodes: stage every `node_stage` minutes, soil every `node_soil` minutes, rain on
every bucket tip regardless. Rules: a node that has not heard a mode for 6 h falls back to
**STORM** cadence, not QUIET (silence is never read as calm); a mode change takes effect at
the node's next check-in, never by interrupting a reading in progress; and the mode never
alters what the node measures or how it reports — only how often. A bench node
(`BENCH*` / `test: true`) follows the cadence too, so the bench test exercises the downlink.

## Suggested acceptance for the Speedwell install

1. Three consecutive feed cycles show `env` reporting for CC-SPD-1830 with no rejections.
2. BME280 pressure trend agrees with K24A (Jackson County Airport) within ~2 hPa after
   elevation correction — the first sanity check on the sensor and on the timestamp path.
3. The gateway's log shows a cadence change within one feed cycle of a mode change
   (force one on the bench by editing `feed/readiness.json` locally).
4. Then the radar stage: compare against FIMAN 25380 at the same minute for 48 h before
   the Speedwell row is allowed to confirm — that is the datum tie the whole system has
   been waiting for.
