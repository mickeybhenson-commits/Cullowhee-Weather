# Runbook — cutting surveyed thresholds for the six uncovered reaches

Closes open safety item #1 in `claude/NOAH_SCOPE_no_lost_lives.md`: six of the
seven in-scope reaches, including **every tributary**, have no surveyed channel
geometry and are running on `bankfull x (1.0, 1.5, 2.0)` arithmetic thresholds.

Run on your workstation. A sandboxed session cannot reach 3DEP or NLDI.

---

## 0 · Sync the repo

```
cd C:\Users\micke\Cullowhee-Weather
git status
```

If anything is modified, stash or commit it first — your local copy was last
touched 2026-07-29 and is well behind.

```
git pull
```

Confirm you have the script:

```
dir scripts\xs_from_3dep.py
```

## 1 · Install the one dependency

```
pip install numpy
```

That's all it needs. No rasterio, no pyproj, no GDAL, no DEM download.

## 2 · Self-check — offline, 2 seconds

```
cd scripts
python xs_from_3dep.py --selfcheck
```

Expect:

```
  thalweg elev   100.00   (true 100.00)
  bankfull depth 6.50 ft (true ~6.0)
  Q=  500 cfs -> depth  1.65 ft   (round-trip    500 cfs)
  Q= 2000 cfs -> depth  3.62 ft   (round-trip   2000 cfs)
  Q= 4050 cfs -> depth  5.29 ft   (round-trip   4050 cfs)
  PASS
```

If this fails, stop — the geometry maths is broken and nothing downstream is
trustworthy.

## 2b · Probe NLDI — 10 seconds

The centerline comes from USGS NLDI. Check it answers before spending a run on
it:

```
python xs_from_3dep.py --probe-nldi --nav-km 4
```

Expect a line per pour point:

```
  NLDI https://api.water.usgs.gov/nldi/linked-data  comid 9752106
  CC-SPD-1830       37 flowlines,  1204 vertices
```

The script tries `api.water.usgs.gov` first, then the two legacy
`labs.waterdata.usgs.gov` hosts. If all three fail it prints every attempt.
The old host now 404s — that is why this probe exists.

## 3 · Scouting run — one basin, coarse, ~1 minute

Do **not** start with the full run. Check the sections look like valleys first.

```
python xs_from_3dep.py --basin CC-SPD-1830 --spacing 1000 --width 250 --nav-km 4 --out-dir xs_scout
```

`--nav-km 4` keeps NLDI from returning the whole upstream network on the first
try. Expect roughly 15–30 sections and one line printed per section.

### What good output looks like

```
    CC-SPD-1830_003  thalweg 2118.4 ft  bankfull 2.85  topbank 4.10  d100 6.20
```

- **thalweg** should fall steadily as you move downstream, roughly 2100–2600 ft
  across this watershed
- **bankfull** 1–5 ft. Compare against the Bieger regional values already in
  `basins.py`: SPD 2.71, TIL 2.02, MS 2.32, UP 1.78, COX 1.11, LB 1.31. LiDAR
  usually reads a little deeper, since it finds top-of-bank rather than
  bankfull.
- **topbank** >= bankfull, and **d100** > topbank. If d100 is *below* topbank
  the reach conveys the 100-yr in-bank, which is possible on the steep upper
  reaches but worth a look.

### Red flags and what they mean

| symptom | cause | fix |
|---|---|---|
| bankfull ~0.1 ft | section missed the channel | widen `--width`, or the centerline is offset |
| bankfull > 15 ft | detector locked onto a valley wall | narrow `--width` to 120–180 |
| thalweg jumps 50+ ft between neighbours | sections crossing a different channel or a road cut | inspect those section CSVs |
| "too many gaps, skipped" repeatedly | outside NC QL2 coverage, or 3DEP throttling | re-run; if persistent, raise `PAUSE` in the script |

Open two or three `xs_scout/CC-SPD-1830/*.csv` and plot `station_ft` against
`elev_ft`. You want a recognisable V or U with a flat floodplain either side.
If they look like noise, the centerline is wrong and nothing else matters.

## 4 · Tune width, if needed

`--width` is the single most sensitive knob. 250 ft suits Speedwell and the
mainstem; the small tributaries want less.

```
python xs_from_3dep.py --basin CC-COX-097 --spacing 500 --width 150 --nav-km 3 --out-dir xs_scout
```

## 5 · Production run — all six reaches

```
python xs_from_3dep.py --spacing 300 --width 250 --out-dir xs_out
```

With no `--basin` it targets all six uncovered reaches: Speedwell, Tilley
Creek, Mtn. Lower, Upper Cullowhee, Cox Branch, Long Branch.

**This takes a while.** One 3DEP request per section plus a 0.4 s pause; at
300 ft spacing across the upstream network expect several hundred sections and
10–30 minutes. Note that Speedwell's upstream navigation already includes
Mtn. Lower, Tilley and Upper Cullowhee — they nest — so sections will repeat
across those runs. That is fine; each basin picks its own controlling section.

## 6 · Read the results

```
xs_out\summary.csv            every section: thalweg, bankfull, topbank, d100
xs_out\thresholds_lidar.py    SURVEYED_THR dict
xs_out\<basin>\*.csv          station/elevation per section
```

The script prints the exact `thr_ft` / `thr_src` lines to paste. Each reach
takes its **controlling section** — the one that goes out of bank soonest, not
the average. Under the project scope the weakest point is the one that matters.

## 7 · Update `basins.py`

Replace `thr_ft` and `thr_src` for each of the six. Keep
**`CC-WCU-2260` untouched** — its 7 / 9 / 11 ft is field-validated (11 ft =
water in the road) and no LiDAR cut beats a real observation.

## 8 · Expect the live map NOT to change

Important, or you will think it failed.

`live.html` classifies the seven non-campus reaches by **discharge return
period** (`freqPosture`), not by stage — that was the reviewed 2026-07-15
decision, taken because the rectangular stage rating is invalid out of bank.
So new `thr_ft` values will **not** move the basin colours.

What they *do* give you immediately:

- an honest stage number in the "~RP / stage" column
- `thr_src` that says SURVEYED instead of PLACEHOLDER
- the real section geometry, which is what `bfe_to_thresholds.py` always
  intended as "a second, independent stage check"

## 9 · Then decide: should surveyed stage re-enter the posture?

A design decision, not part of this run. With real sections you could classify
each reach on `max(return-period posture, surveyed-stage posture)` — belt and
braces, and it can only escalate. That is the same rule already used for the
FIMAN gage at Speedwell. Worth doing, worth doing deliberately.

## 10 · Commit

```
git add basins.py scripts\xs_out\summary.csv
git commit -m "basins: surveyed thresholds for the six uncovered reaches (NC QL2 via 3DEP)"
git push
```

Commit `summary.csv` — it is the evidence behind the numbers, and the next
person to touch those thresholds will want it.

---

## Caveats to carry forward

Bare-earth LiDAR is a **fixed flight date**: it does not show post-Helene
channel change. Re-cut after major events.

It **cannot see below the water surface**. Top-of-bank and floodplain come out
well; the thalweg is only as good as the flow depth on flight day. For
thresholds — which are about going *out of bank* — that is the right trade.

Sections are cut **perpendicular to the NHD flowline**, which is a
cartographic centerline, not a surveyed thalweg. On tight meanders a cut can
cross the channel obliquely and over-read the width. The scouting run in step 3
is where you catch that.
