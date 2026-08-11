# The two errors are not comparable — and the calibration is the bigger one

**Date:** 2026-08-11
**Trigger:** `noah_storm_duration_not_24h_2026-08-10.md` §"What is NOT captured":
two errors in opposite directions, *"their relative size is untested."*
Now tested.

**Method:** independent Python port of the deployed chain (`live.html` JS /
`cwm_model`), with within-event wetness added as a continuous knob `beta`.
`beta = 0` reproduces the deployed static path to 1.8e-15. Every published anchor
is reproduced before any new claim is made — see *Port validation* at the end.
Code: `engine.py`, `validate.py`, `experiment{,2,3,4}.py`.

---

## Answer to the question that was asked

At the campus reach, Helene forcing, holding everything else fixed:

| error | direction | size |
|---|---|---|
| **SCS Type II 24-h framing** vs real 48-h shape | **over**-predicts | **2.37×** |
| **frozen within-event wetness** (static → dynamic) | **under**-predicts | **1.17×** |

**The shape error is 8.2× the size of the wetness error at the campus, and 19.7×
at Cox Branch** (shape 4.86×, wetness 1.20×). They are in opposite directions as
suspected, but they are not remotely the same magnitude — the question of whether
they "cancel" does not really arise.

The flashier the basin, the worse the shape error: Cox Branch (Tc 29 min) at
**4.86×** against the campus (Tc 127 min) at 2.37×. **The design-storm assumption
does the most damage exactly where lead time is shortest.** The `SCEN` what-if
deck in `live.html` is still on that path.

The shape half is already fixed on the live path (`assessBasinEvent`,
2026-08-03). What remains live is the 1.17× — and it turns out that one mostly
is not real either.

---

## The within-event wetness term is absorbed by the calibration

Turning `beta` on naively raises the campus Helene peak 2,274 → 2,653 cfs (RP 9.3
→ 15.3) and flips posture on **11.1% of a (total × wetness) grid**, always toward
more severe. That looks like a safety win.

It is not earned. `calib` (`a·Q^b`) was **fitted with the static physics**, and
the fit was recovered here exactly: solving for the wetness behind
`basins.py`'s `calib_anchors` gives **w = 0.544–0.556 on all eight basins, both
anchors agreeing to three decimals, at a CN equal to CN2** — i.e. ARC-II, the
standard design convention. Changing the runoff physics under a calibration
fitted to the old physics is the same defect as the open Tc disagreement.

Refit `calib` properly — same design storms, same design wetness, dynamic
physics — and the change nearly vanishes:

| configuration | campus Q | RP | stage |
|---|---|---|---|
| static physics, original calib, w = 0.271 | 2 273 | 9.3 | 8.33 |
| dynamic physics, **original** calib *(naive)* | 2 652 | 15.3 | 8.78 |
| dynamic physics, **refitted** calib | **2 267** | **9.2** | **8.32** |
| surveyed truth | 2 274 | ~9 | ~8.4 |

Operationally the properly-refitted change is small **and mixed in sign** —
campus rain-to-trip, real Helene shape:

| antecedent | | WATCH | WARNING | EMERGENCY |
|---|---|---|---|---|
| w = 0.30 | static + original | 6.46″ | 12.19″ | 22.83″ |
| | dynamic + refitted | 6.31″ | 12.66″ | 25.35″ |
| w = 0.80 | static + original | 4.76″ | 10.30″ | 20.37″ |
| | dynamic + refitted | **5.03″** | **11.86″** | **23.90″** |

**On wet antecedent the refitted dynamic model warns *later*, not earlier.** Under
the no-lost-lives standard that is the wrong direction, and it is the opposite of
what the naive version suggests.

**Recommendation: do not ship within-event wetness accounting as a standalone
change.** It is not free, it is not clearly safe, and its apparent benefit is an
artifact of leaving the calibration stale. Sensitivity: the effect survives a
generous 0.05 in/hr drainage term (1.17× → 1.10×), so this is not a knife-edge
result — it is genuinely small once the calibration is honest.

*Correction on record:* the first refit here was done at saturated design wetness
rather than the ARC-II wetness the original used, and produced an alarming
(and wrong) result — b > 1, Helene under-predicted 46%, rain-to-trip nearly
doubled. Caught and corrected before any conclusion was drawn. The lesson is the
finding itself: **this calibration is very sensitive to the basis it is fitted
on.** Which leads to the real problem.

---

## The real finding: `calib` is extrapolating 2.5–4.6× outside its fitted range

`calib` is a **two-point power law with b < 1**, fitted on SCS Type II design-storm
peaks at the 10-yr (4.8″) and 100-yr (7.5″) depths. Since 2026-08-03 the live path
feeds it **real-hyetograph peaks**, which for the same rainfall depth are far
smaller:

| basin | b | Type II 10-yr peak | real-shape peak, same depth | ratio |
|---|---|---|---|---|
| Cox Branch | 0.940 | 446 | 97 | **4.59×** |
| Long Branch | 0.921 | 734 | 171 | **4.30×** |
| Upper Cullowhee | 0.815 | 1 984 | 498 | 3.98× |
| Tilley Creek | 0.784 | 2 171 | 625 | 3.47× |
| Speedwell | 0.739 | 5 635 | 1 623 | 3.47× |
| Mainstem abv SPD | 0.760 | 3 368 | 977 | 3.45× |
| WCU campus | 0.744 | 4 985 | 1 862 | 2.68× |
| Mouth | 0.742 | 4 724 | 1 890 | 2.50× |

Every operational call the system makes is a **nonlinear rescaling applied 2.5–4.6×
below anything it was fitted against**, and the worst offenders are the two
flashiest, most lead-limited reaches.

The visible consequence: **a 10-year rainfall, delivered in a realistic 48-hour
pattern, maps to a 1.4–3.0 year flow.**

| basin | implied RP of a 10-yr rainfall | | basin | implied RP |
|---|---|---|---|---|
| Cox Branch | **1.4 yr** | | Speedwell | 1.9 yr |
| Long Branch | 1.5 yr | | Mainstem | 2.0 yr |
| Upper Cullowhee | 1.7 yr | | WCU campus | 2.7 yr |
| Tilley Creek | 1.9 yr | | Mouth | 3.0 yr |

That is *not automatically wrong* — Helene really was ~200-yr rainfall and a ~9-yr
peak, so rainfall frequency genuinely does not transfer to flow frequency here.
But it means the calibration's design premise (10-yr rain → 10-yr flow) **is not
what the live path is doing**, and nobody chose the behaviour it has instead.

Refitting `calib` on real-shape storms is not the fix either: it drives b > 1 on
six of eight basins and produces Helene peaks of 5 900–8 200 cfs against a truth
of 2 274. **Neither basis is defensible. The two-point power-law form is the
problem**, and it is now the largest unexamined assumption in the chain — larger
than wetness, larger than Tc, larger than the shape error that has already been
fixed.

---

## Second finding: the Helene anchor does not identify (rainfall, wetness)

Which (basin rainfall, antecedent wetness) pairs reproduce the surveyed 2 274 cfs?

| basin total | w (static physics) |
|---|---|
| 7.22″ *(K24A measured)* | **no solution — saturated soil still 3% short (2 206 cfs)** |
| 7.48″ | 1.000 *(saturated — the minimum total that can reach truth at all)* |
| 8.00″ | 0.796 |
| 9.00″ | 0.498 |
| 10.00″ *(repo anchor)* | 0.272 |
| 11.00″ | 0.092 |

It is a **ridge, not a point.** And the project has been recording two ends of the
same ridge as if they were independent confirmations:

- `basins.py` `HELENE_2024`: *"7.0–8.4 in / 36 h (COOP-anchored), **ARC-III** (P5 2.49″)"*
- `noah_permeability_lever_is_wetness_2026-08-10.md`: *10 in, **drought-dry**, w = 0.15–0.30*

**These are the same solution.** (8.0″, w ≈ 0.80) and (10.0″, w ≈ 0.27) both land on
2 274 cfs. They cannot both be described as corroborating evidence.

Two consequences worth facing:

1. **The "Helene was drought-dry" reading depends on a rainfall total that was
   assumed, not measured at the basin.** K24A recorded 7.22″. The 10″ is
   COOP-anchored. At 8″ the same surveyed peak implies **wet** antecedent soil —
   which inverts the headline of the permeability note. The independent evidence
   (P5 = 2.49″ → classic SCS ARC-III) points the *wet* way, and `basins.py`
   already says ARC-III.
2. **At the measured 7.22″ the model cannot reach the surveyed peak at any
   wetness** — saturated soil yields 2 206 cfs against 2 274, and the minimum
   total that reaches truth is 7.48″ at full saturation. The shortfall is only
   3%, so this is a weak signal, not a contradiction. But it does mean the
   measured gauge total and the surveyed peak are only reconcilable at the very
   wet end — which again points away from "drought-dry", and suggests basin
   rainfall exceeded K24A (plausible: orographic, and K24A is a valley airport
   site). Worth stating rather than leaving implicit.

Until basin rainfall is pinned independently — **basin-averaged MRMS/AORC QPE over
the Helene window, which is the same fix already open for the live path** — the
antecedent wetness inferred from Helene is not a measurement. It is one free
parameter absorbing the error in another.

---

## Third finding: duration is not the axis

`noah_storm_duration_not_24h_2026-08-10.md` framed frozen wetness as a
long-event problem — *"for a watershed whose defining events are 40–48 hours
long."* The data does not support the duration framing. Dynamic/static peak ratio,
campus, Helene shape resampled to each duration:

| total | 12 h | 24 h | 48 h | 72 h |
|---|---|---|---|---|
| 2″ | 1.08× | 1.08× | 1.08× | 1.08× |
| **4″** | 1.19× | 1.19× | **1.20×** | 1.20× |
| **6″** | 1.19× | 1.19× | 1.18× | 1.18× |
| 10″ | 1.14× | 1.12× | 1.12× | 1.12× |
| 14″ | 1.10× | 1.08× | 1.08× | 1.09× |

**Essentially flat in duration, and peaked at 4–6″ totals** — i.e. at moderate
storms, the ordinary warning-relevant range, not at the rare long ones. If the
term is ever worth adding, this is where it earns its keep, and the justification
should be written that way.

---

## What to do, in order

1. **Decide what `calib` is supposed to mean on the real-shape path**, and write
   it down. Right now the deployed system inherits a design-storm mapping it
   never chose. This outranks every other model item, including Tc.
2. **Pin Helene's basin rainfall independently** (basin-averaged MRMS/AORC over
   the event). Until then the antecedent wetness is not identified, and neither
   is the runoff ratio quoted from it. Same fix as the open lockstep item.
3. **Stop citing the two Helene descriptions as mutual corroboration.** Pick one,
   or record the ridge honestly.
4. **Label `SCEN` as design storms on the surface** — the deck over-states by
   2.4× at the campus and **4.9× at Cox Branch**, the shortest-lead reach.
5. **Within-event wetness: not now.** Revisit only after (1), and pre-register the
   prediction — it is a ~1.1–1.2× term whose sign flips depending on whether the
   calibration is refit.

None of this needs hardware. All of it precedes sensor deployment, because every
one of these numbers is what the sensors will be validated against.

---

## Port validation (run before any claim above)

| check | result |
|---|---|
| `beta = 0` ≡ deployed static path | max abs diff **1.8e-15** |
| `cn_from_wetness(CN2, 1.0)` vs published ARC-III worked example, 6 basins | all within 0.06 CN |
| campus Type II 3.0″ w = 1.0 → raw peak | **4 784** (published 4 784) |
| campus Type II 3.0″ w = 1.0 → calibrated | **2 308** (published 2 308) |
| campus Helene real 10″ w = 1.00 | **2 894** (published 2 894) |
| campus Helene real 10″ w = 0.15 | 2 109 (published 2 130, −1%) |
| runoff ratio at the Helene anchor | **0.41** (published 0.41) |
| calibration design wetness recovered | w = 0.544–0.556, CN ≡ CN2, 8/8 basins |

**Caveats.** Single-event analysis — Helene is the only surveyed anchor, and it is
one point on one storm; the 2.37×/4.86× shape ratios are specific to Helene's
pattern. `beta` is a new parameter with no in-basin evidence behind it; its only
defence is that it is dimensionally honest (an inch of infiltration consumes an
inch of retention storage) and that `beta = 0` recovers the deployed engine
exactly. The surveyed truth itself (2 274 cfs) is derived from marks upstream of
the campus section via the FRIS profile, not a direct campus survey.
