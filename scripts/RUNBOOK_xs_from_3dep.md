# Runbook — cutting surveyed thresholds for the six uncovered reaches

Closes open safety item #1 in `claude/NOAH_SCOPE_no_lost_lives.md`: six of the
seven in-scope reaches, including **every tributary**, have no surveyed channel
geometry and are running on `bankfull x (1.0, 1.5, 2.0)` arithmetic thresholds.

**Status 2026-08-03 (commit `f4026bb`): five of the six are closed** —
Speedwell, Tilley Creek, Mtn. Lower, Cox Branch and Long Branch now carry
LiDAR-cut thresholds in `basins.py`. **`CC-UP-503` (Upper Cullowhee) is still
open**, withheld because its ladder came out non-monotone; see step 6.

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
python xs_from_3dep.py --probe-nldi
```

It makes ONE network fetch from the outlet and splits it by sub-basin polygon:

```
  NLDI https://api.water.usgs.gov/nldi/linked-data  comid 19730122
  61 flowlines, 2140 vertices total

  basin            runs  vertices  channel mi  sections @300ft
  CC-MOUTH-2340       1        44        1.62               28
  CC-WCU-2260         3       210        4.10               72
  ...
```

**Read the `channel mi` and `sections @300ft` columns** — that is what sets how
long step 6 takes, at roughly 1 second per section.

Any basin reporting **NO vertices inside this polygon** has no NHD flowline of
its own at this scale; give it a lower `--spacing` or supply a hand-drawn
`--centerline` CSV.

### Why one fetch and not eight

A pour point is a **confluence**, so NLDI's point-in-catchment lookup there
lands on the **mainstem**, not on the tributary. Measured 2026-08-03: Cox
Branch and Long Branch both resolved to comid 19730148 — the mainstem — so both
would have been cut from the same channel and neither from its own branch. And
CC-SPD-1830 returned *less* upstream network than CC-UP-503, which it contains,
which is impossible for a real basin.

So the script now pulls the network once from the outlet and assigns each
vertex to the **smallest containing sub-basin polygon** — the polygons are
cumulative, so smallest-containing is exactly "this reach's own drainage."
Same rule that fixed the always-Mouth click bug in `flash.html`.

The host list is tried in order: `api.water.usgs.gov` first, then the two
legacy `labs.waterdata.usgs.gov` hosts, which now 404.

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

## 4b · Validation run — the one reach with surveyed truth

Do this **before** the production run, every time the selection logic changes.
CC-WCU-2260 is the only reach where FRIS-RAS gives real geometry to check
against, so it is the method's only calibration:

```
python xs_from_3dep.py --basin CC-WCU-2260 --out-dir xs_check
```

Expect `thr_ft=(2.2, 4.63, 9.12)` — top-of-bank 4.63 against FRIS 4.1 (+13%),
100-yr depth 9.12 against 9.5 (−4%). If this moves, nothing downstream is
trustworthy and the six unsurveyed reaches are not worth cutting.

## 5 · Production run — all six reaches

```
python xs_from_3dep.py --out-dir xs_out
```

With no `--basin` it targets all six uncovered reaches: Speedwell, Tilley
Creek, Mtn. Lower, Upper Cullowhee, Cox Branch, Long Branch.

Do **not** pass a global `--width` here. Each reach has its own default sized
to its channel (Cox Branch 140 ft, the campus mainstem 260 ft); one flag
overrides all of them and drowns the small branches in floodplain.

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

### How a reach picks its numbers

NLDI hands back several **runs** per polygon — the channel through the
incremental area plus the tributaries joining it. The script does **not** pick
one run. It ranks every cut section by thalweg elevation and pools the lowest
ones **across runs**:

- lowest third of the reach, floor 5 sections, cap 15 (~0.85 mi at 300 ft)
- widened only if bank detection starved the base window
- WATCH / WARNING / EMERGENCY are the **median** over that pool

The reason is dimensional. `thr_ft` describes stage at the **pour point**, and
`REG_Q100` is the pour-point discharge, so the geometry it is routed through
has to be near the pour point too — a section 1.5 mi upstream drains a fraction
of the area and over-deepens the answer. Sub-basin polygons are drainage
divides, so the lowest thalweg *is* the pour point.

The output prints the pool for each reach:

```
    run 0: 10 sections (9 passing), thalweg 2083.7-2095.2 ft
    run 1: 19 sections (17 passing), thalweg 2106.1-2134.2 ft
    pour-point pool: 9 of 29 sections, thalweg 2083.7-2095.2 ft, run(s) 0
```

That 10.9 ft step between run 0's top and run 1's bottom is the tributary
junction — which is why pooling by elevation separates them without needing to
know anything about the network topology.

### Two failure verdicts, and they mean different things

**`DO NOT PASTE — out of order`** (EMERGENCY ≤ WARNING). Hard reject. The
ladder would escalate backwards, which is worse than the placeholder it would
replace. The reach keeps its existing `thr_ft`.

**You do not have to plot CSVs to work out why.** The script prints a
`DIAGNOSIS:` line per reach. The two failures want *opposite* fixes, so a wrong
guess makes the reach worse — that is the whole reason it does the reading:

| DIAGNOSIS | what it saw | remedy |
|---|---|---|
| **width** | sections ran to a hillside, thalweg near the centre of the cut | re-cut narrower (command is printed) |
| **centerline** | thalweg persistently >45% of the half-width off centre — the NHD flowline is not on the water | hand-drawn `--centerline`. Narrowing makes it **worse**: it crops away the channel you already missed |
| **no channel resolved** | flat at the low point, thalweg centred | smaller `--spacing` and more `--npts` before touching width |
| **mixed** | no single failure dominates | re-cut narrower first (cheap), then read the line again |

`summary.csv` carries `why` and `thalweg_offset_frac` per section if you want to
check the call yourself.

**`CAUTION — tight ladder`** (WARNING < 0.5 ft above WATCH). Not a reject. On a
small steep incised branch there is no bankfull bench distinct from
top-of-bank — Cox Branch reads bankfull == top-of-bank on several sections — so
a ~1 ft span from bankfull to the 100-yr is the channel telling the truth. It
is emitted with the caution attached; expect WATCH and WARNING to fire close
together there.

### Sanity check before you accept anything

Compare each new **top-of-bank** against the Bieger regional bankfull already
in `basins.py`. LiDAR should read a little *deeper*, since it finds
top-of-bank rather than bankfull. The 2026-08-03 cut:

| reach | LiDAR topbank | Bieger bankfull |
|---|---|---|
| CC-SPD-1830 | 2.75 | 2.71 |
| CC-TIL-705 | 2.41 | 2.02 |
| CC-MS-1100 | 2.79 | 2.32 |
| CC-COX-097 | 2.00 | 1.11 |
| CC-LB-171 | 2.73 | 1.31 |

If a reach comes out *below* its regional bankfull, something is wrong.

## 7 · Update `basins.py`

**Done for five reaches as of commit `f4026bb`** — Speedwell, Tilley, Mtn.
Lower, Cox Branch and Long Branch now carry `SURVEYED:` provenance. Re-running
should reproduce them; if it doesn't, that is a finding, so say so rather than
overwriting.

Still to do: **`CC-UP-503`**, withheld as out-of-order. See step 6.

Keep **`CC-WCU-2260` untouched** — its 7 / 9 / 11 ft is field-validated (11 ft =
water in the road) and no LiDAR cut beats a real observation. It is cut anyway,
as the method's only check against surveyed truth: the run must reproduce
`(2.20, 4.63, 9.12)`, against FRIS-RAS top-of-bank 4.1 ft and d100 9.5 ft.

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
