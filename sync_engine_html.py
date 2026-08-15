#!/usr/bin/env python3
"""
sync_engine_html.py — make basins.py the single source for the numbers the
browser engines run on.

    python sync_engine_html.py            # report drift, change nothing (exit 1 if any)
    python sync_engine_html.py --write    # fix it

WHY THIS EXISTS
---------------
The flood engine exists in Python (cwm_model.py) and again in JavaScript, embedded
in live.html and rain_to_trip.html so the public map computes postures in the
visitor's browser with nothing running server-side. That is a good property and it
is not what this fixes. What it fixes is that the same NUMBERS live in three places.

Every divergence found on 2026-08-11 and 2026-08-13 was a number, never a logic
difference, and every one was in HTML while both Python twins were correct:

  2026-08-03  surveyed LiDAR ladders reached basins.py; live.html kept the
              bankfull x(1.0,1.5,2.0) placeholders for eight days, and
              rain_to_trip.html for ten — Speedwell's WATCH sitting 1.24 ft above
              the surveyed one.
  2026-08-12  CC-UP-503's drainage area corrected to 5.03 in two places of three;
              live.html still read DA:5.35 with its calibration fitted to it.
  2026-07-15  the 1.5-yr WATCH basin set reached flood_rating.py and no browser
              copy could express it at all for four weeks.

The consistency tests now DETECT all of that, and discover engine-bearing HTML
rather than naming live.html by hand. This is the other half: a red test says the
numbers disagree, and this makes them agree.

WHAT IT SYNCS, AND WHAT IT DELIBERATELY DOES NOT
------------------------------------------------
Synced, because basins.py owns them and the tests already assert them:

    thr    <- basins.BASINS[bid]["thr_ft"]      threshold ladder
    DA     <- basins.BASINS[bid]["da_sqmi"]     drainage area
    calib  <- basins.BASINS[bid]["calib"]       (a, b) of the calibration power law
    WATCH_1_5YR <- flood_rating.WATCH_1_5YR     the early-WATCH basin set

NOT synced, on purpose:

    Tc     cwm_model and basins DISAGREE for CC-MS-1100 and CC-SPD-1830, and that
           divergence is DECLARED in test_registry_engine_consistency because the
           calibration was fitted against the engine's values. Rewriting Tc from
           the registry would silently de-anchor calib on two reaches.
    CN2, qb, sec
           these live in cwm_model.py, not the registry. basins.py is not their
           source of truth, so this tool has no business writing them.

TWO DESIGN RULES, BOTH LEARNED THE HARD WAY WHILE WRITING THIS
--------------------------------------------------------------
1. COMPARE BY VALUE, NEVER BY TEXT. The first version treated `calib:[2.777,0.760]`
   as drift because it would have rendered `0.76`, and offered to rewrite the
   deployed map to delete a trailing zero. Cosmetic churn in a 713 KB public file is
   pure risk for no gain. Tolerances here MIRROR the consistency tests exactly — DA
   to 0.05 sq mi, calib to 4 dp, thr to 3 dp — so the tool and the test can never
   disagree about what counts as drift.

2. BRACE-MATCH, NEVER `[^}]*`. The first version could not find `thr` on six of
   eight basins, because those entries carry a nested `sec:{...}` before it and the
   character class stopped at the first `}`. It reported them as unlocatable rather
   than mangling them, which is the only reason this is a footnote and not an
   incident. Basin entries are now sliced by counting braces.

SAFETY
------
Byte-level. Every replacement asserts exactly one match. A field that cannot be
located is reported and the file left alone rather than guessed at. Line endings are
counted before and after and a change aborts the write. Nothing is written without
--write, and a file needing no change is not rewritten at all.

Stdlib only.
"""

import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import basins                                            # noqa: E402
import flood_rating                                      # noqa: E402

# Tolerances mirror test_registry_engine_consistency.py. If they ever drift apart,
# the tool would "fix" something the test accepts, or leave something it rejects.
TOL_DA = 0.05
DP_CALIB = 4
DP_THR = 3


def engine_html():
    """Every page at the repo root carrying its own BASINS literal — discovered,
    not named. Naming live.html is how rain_to_trip.html went unchecked."""
    out = []
    for fn in sorted(os.listdir(HERE)):
        if not fn.endswith(".html"):
            continue
        try:
            with open(os.path.join(HERE, fn), "rb") as f:
                raw = f.read()
        except OSError:
            continue
        if re.search(rb'"CC-[A-Z]+-\d+"\s*:\s*\{[^}]*\bDA\s*:', raw):
            out.append((fn, raw))
    return out


def basin_slice(raw, bid):
    """(start, end) of this basin's `{...}` entry, by counting braces.

    Not a regex: these entries contain a nested `sec:{...}`, and `[^}]*` stops at
    the first closing brace — which silently hid six threshold ladders from the
    first version of this tool.
    """
    m = re.search(rb'"' + re.escape(bid).encode() + rb'"\s*:\s*\{', raw)
    if not m:
        return None
    i = m.end() - 1
    depth = 0
    for j in range(i, len(raw)):
        c = raw[j:j + 1]
        if c == b"{":
            depth += 1
        elif c == b"}":
            depth -= 1
            if depth == 0:
                return (i, j + 1)
    return None


def find_field(raw, span, name):
    """(whole_match, prefix, value_text) for `name:` inside a basin slice."""
    lo, hi = span
    m = re.search(name.encode() + rb"\s*:\s*(\[[^\]]*\]|[0-9.]+)", raw[lo:hi])
    if not m:
        return None
    return (m.group(0), m.group(0)[:m.start(1) - m.start(0)], m.group(1),
            lo + m.start(), lo + m.end())


def nums(text):
    return [float(x) for x in re.findall(rb"[0-9.]+", text)]


def fmt(vals, bracket):
    body = ",".join(f"{v:g}" for v in vals)
    return (b"[" + body.encode() + b"]") if bracket else body.encode()


def edits_for(raw):
    """[(label, lo, hi, new_bytes)] this file needs. Empty = already correct."""
    todo, missing = [], []

    def field(bid, span, name, want, tol=None, dp=None, bracket=False):
        f = find_field(raw, span, name)
        if f is None:
            missing.append(f"{bid}: {name}")
            return
        _, prefix, val, lo, hi = f
        got = nums(val)
        if len(got) != len(want):
            todo.append((f"{bid} {name}", lo, hi, prefix + fmt(want, bracket)))
            return
        if tol is not None:
            same = all(abs(g - w) <= tol for g, w in zip(got, want))
        else:
            same = all(round(g, dp) == round(w, dp) for g, w in zip(got, want))
        if not same:
            todo.append((f"{bid} {name}", lo, hi, prefix + fmt(want, bracket)))

    for bid, reg in basins.BASINS.items():
        span = basin_slice(raw, bid)
        if span is None:
            missing.append(f"{bid}: entry not found")
            continue
        if reg.get("thr_ft") is not None:
            field(bid, span, "thr", list(reg["thr_ft"]), dp=DP_THR, bracket=True)
        field(bid, span, "DA", [reg["da_sqmi"]], tol=TOL_DA)
        if reg.get("calib"):
            field(bid, span, "calib", list(reg["calib"]), dp=DP_CALIB, bracket=True)

    want = sorted(flood_rating.WATCH_1_5YR)
    m = re.search(rb"(WATCH_1_5YR\s*=\s*)\[([^\]]*)\]", raw)
    if not m:
        missing.append("WATCH_1_5YR array")
    else:
        got = sorted(x.decode() for x in re.findall(rb'"([^"]+)"', m.group(2)))
        if got != want:
            new = m.group(1) + b"[" + b",".join(
                b'"' + b.encode() + b'"' for b in want) + b"]"
            todo.append(("WATCH_1_5YR", m.start(), m.end(), new))

    return todo, missing


def apply(raw, todo):
    crlf = raw.count(b"\r\n")
    for label, lo, hi, new in sorted(todo, key=lambda t: -t[1]):   # back to front
        raw = raw[:lo] + new + raw[hi:]
    if raw.count(b"\r\n") != crlf:
        raise SystemExit("refusing to write: line endings changed")
    return raw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true",
                    help="fix the files (default is report-only)")
    a = ap.parse_args()

    pages = engine_html()
    if not pages:
        raise SystemExit("no engine-bearing HTML found — the BASINS-literal pattern "
                         "has stopped matching, so this tool is silently a no-op. "
                         "Fix the pattern; do not ignore this.")

    bad = 0
    for fn, raw in pages:
        todo, missing = edits_for(raw)
        if missing:
            bad += len(missing)
            print(f"{fn}: CANNOT LOCATE {len(missing)} field(s) — file left untouched")
            for m in missing:
                print(f"    {m}")
        if not todo:
            if not missing:
                print(f"{fn}: up to date")
            continue
        bad += len(todo)
        print(f"{fn}: {len(todo)} field(s) differ from basins.py")
        for label, lo, hi, new in todo:
            print(f"    {label:<22} {raw[lo:hi].decode(errors='replace')}"
                  f"  ->  {new.decode(errors='replace')}")
        if a.write and not missing:
            with open(os.path.join(HERE, fn), "wb") as f:
                f.write(apply(raw, todo))
            print("    written.")
        elif a.write:
            print("    NOT written — unlocatable fields above must be fixed by hand first.")

    if not bad:
        print("\nall engine HTML matches basins.py")
        return 0
    if a.write:
        print(f"\n{bad} field(s) handled. Re-run the consistency tests before committing.")
        return 0
    print(f"\n{bad} field(s) out of date. Re-run with --write to fix.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
