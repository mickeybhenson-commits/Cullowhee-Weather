# CHANGELOG — 2026-07 improvement set

Approved by Dr. M. B. Henson, 2026-07-15. All numbers traceable to basins.py or
the WCU Helene study.

## Added
- lead_time.py — §4 per-basin lead time + lead-limited flags (120-min requirement).
- flood_ensemble.py — §3 input-uncertainty ensemble (QPF ±25%, wetness ±0.15).
- backtest_helene.py — §1/§2 Helene validation harness (exits 0 only when validated).
- test_improvements.py — 18 unit tests across §2/§3/§4 + back-test.
- README_improvements.md — this bundle's guide.

## Changed
- flood_rating.py
  - §2: non-campus reaches now post by discharge return-period (WATCH ≥2-yr,
    WARNING ≥10-yr, EMERGENCY ≥100-yr); Cox/Long Branch WATCH at 1.5-yr.
  - Campus (CC-WCU-2260) unchanged: validated TVA 7/9/11 ft stage.
  - §3: pi_band() adds a USGS 90% prediction-interval confidence band.
  - Legacy stage posture kept as posture_stage() (campus-authoritative, else x-check).
  - assess() return dict extended: posture, basis, rp_best, rp_band, confidence,
    depth_ft, stage_posture, thr_validated. Backward-compatible on `posture`.
- basins.py
  - Added tc_min / tc_src per basin and LEAD_REQ_MIN (for §4). No flow/rating values
    changed.

## Superseded
- cwm_classify.py — folded into flood_rating.py (which reads basins.py reg_pi
  directly). Safe to delete from the repo.

## Validation
- Helene (QPF=10 in, wetness=0.25): runoff 40%, campus EMERGENCY @ 11.2 ft,
  154–190-yr flow; four under-warned reaches (UP-503, TIL-705, MS-1100, SPD-1830)
  corrected WARNING → EMERGENCY. 18/18 unit tests pass.
