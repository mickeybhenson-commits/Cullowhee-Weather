# NOAH — within-event wetness vs storm-shape error experiment (2026-08-11)

Answers the open question in `noah_storm_duration_not_24h_2026-08-10.md`:
how big is the frozen-within-event-wetness error relative to the SCS Type II
shape error, and do they cancel?

Findings: `noah_calib_fitted_offshape_2026-08-11.md`

## Run

    python3 validate.py         # port validation — run this first
    python3 experiment.py       # the 2x2, campus + Cox Branch
    python3 experiment2.py      # identifiability, drainage, duration sweep
    python3 experiment3.py      # calibration interaction (the real finding)
    python3 experiment4.py      # calibration basis + final anchor check
    python3 verify_headline.py  # fresh re-derivation of every headline number

No dependencies beyond the standard library.

## What's here

- `engine.py` — independent port of the deployed chain (live.html JS / cwm_model).
  `beta=0` reproduces the deployed static path to 1.8e-15; `beta=1` means every
  inch that infiltrates consumes an inch of retention storage.
- `data/k24a_helene_hourly.csv` — copied verbatim from the project.

Engine params (DA/Tc/CN2) are test_model.py's, i.e. what `calib` was fitted
against. calib / reg_q / tva_wse / bed_ft / section are basins.py's.
