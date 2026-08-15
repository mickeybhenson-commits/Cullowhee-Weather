#!/usr/bin/env python3
"""
verify_surveyed_thresholds.py — do the deployed threshold ladders still follow
from the LiDAR they claim to come from?

    python verify_surveyed_thresholds.py

WHY THIS EXISTS
---------------
Five reaches in basins.py carry `thr_src = "SURVEYED: NC QL2 LiDAR via USGS 3DEP,
pour-point pool, N of M sections"`. Those three numbers per reach are what turn a
stage into a posture on five of the eight sub-basins, including both 1.5-yr WATCH
reaches. Until 2026-08-15 nothing checked them against the cut they came from.

Everything else in this repo is checked one layer too late. The consistency tests
assert that live.html, cwm_model and basins.py agree about a threshold — but they
agree about whatever number is in basins.py, correct or not. A wrong ladder
propagates cleanly and every test stays green. This is the layer under that: does
the number in the registry follow from the evidence in scripts/xs_out/summary.csv?

The runbook already asked for this, in step 7:

    "should reproduce them; if it doesn't, that is a finding, so say so rather
     than [paper over it]"

WHAT IT RECONCILES
------------------
Per RUNBOOK_xs_from_3dep.md §6, a reach picks its numbers by pooling the lowest
sections by thalweg elevation ACROSS runs — the pour point, because thr_ft is a
stage at the pour point and REG_Q100 is the pour-point discharge — and taking the
median over that pool:

    WATCH     = median bankfull_depth_ft
    WARNING   = median topbank_depth_ft
    EMERGENCY = median d100_above_thalweg_ft

ONE LIMITATION, STATED RATHER THAN PAPERED OVER
------------------------------------------------
The base window is "lowest third, floor 5, cap 15", and the runbook says it is
"widened only if bank detection starved the base window" — without giving the
criterion. That widening is real: CC-COX-097's base window of 9 holds only 7
passing sections, and the shipped ladder needs 8.

So this tool does NOT re-derive the pool SIZE. It reads N from the reach's own
`thr_src` claim, finds the smallest window that yields exactly N passing sections,
and checks the medians there. That makes this a check on the NUMBERS and on the
CUT, not on the selection heuristic:

  * a hand-edited threshold in basins.py            -> caught
  * a re-cut that changed the section geometry      -> caught (M is verified too)
  * a corrupted or truncated summary.csv            -> caught
  * a change to the widening rule alone             -> NOT caught

If the widening criterion is ever written down, replace `_pool_of_size` with it
and this becomes a full reproduction.

Stdlib only. Read-only. Exit 0 = every surveyed ladder reproduces.
"""

import csv
import os
import re
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SUMMARY = os.path.join(HERE, "scripts", "xs_out", "summary.csv")
STALE = os.path.join(HERE, "scripts", "xs_out", "thresholds_lidar.py")

BASE_FLOOR, BASE_CAP = 5, 15
FIELDS = ("bankfull_depth_ft", "topbank_depth_ft", "d100_above_thalweg_ft")
CLAIM = re.compile(r"pour-point pool,\s*(\d+)\s+of\s+(\d+)\s+sections?")


def sections():
    """{basin_id: [row]} from the cut summary."""
    if not os.path.exists(SUMMARY):
        raise SystemExit(
            f"cannot find {os.path.relpath(SUMMARY, HERE)}\n\n"
            "This is the evidence behind five deployed threshold ladders and it is\n"
            "NOT tracked by git — RUNBOOK_xs_from_3dep.md step 7 says to commit it\n"
            "alongside basins.py, and that half did not happen:\n\n"
            "    git add scripts/xs_out/summary.csv\n\n"
            "Until it is committed, the ladders in basins.py have no provenance\n"
            "anywhere but one laptop.")
    out = {}
    with open(SUMMARY, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            out.setdefault(r["basin_id"], []).append(r)
    return out


def _pool_of_size(rows, n_want):
    """Lowest-thalweg sections, widening until exactly n_want of them passed.

    Starts at the documented base window and grows one section at a time, which
    is what the runbook describes qualitatively ("widened only if bank detection
    starved the base window") without giving the criterion. Returns None if no
    window yields the claimed count — itself a finding worth printing.
    """
    ranked = sorted(rows, key=lambda r: float(r["thalweg_elev_ft"]))
    base = max(BASE_FLOOR, min(BASE_CAP, len(ranked) // 3))
    for w in range(base, len(ranked) + 1):
        pool = [r for r in ranked[:w] if r["ok"] == "1"]
        if len(pool) == n_want:
            return pool, w, base
        if len(pool) > n_want:
            return None, w, base
    return None, len(ranked), base


def ladder(pool):
    return tuple(round(st.median(float(r[k]) for r in pool), 2) for k in FIELDS)


def stale_dict():
    """The GENERATED thresholds_lidar.py sitting beside the summary, if present.

    It is read only to be warned about. On 2026-08-15 the copy in scripts/xs_out/
    was the output of an OLDER selection method — per-run rather than pour-point
    pool — and disagreed with every shipped reach while carrying a 'GENERATED'
    banner in the directory the runbook names as the results location.
    """
    if not os.path.exists(STALE):
        return None
    ns = {}
    try:
        exec(compile(open(STALE, encoding="utf-8", errors="replace").read(),
                     STALE, "exec"), ns)
    except Exception:                                     # noqa: BLE001
        return None
    return ns.get("SURVEYED_THR")


def main():
    import basins                                          # noqa: E402

    cut = sections()
    print("=" * 78)
    print("SURVEYED THRESHOLD RECONCILIATION — basins.py against the 3DEP cut")
    print("=" * 78)
    print(f"  evidence: {os.path.relpath(SUMMARY, HERE)}  "
          f"({sum(len(v) for v in cut.values())} sections, {len(cut)} reaches)")
    print()

    bad = 0
    surveyed = 0
    for bid, reg in basins.BASINS.items():
        src = reg.get("thr_src") or ""
        if not src.startswith("SURVEYED"):
            continue
        surveyed += 1
        dep = reg.get("thr_ft")
        m = CLAIM.search(src)
        if not m:
            bad += 1
            print(f"  {bid:<14} thr_src says SURVEYED but states no 'N of M sections' pool")
            continue
        n_want, m_want = int(m.group(1)), int(m.group(2))
        rows = cut.get(bid)
        if not rows:
            bad += 1
            print(f"  {bid:<14} MISSING from the cut summary entirely")
            continue
        if len(rows) != m_want:
            bad += 1
            print(f"  {bid:<14} CUT CHANGED — thr_src claims {m_want} sections, "
                  f"summary.csv holds {len(rows)}")
            continue
        pool, w, base = _pool_of_size(rows, n_want)
        if pool is None:
            bad += 1
            print(f"  {bid:<14} no window yields the claimed pool of {n_want} "
                  f"(base window {base}, gave up at {w})")
            continue
        got = ladder(pool)
        ok = dep is not None and all(abs(a - c) <= 0.005 for a, c in zip(got, dep))
        if not ok:
            bad += 1
        note = f"window {w}" + (f" (widened from {base})" if w != base else "")
        print(f"  {bid:<14} {'ok  ' if ok else 'FAIL'} "
              f"deployed {str(dep):<22} recomputed {str(got):<22} "
              f"pool {n_want} of {m_want}, {note}")

    print()
    st_ = stale_dict()
    if st_:
        diff = []
        for bid, v in st_.items():
            dep = (basins.BASINS.get(bid) or {}).get("thr_ft")
            if dep is None or tuple(round(x, 2) for x in v) != tuple(dep):
                diff.append((bid, tuple(v), dep))
        missing = [b for b, r in basins.BASINS.items()
                   if (r.get("thr_src") or "").startswith("SURVEYED") and b not in st_]
        if diff or missing:
            print("-" * 78)
            print(f"WARNING — {os.path.relpath(STALE, HERE)} disagrees with what shipped.")
            print("It carries a 'GENERATED' banner and sits where the runbook says results")
            print("live, so it reads as authoritative. It is not. Delete or regenerate it.")
            for bid, v, dep in diff:
                print(f"    {bid:<14} that file says {str(v):<22} deployed {dep}")
            for bid in missing:
                print(f"    {bid:<14} absent from that file although it shipped")

    print("=" * 78)
    if not surveyed:
        raise SystemExit("no reach in basins.py claims a SURVEYED source — either the "
                         "registry changed shape or this tool is looking at the wrong "
                         "thing. Not passing vacuously.")
    if bad:
        print(f"{bad} of {surveyed} surveyed ladder(s) DO NOT follow from the cut.")
        print("The runbook's own step 7: that is a finding, so say so rather than")
        print("paper over it. Do not edit basins.py to match — find out which is wrong.")
        return 1
    print(f"all {surveyed} surveyed ladders reproduce from the cut.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
