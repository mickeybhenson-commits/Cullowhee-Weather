"""
runoff_dynamic_fixed.py — the derivation record for engine.py's
incremental_runoff_dynamic.

APPLIED 2026-08-13. This file no longer carries its own implementation: engine.py
now contains this form, and two copies of a numerical method is the failure mode
this repo has spent a month removing. What survives here is WHY, which is worth
keeping and is not worth duplicating.

Re-verified against engine.py on 2026-08-13 before folding it in: the peak is
monotone in beta across [0, 6] (the old form fell from 2,261 cfs at beta 2.00 to
2,174 and plateaued), and beta = 0 matches incremental_runoff_static to 0.17% at
the default sub=20 — the 0.04% quoted below is the sub=100 figure.

THE BUG IN THE CURRENT VERSION
------------------------------
It re-evaluates the CUMULATIVE SCS closed form

    F = S*Pe / (Pe + S)

using the CURRENT S at every step. F increases with S, so when S shrinks the
function retroactively restates infiltration that already happened at a drier
soil state — and with it, runoff that has already been routed through the unit
hydrograph. Three symptoms, all observed:

  * dF goes negative and is silenced by a max(0, .) clamp
  * cumulative runoff jumps when S drops, injecting a spurious increment
  * THE PEAK IS NON-MONOTONE IN BETA — it rises to a false maximum near
    beta ~ 2.0 (2,261 cfs on the Helene case), then falls and plateaus at 2,174

The third one is the dangerous one: bisecting on beta against a non-monotone
response returns values that do not reproduce their target. That happened, and
the (w0, beta) "roots" it produced were published before being caught.

THE FIX
-------
Integrate the SCS derivative instead of re-evaluating its integral:

    dQ/dPe = 1 - (S/(Pe+S))^2
    dF     = dPe - dQ
    S     <- max(S_min, S - beta*dF)

Runoff already banked is never revised. dF cannot be negative, so no clamp is
needed. Mass is conserved by construction: dQ + dF == dPe exactly.

VERIFIED
--------
  * beta = 0 reproduces incremental_runoff_static to 0.04% (discretisation only)
  * the peak is MONOTONE in beta across [0, 6]
  * the plateau that remains is the ARC-III saturation floor, which is correct:
    once the soil is saturated, more beta cannot do more

WHAT IT DOES NOT FIX
--------------------
Within-event wetting still cannot reproduce Helene's surveyed peak from the
measured 7.22 in, and cannot beat the static saturated bound. Initial abstraction
is fixed at Ia = 0.2*S0 at storm onset, so a storm starting on drier soil forfeits
that depth permanently. That is correct SCS behaviour, not another bug — see
noah_helene_ridge_is_frozen_wetness_2026-08-12.md.
"""

# The implementation lives in engine.py. Imported here so that anything already
# depending on this module keeps working, and so there is exactly one copy.
from engine import incremental_runoff_dynamic  # noqa: F401,E402
