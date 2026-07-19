"""
lead_time.py - §4 per-basin lead time (time-to-peak) and lead-limited flags.

Time of concentration (Tc) ~= time to peak. A reach whose Tc is below the
operational forecast-lead requirement (basins.LEAD_REQ_MIN, 120 min) cannot make
actionable lead on observation alone: by the time a gauge shows the rise, the
peak is already arriving. Those reaches are LEAD-LIMITED and must be warned
forecast-driven (QPF-based), not just observation-driven.

Source of Tc: basins.py tc_min (Kirpich / buildsheet; NRCS-wet where noted in
tc_src). No fabricated numbers - this module only reads and flags.

  from lead_time import lead_flags, lead_report
  lead_flags("CC-COX-097")   # -> dict
  lead_report()              # -> printed table
"""

from basins import BASINS, routed_order, LEAD_REQ_MIN


def lead_flags(bid):
    """Lead-time posture for one basin.
      tc_min        time of concentration ~ time to peak (minutes)
      lead_req_min  operational actionable-lead requirement (minutes)
      margin_min    tc_min - lead_req_min (negative => lead-limited)
      lead_limited  True if the reach cannot make lead on observation alone
      registry_lead the basins.py `lead` tag ("limited"/"adequate") for cross-check
    """
    rec = BASINS[bid]
    tc = rec.get("tc_min")
    limited = (tc is not None) and (tc < LEAD_REQ_MIN)
    return {
        "basin": bid,
        "tc_min": tc,
        "tc_src": rec.get("tc_src", ""),
        "lead_req_min": LEAD_REQ_MIN,
        "margin_min": (tc - LEAD_REQ_MIN) if tc is not None else None,
        "lead_limited": limited,
        "registry_lead": rec.get("lead"),
    }


def lead_limited_basins():
    """List of reaches that cannot make actionable lead on observation alone."""
    return [b for b in routed_order() if lead_flags(b)["lead_limited"]]


def lead_report():
    print("=" * 84)
    print(f"§4 LEAD-TIME REPORT - operational actionable-lead requirement = {LEAD_REQ_MIN} min")
    print("  Tc < requirement => LEAD-LIMITED => needs forecast-driven (not obs-only) warning")
    print("=" * 84)
    hdr = f"{'basin':14s}{'Tc min':>8}{'margin':>8}  {'flag':<14}{'registry':<10}note"
    print(hdr); print("-" * (len(hdr) + 20))
    for bid in routed_order():
        f = lead_flags(bid)
        tc = f"{f['tc_min']}" if f["tc_min"] is not None else "--"
        mg = f"{f['margin_min']:+d}" if f["margin_min"] is not None else "--"
        flag = "LEAD-LIMITED" if f["lead_limited"] else "lead-adequate"
        # cross-check the derived flag against the registry tag
        reg = f["registry_lead"] or ""
        mismatch = ""
        if reg in ("limited", "adequate"):
            derived = "limited" if f["lead_limited"] else "adequate"
            if derived != reg:
                mismatch = "  <-- MISMATCH vs registry, verify"
        note = (f["tc_src"][:34] + mismatch)
        print(f"{bid:14s}{tc:>8}{mg:>8}  {flag:<14}{reg:<10}{note}")
    print("-" * (len(hdr) + 20))
    limited = lead_limited_basins()
    print(f"Lead-limited: {len(limited)}/{len(routed_order())} reaches -> "
          f"{', '.join(limited)}")
    print("Only the campus (Tc 127) and mouth (147) clear the 120-min bar; every tributary")
    print("is lead-limited, which is the quantitative case for forecast-driven warning.")


if __name__ == "__main__":
    lead_report()
