"""
runoff_dynamic_fixed.py — mass-consistent replacement for
engine.py's incremental_runoff_dynamic.

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

def incremental_runoff_dynamic(hyeto, cn0, cn_sat, beta=1.0, drain_in_hr=0.0,
                               dt_hr=0.25, sub=20):
    """Within-event wetness accounting, mass-consistent.

    hyeto   : per-interval rainfall, inches
    cn0     : curve number at the storm's initial wetness
    cn_sat  : ARC-III curve number (the saturation floor)
    beta    : inches of retention storage consumed per inch infiltrated
              beta = 0 -> the deployed static engine
    sub     : sub-steps per interval; controls discretisation error only

    Returns (incremental_runoff, S_trace) — same signature as the original.
    """
    from engine import s_from_cn                      # or inline the identity

    S0 = s_from_cn(cn0)
    S_min = s_from_cn(cn_sat)
    S = S0
    Ia = 0.2 * S0
    P = 0.0
    Pe = 0.0
    Q = 0.0
    out, strace = [], []
    for p in hyeto:
        q0 = Q
        for _ in range(sub):
            dp = p / sub
            P += dp
            newPe = max(0.0, P - Ia)
            dPe = newPe - Pe
            Pe = newPe
            if dPe <= 0.0:
                continue
            frac = 1.0 - (S / (Pe + S)) ** 2 if (Pe + S) > 0 else 1.0
            dQ = dPe * frac
            dF = dPe - dQ
            Q += dQ
            S = min(S0, max(S_min, S - beta * dF + drain_in_hr * dt_hr / sub))
        out.append(Q - q0)
        strace.append(S)
    return out, strace

