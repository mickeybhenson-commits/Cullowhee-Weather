"""
flood_ensemble.py - §3 companion: input-uncertainty ensemble.

The PI band in flood_rating.pi_band() carries the *regression* uncertainty on a
given flow. This module carries the *input* uncertainty: it perturbs the forecast
rainfall (QPF +-25%) and the antecedent wetness (+-0.15) on a deterministic grid,
re-runs the full rainfall->runoff->peak chain (cwm_model) for each combination,
classifies each with the authoritative engine (flood_rating.assess), and reports
the POSTURE DISTRIBUTION so an operator can see whether a call is firm or marginal.

Deterministic 3x3 grid (low/central/high on each axis) - no RNG, fully reproducible.

  from flood_ensemble import ensemble
  ensemble("CC-WCU-2260", qpf=10, wetness=0.25)
      # -> {"posture_dist": {...}, "modal": "...", "firm": bool, "members": [...]}
"""

import cwm_model as cwm
from flood_rating import assess

_ORDER = ["NORMAL", "WATCH", "WARNING", "EMERGENCY", "N/A"]


def ensemble(bid, qpf, wetness, qpf_unc=0.25, w_unc=0.15):
    """Posture distribution over a 3x3 QPF x wetness grid.
      posture_dist  {category: fraction} summing to 1.0, richest-first
      modal         most frequent category
      firm          True if every member agrees
      members       list of (qpf, wetness, posture) tuples
    """
    qs = [qpf * (1 - qpf_unc), qpf, qpf * (1 + qpf_unc)]
    ws = [max(0.0, wetness - w_unc), wetness, min(1.0, wetness + w_unc)]
    members, counts = [], {}
    for q in qs:
        for w in ws:
            qp_raw = cwm.assess(bid, q, w)["qp_raw"]   # raw TR-55 peak (uncalibrated)
            post = assess(qp_raw, bid)["posture"]       # authoritative engine calibrates + classifies
            members.append((round(q, 2), round(w, 3), post))
            counts[post] = counts.get(post, 0) + 1
    tot = sum(counts.values())
    dist = {k: round(v / tot, 3) for k, v in
            sorted(counts.items(), key=lambda kv: (-kv[1], _ORDER.index(kv[0])))}
    modal = next(iter(dist))
    return {"basin": bid, "qpf": qpf, "wetness": wetness,
            "posture_dist": dist, "modal": modal,
            "firm": len(counts) == 1, "members": members}


def ensemble_members(bid, member_qpfs, wetness, w_unc=0.15):
    """REAL-ensemble successor to ensemble(): the QPF axis carries actual
    forecast members (WeatherNext 2 via weathernext_source, bias-corrected
    by the caller) instead of the synthetic +-25% grid. The wetness axis
    keeps its own low/central/high perturbation — soil state uncertainty is
    OURS (sensor sparsity, drydown model), not the weather model's, so the
    two spreads compose rather than substitute.

    members x 3 wetness cells, each run through the same authoritative
    cwm_model -> flood_rating chain as ensemble(). Adds `p_exceed`:
    fraction of cells at or above each posture level — the number live.html
    shows next to the Outlook pill. Falls back to ensemble() upstream when
    no members are available (caller's job; this function requires them).
    """
    if not member_qpfs:
        raise ValueError("member_qpfs is empty — use ensemble() fallback")
    ws = [max(0.0, wetness - w_unc), wetness, min(1.0, wetness + w_unc)]
    members, counts = [], {}
    for q in member_qpfs:
        for w in ws:
            qp_raw = cwm.assess(bid, q, w)["qp_raw"]
            post = assess(qp_raw, bid)["posture"]
            members.append((round(q, 2), round(w, 3), post))
            counts[post] = counts.get(post, 0) + 1
    tot = sum(counts.values())
    dist = {k: round(v / tot, 3) for k, v in
            sorted(counts.items(), key=lambda kv: (-kv[1], _ORDER.index(kv[0])))}
    rank = {k: i for i, k in enumerate(_ORDER)}     # N/A ranks past EMERGENCY;
    p_exceed = {}                                   # exclude it from exceedance
    for lvl in ("WATCH", "WARNING", "EMERGENCY"):
        n = sum(v for k, v in counts.items()
                if k != "N/A" and rank[k] >= rank[lvl])
        p_exceed[lvl] = round(n / tot, 3)
    modal = next(iter(dist))
    return {"basin": bid, "n_members": len(member_qpfs), "wetness": wetness,
            "posture_dist": dist, "modal": modal, "p_exceed": p_exceed,
            "firm": len(counts) == 1, "members": members}


def ensemble_report(bid, qpf, wetness, **kw):
    e = ensemble(bid, qpf, wetness, **kw)
    dist = "  ".join(f"{k} {v:.0%}" for k, v in e["posture_dist"].items())
    print(f"{bid:14s} QPF={qpf} wet={wetness}  modal={e['modal']:<10} "
          f"{'FIRM' if e['firm'] else 'marginal'}   [{dist}]")
    return e


if __name__ == "__main__":
    from basins import routed_order
    print("=" * 92)
    print("§3 INPUT-UNCERTAINTY ENSEMBLE - Helene forcing (QPF=10 in, wetness=0.25)")
    print("  QPF +-25%, wetness +-0.15, 3x3 grid; posture distribution per reach")
    print("=" * 92)
    for bid in routed_order():
        ensemble_report(bid, 10, 0.25)
    print("-" * 92)
    print("\nREAL-MEMBER PATH (ensemble_members, synthetic 8-member spread around 10\"):")
    mem = [7.5, 8.6, 9.3, 9.8, 10.2, 10.9, 11.8, 13.0]
    e = ensemble_members("CC-WCU-2260", mem, 0.25)
    assert abs(sum(e["posture_dist"].values()) - 1.0) < 0.01
    assert e["p_exceed"]["WATCH"] >= e["p_exceed"]["WARNING"] >= e["p_exceed"]["EMERGENCY"]
    assert len(e["members"]) == len(mem) * 3
    pe = e["p_exceed"]
    print(f"  CC-WCU-2260  modal={e['modal']}  P(>=WATCH)={pe['WATCH']:.0%} "
          f"P(>=WARNING)={pe['WARNING']:.0%}  P(>=EMERGENCY)={pe['EMERGENCY']:.0%}")
    try:
        ensemble_members("CC-WCU-2260", [], 0.25)
        raise AssertionError("empty members must raise")
    except ValueError:
        pass
    print("  ensemble_members self-checks passed")
    print("A 'FIRM' reach posts the same category across the whole input envelope; a 'marginal'")
    print("one straddles a boundary - operators should read those with the PI band in mind.")
