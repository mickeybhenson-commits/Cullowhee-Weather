#!/usr/bin/env python3
"""
check_gribble_observed.py — verify the OBSERVED side of the Gribble Gap Mode C table
against the raw flume record, without moving 13.5 MB anywhere.

    python scripts/check_gribble_observed.py

WHY
---
`experiments/gribble_gap_modeC/validate_modeC.py` makes a strong negative claim: that
Mode C cannot be fitted, because the SCS-CN depth predicts ZERO runoff on 44% of events
that measurably produced it, and because k spans 1300x as a function of duration.

Both halves are computed against `runoff_in` and `peak_cfs` from the derived event
inventory. **Nobody has checked those derived columns against the record they came from**
— `wchrs_public/GribbleGap_Q_T_ALL_CLEAN_NOV19.csv`, the flume's own discharge series.
That file has never been read by anything in this repo, and it is the only file in it I
was unable to transfer (the device bridge timed out on it three times).

So this runs where the file already is. Paste the output back.

WHAT IT CHECKS, AND HOW MUCH EACH IS WORTH
------------------------------------------
1. PEAK — max Q(t) inside each event window vs the inventory's `peak_cfs`.
   **This is the load-bearing one.** Mode C fits k = observed peak / modelled peak, so
   `peak_cfs` IS the observed side. It needs no assumptions: a maximum is a maximum.

2. RUNOFF DEPTH — the volume under the hydrograph over the catchment area vs `runoff_in`.
   Weaker, and honestly so: `runoff_in` is presumably baseflow-separated and the
   separation method is not documented anywhere I can find. So this prints BOTH the total
   volume depth and a constant-baseflow-separated one (pre-event Q held flat), and you
   should read a consistent OFFSET between them as "different separation method" — which
   is fine — and a scattered, sign-flipping difference as "something is actually wrong".

3. RESOLUTION — the record's own sampling interval and span. This bears directly on
   validate_modeC's first reason: on a catchment whose Tc is ~10 minutes, whether the
   record resolves the peak at all decides how much `peak_cfs` can be trusted.

Stdlib only. Read-only — writes nothing, changes nothing.
"""

import csv
import datetime as dt
import os
import statistics as st
import sys

# Lives in scripts/ deliberately. test_wired.py governs every .py at the REPO ROOT —
# tracked or not, because it walks the filesystem — so a one-shot diagnostic dropped
# there would fail test_every_module_is_reachable_or_declared before it was ever
# committed. scripts/ is outside PKG_DIRS and is already where the other workstation
# one-shots live (backfill_ledger.py, build_mrms_masks.py).
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
RAW = os.path.join(ROOT, "wchrs_public", "GribbleGap_Q_T_ALL_CLEAN_NOV19.csv")
INV = os.path.join(ROOT, "gribble_gap_event_inventory_2015_2019.csv")
MC = os.path.join(ROOT, "gribble_gap_modeC_input_table.csv")

DA_SQMI = 0.166                      # gribble_gap.json streamstats.DA_adopted_sqmi
SQMI_FT2 = 27_878_400.0
_TIME_COL = _Q_COL = None            # set by --time-col / --q-col


def parse_ts(s):
    s = (s or "").strip().replace("/", "-")
    if not s:
        return None
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
              "%Y-%m-%dT%H:%M", "%m-%d-%Y %H:%M", "%m-%d-%Y %H:%M:%S", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, f)
        except ValueError:
            pass
    try:
        return dt.datetime.fromisoformat(s.replace("Z", ""))
    except ValueError:
        return None


def pick(header, *wants):
    """First column matching any of `wants`, tested BOTH directions.

    The real header is ['', 'TEMPERATURE', 'disch', 'time', 'GGwsd', 'note']. A one-way
    `if want in name` test looking for "discharge" never matches a column called "disch",
    because the wanted token is LONGER than the name. Test both containments.

    Order matters: "temperature" is checked before the bare "temp"/"time" tokens so a
    TEMPERATURE column is never mistaken for a time column.
    """
    low = [h.lower().strip() for h in header]
    for w in wants:
        for i, h in enumerate(low):
            if not h:
                continue
            if w == h or w in h or h in w:
                return i
    return None


def load_raw():
    if not os.path.exists(RAW):
        raise SystemExit(f"cannot find {os.path.relpath(RAW, ROOT)}")
    with open(RAW, encoding="utf-8", errors="replace", newline="") as f:
        r = csv.reader(f)
        header = next(r)
        ti = _TIME_COL if _TIME_COL is not None else pick(
            header, "datetime", "timestamp", "date_time", "time", "date")
        qi = _Q_COL if _Q_COL is not None else pick(
            header, "discharge", "disch", "flow", "q_cfs", "cfs", "q")
        if ti is None or qi is None:
            raise SystemExit(
                "could not identify the time/discharge columns.\n\n  columns:\n"
                + "\n".join(f"    [{i}] {h!r}" for i, h in enumerate(header))
                + "\n\n  Name them explicitly and re-run:\n"
                  "    python scripts/check_gribble_observed.py --time-col N --q-col N")
        print(f"  raw columns   : time={header[ti]!r}  discharge={header[qi]!r}")
        rows, bad = [], 0
        for rec in r:
            if len(rec) <= max(ti, qi):
                bad += 1
                continue
            t = parse_ts(rec[ti])
            try:
                q = float(rec[qi])
            except ValueError:
                bad += 1
                continue
            if t is None:
                bad += 1
                continue
            rows.append((t, q))
    rows.sort()
    return rows, bad, header


def _cli():
    """--time-col N / --q-col N override auto-detection. Guessing a column in a file
    nobody has read is the one place this script could silently answer the wrong
    question, so it prints what it picked and lets you correct it."""
    global _TIME_COL, _Q_COL
    a = sys.argv[1:]
    for flag, name in (("--time-col", "_TIME_COL"), ("--q-col", "_Q_COL")):
        if flag in a:
            globals()[name] = int(a[a.index(flag) + 1])


def main():
    _cli()
    print("=" * 88)
    print("GRIBBLE GAP — do the derived observed columns match the raw flume record?")
    print("=" * 88)
    rows, bad, _ = load_raw()
    if not rows:
        raise SystemExit("no parseable rows in the raw record")

    gaps = [(rows[i + 1][0] - rows[i][0]).total_seconds() / 60.0
            for i in range(min(len(rows) - 1, 200_000))]
    step = st.median(gaps) if gaps else 0
    print(f"  rows          : {len(rows):,}  ({bad:,} unparseable)")
    print(f"  span          : {rows[0][0]:%Y-%m-%d} .. {rows[-1][0]:%Y-%m-%d}")
    print(f"  median step   : {step:.1f} min      (catchment Tc is ~9.7 min)")
    if step > 9.7:
        print("      ^ the sample interval is LONGER than Tc: a true peak can fall")
        print("        between samples, so peak_cfs is a lower bound, not the peak.")

    # Sampling interval BY YEAR. Added 2026-08-16 to test one specific hypothesis about
    # the factor-of-exactly-2 break in runoff_in: a derivation that assumed a FIXED time
    # step would double (or halve) its volumes across any change in the record's actual
    # step. This integrates on real timestamps, so it is right either way — which makes
    # it the thing that can tell them apart. A flat table here KILLS the hypothesis; a
    # step change at the same date as the break is very likely the cause.
    # Bucketed by MONTH, not year, then collapsed into contiguous regimes. A first
    # version bucketed by YEAR and reported "flat" on a synthetic record that genuinely
    # switched 5-min -> 10-min in mid-2016: the change year holds both regimes, so its
    # median just picks whichever is more numerous. The break being investigated is
    # itself mid-year, so yearly buckets were blind to exactly the case they were for.
    by_mon = {}
    for i in range(len(rows) - 1):
        d = (rows[i + 1][0] - rows[i][0]).total_seconds() / 60.0
        if 0 < d <= 720:                       # ignore multi-day outage gaps
            by_mon.setdefault(rows[i][0].strftime("%Y-%m"), []).append(d)
    regimes = []
    for mon in sorted(by_mon):
        m = round(st.median(by_mon[mon]), 1)
        if regimes and regimes[-1][2] == m:
            regimes[-1][1] = mon
            regimes[-1][3] += len(by_mon[mon])
        else:
            regimes.append([mon, mon, m, len(by_mon[mon])])
    print()
    print("  sampling regimes (does the record's own step change, and when?):")
    for lo, hi, m, n in regimes:
        span = lo if lo == hi else f"{lo} .. {hi}"
        print(f"      {span:<22} {m:>5.1f} min   n={n:>7,}")
    steps = [m for _, _, m, _ in regimes]
    if len(regimes) > 1 and max(steps) / min(steps) > 1.5:
        print("      ^^ THE STEP CHANGES. A derivation assuming a FIXED step would be wrong")
        print("         by that ratio on one side — check the change date against the")
        print("         runoff_in break.")
    else:
        print("      -> effectively flat: a fixed-step derivation does NOT explain a")
        print("         runoff_in factor. Next candidate is the drainage area (0.083 vs")
        print("         the adopted 0.166 sq mi would give exactly 2x).")
    print()

    inv = {r["peak_time"]: r for r in csv.DictReader(open(INV, encoding="utf-8"))}
    mc = list(csv.DictReader(open(MC, encoding="utf-8")))

    print("-" * 88)
    print(f"{'event':<12}{'inv peak':>10}{'raw max':>10}{'ratio':>8}{'n':>6}"
          f"{'inv runoff':>12}{'total in':>10}{'bf-sep in':>10}")
    print("-" * 88)

    rows_out, nowin = [], 0
    for r in mc:
        key = r["peak_time"]
        e = inv.get(key)
        if e is None:
            continue
        s_, en = parse_ts(e.get("start")), parse_ts(e.get("end"))
        if s_ is None or en is None:
            nowin += 1
            continue
        win = [(t, q) for t, q in rows if s_ <= t <= en]
        if len(win) < 2:
            nowin += 1
            continue
        raw_max = max(q for _, q in win)
        inv_pk = float(e.get("peak_cfs") or 0)
        vol = 0.0
        for i in range(len(win) - 1):
            vol += 0.5 * (win[i][1] + win[i + 1][1]) * \
                   (win[i + 1][0] - win[i][0]).total_seconds()
        base = min(win[0][1], win[-1][1])
        vol_sep = max(0.0, vol - base * (win[-1][0] - win[0][0]).total_seconds())
        rows_out.append((key[:10], inv_pk, raw_max, len(win),
                         float(r.get("runoff_in") or 0), vol, vol_sep))

    # ---- detect a constant scale BEFORE judging anything -------------------------
    # The raw record's units are documented nowhere. A units difference and a data
    # problem look identical event-by-event and want opposite responses, so resolve it
    # first and report the verdict in the corrected frame. The 2026-08-16 run found
    # 28.306 -> the record is in L/s.
    ratios = [rm / ip for _, ip, rm, _, _, _, _ in rows_out if ip]
    scale, why = 1.0, ""
    if len(ratios) > 2:
        med = st.median(ratios)
        # MAD, not stdev. The centre is a median because it must survive outliers; using
        # a NON-robust spread test alongside it is an inconsistency, and it bites hard:
        # one corrupted peak_cfs among four inflated the stdev past the threshold, the
        # units correction stopped firing, and ALL FOUR events then read as failures with
        # depths 28x too large. One bad row must not hide the scale for the good ones.
        mad = st.median([abs(x - med) for x in ratios]) / med if med else 9e9
        if mad < 0.05 and abs(med - 1.0) > 0.05:
            scale = med
            for f, name in ((28.316847, "litres per second"), (35.314667, "cubic metres per second"),
                            (0.0353147, "cubic feet per second stored as cms"), (1000.0, "millilitres per second")):
                if abs(med / f - 1.0) < 0.01:
                    why = f"  -> the raw record is in {name.upper()} ({f:.4f} per cfs)"
                    break
            why = why or f"  -> a constant factor of {med:.4f}, source unidentified"

    to_in = lambda v: v / scale / (DA_SQMI * SQMI_FT2) * 12.0
    if scale != 1.0:
        _mad = st.median([abs(x - scale) for x in ratios]) / scale * 100
        print(f"  units         : raw/inventory ratio is a constant {scale:.4f}"
              f" (MAD {_mad:.1f}%)")
        print(why)
        print("                  depths below are corrected by it; peaks are judged after it.")
        print()

    print("-" * 88)
    print(f"{'event':<12}{'inv peak':>10}{'raw/scale':>11}{'ratio':>8}{'n':>6}"
          f"{'inv runoff':>12}{'total in':>10}{'bf-sep in':>10}")
    print("-" * 88)
    pk_ratio, dep_tot, dep_sep, nomatch = [], [], [], 0
    for d, ip, rm, n, ir, vol, vsep in rows_out:
        scaled = rm / scale
        ratio = (scaled / ip) if ip else float("nan")
        pk_ratio.append(ratio)
        dep_tot.append(to_in(vol))
        dep_sep.append(to_in(vsep))
        if ip and abs(ratio - 1.0) > 0.02:
            nomatch += 1
        print(f"{d:<12}{ip:>10.2f}{scaled:>11.2f}{ratio:>8.3f}{n:>6}"
              f"{ir:>12.3f}{to_in(vol):>10.3f}{to_in(vsep):>10.3f}")

    print("-" * 88)
    if pk_ratio:
        exact = sum(1 for x in pk_ratio if abs(x - 1.0) <= 0.02)
        frame = " (after the units correction above)" if scale != 1.0 else ""
        print(f"  PEAK      : {exact} of {len(pk_ratio)} events match the raw maximum "
              f"within 2%{frame}")
        if exact == len(pk_ratio):
            print("      -> CONFIRMED. The observed side of Mode C reproduces from the")
            print("         record. validate_modeC's k values rest on real numbers.")
        else:
            print(f"      -> {nomatch} event(s) do not reproduce even after the scale is")
            print("         removed. That is a data problem, not a units one.")
    if dep_tot:
        print(f"  RUNOFF    : median total-volume depth {st.median(dep_tot):.3f} in, "
              f"baseflow-separated {st.median(dep_sep):.3f} in")
        print("      -> compare against the inv runoff column. A CONSISTENT offset is a")
        print("         different separation method or area. A ratio that CHANGES partway")
        print("         through the record is a provenance break — check both halves.")
    if nowin:
        print(f"  {nowin} event(s) had no usable window in the raw record.")
    print()
    print("Paste this whole block back.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
