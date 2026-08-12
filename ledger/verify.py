#!/usr/bin/env python3
"""
verify.py — score the decision ledger, and be honest about what cannot be scored.

    python ledger/verify.py --status                 what the ledger can and cannot answer
    python ledger/verify.py --score                  POD / FAR / CSI from filled outcomes
    python ledger/verify.py --propose                candidate outcomes, for review
    python ledger/verify.py --selftest               offline, no ledger needed

WHY THIS EXISTS
---------------
`ledger/fetch_stage.py` gained outcome columns on 2026-08-12 because the log could not
support the verification its own workflow header claims for it. Columns alone do not
score anything. This reads them.

THE THING THIS TOOL EXISTS TO SAY OUT LOUD
------------------------------------------
Seven of the eight basins have **no observation source at all**. One FIMAN gauge
(site 25380) is the entire measured record for a 23 mi² watershed. For those seven,
POD / FAR / CSI are not "low" or "pending" — they are **undefined**, and they stay
undefined until sensors land. No amount of logging fixes that.

The failure mode this guards against is subtle and common: score the one observable
basin, print a number, and let it be read as the system's skill. A skill score computed
over 1 of 8 basins is a statement about one gauge, not about a warning system. So every
output here carries its denominator, and the coverage line comes before the scores.

WHY UNVERIFIED ROWS ARE NOT COUNTED AS CORRECT NEGATIVES
--------------------------------------------------------
The tempting shortcut is to treat every quiet logged hour as a true negative. It is
wrong, and it is wrong in the direction that flatters the system: FAR falls and CSI
rises simply by logging more often, with no improvement in anything. A row is a correct
negative only when something actually looked and saw nothing. `outcome` empty means
nobody looked. Those rows are counted and reported, never scored.

    hit            predicted >= level, and it happened
    miss           did not predict, and it happened
    false alarm    predicted >= level, and it did not happen
    correct neg    did not predict, and confirmed nothing happened
    unverified     outcome empty — EXCLUDED, reported separately

    POD = hits / (hits + misses)              did we catch what happened?
    FAR = false / (hits + false)              how much of what we cried was wolf?
    CSI = hits / (hits + misses + false)      both at once; ignores correct negatives

CSI is the headline because it is the one that does not improve by logging more quiet
hours. POD without FAR is gameable by warning constantly; FAR without POD by never
warning. Report all three or none.
"""

import argparse
import collections
import csv
import datetime as dt
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LEDGER = os.path.join(HERE, "decisions.csv")
DEFAULT_PROPOSALS = os.path.join(HERE, "outcome_proposals.csv")

LEVELS = ["NORMAL", "WATCH", "WARNING", "EMERGENCY"]
RANK = {L: i for i, L in enumerate(LEVELS)}
OUTCOME_OK = {"flood", "no_flood", "unknown", ""}

# How long after a decision we consider it "covered". A decision is about the near
# future; scoring it against a window shorter than the basin's response time would
# mark a correct warning as a false alarm.
DEFAULT_WINDOW_MIN = 180

# Basins with a measured source today. Everything else cannot be verified at all.
# Keep this in step with reality — an entry here that has no live feed is worse than
# no entry, because it makes an unverifiable basin look verifiable.
OBSERVED_BY = {"CC-SPD-1830": "FIMAN 25380"}


def load(path):
    if not os.path.exists(path):
        sys.exit(f"{path} not found — run ledger/fetch_stage.py --csv first")
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if rows and "outcome" not in rows[0]:
        sys.exit(f"{path} has no outcome column. Run ledger/fetch_stage.py once to "
                 "migrate the schema, then come back.")
    bad = {r.get("outcome", "") for r in rows} - OUTCOME_OK
    if bad:
        sys.exit(f"unrecognised outcome value(s): {sorted(bad)}. "
                 f"Allowed: flood / no_flood / unknown / empty.")
    return rows


def _t(s):
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def contingency(rows, level, kind="model"):
    """-> Counter over hit/miss/false/correct_neg/unverified for one level threshold."""
    c = collections.Counter()
    for r in rows:
        if r.get("kind") != kind:
            continue
        predicted = RANK.get(r.get("level", "NORMAL"), 0) >= RANK[level]
        oc = (r.get("outcome") or "").strip()
        if oc in ("", "unknown"):
            c["unverified"] += 1
            continue
        happened = oc == "flood"
        if predicted and happened:
            c["hit"] += 1
        elif predicted and not happened:
            c["false"] += 1
        elif not predicted and happened:
            c["miss"] += 1
        else:
            c["correct_neg"] += 1
    return c


def scores(c):
    h, m, f = c["hit"], c["miss"], c["false"]
    pod = h / (h + m) if (h + m) else None
    far = f / (h + f) if (h + f) else None
    csi = h / (h + m + f) if (h + m + f) else None
    return pod, far, csi


def _fmt(v):
    return "  n/a" if v is None else f"{v:5.2f}"


# --------------------------------------------------------------------------- #
def cmd_status(rows):
    basins = sorted({r["basin_id"] for r in rows if r.get("basin_id")})
    model = [r for r in rows if r.get("kind") == "model"]
    obs = [r for r in rows if r.get("kind") == "obs"]
    ts = [t for t in (_t(r.get("issued_utc") or r.get("valid_utc")) for r in rows) if t]

    print("=" * 78)
    print("LEDGER STATUS")
    print("=" * 78)
    print(f"  rows            : {len(rows)}  ({len(model)} model, {len(obs)} observed)")
    if ts:
        span = (max(ts) - min(ts)).total_seconds() / 3600.0
        print(f"  span            : {min(ts):%Y-%m-%d %H:%M}Z .. {max(ts):%Y-%m-%d %H:%M}Z"
              f"  ({span:.1f} h)")
    print(f"  basins present  : {len(basins)}")

    print("\n" + "=" * 78)
    print("WHAT CAN BE VERIFIED AT ALL")
    print("=" * 78)
    print(f"  {'basin':<16}{'model rows':>12}{'observed':>11}   verifiable?")
    print("  " + "-" * 62)
    n_obs_basins = 0
    for b in basins:
        nm = sum(1 for r in model if r["basin_id"] == b)
        no = sum(1 for r in obs if r["basin_id"] == b)
        src = OBSERVED_BY.get(b)
        if src:
            n_obs_basins += 1
        print(f"  {b:<16}{nm:>12}{no:>11}   "
              + (f"yes — {src}" if src else "NO — no measured source exists"))
    print("  " + "-" * 62)
    print(f"  {n_obs_basins} of {len(basins)} basins can ever produce a verification sample.")
    if n_obs_basins < len(basins):
        print("\n  For the rest, POD / FAR / CSI are UNDEFINED — not low, not pending.")
        print("  Logging more rows does not change this. Sensors do.")

    unv = sum(1 for r in model if (r.get("outcome") or "").strip() in ("", "unknown"))
    print("\n" + "=" * 78)
    print("OUTCOME COVERAGE")
    print("=" * 78)
    print(f"  model rows with an outcome recorded : {len(model)-unv} / {len(model)}"
          f"  ({100*(len(model)-unv)/max(1,len(model)):.0f}%)")
    if unv == len(model):
        print("\n  NOTHING has been verified yet. --score will report no numbers, which")
        print("  is the correct answer, not a failure. Use --propose to generate")
        print("  candidate outcomes from the measured record, then review them.")
    return 0


def cmd_score(rows, min_sample):
    model = [r for r in rows if r.get("kind") == "model"]
    print("=" * 78)
    print("SKILL SCORES")
    print("=" * 78)
    total_scored = 0
    for level in ("WATCH", "WARNING", "EMERGENCY"):
        c = contingency(rows, level)
        n = c["hit"] + c["miss"] + c["false"] + c["correct_neg"]
        total_scored += c["hit"] + c["miss"] + c["false"]
        pod, far, csi = scores(c)
        print(f"\n  [{level} or above]   verified {n} / {len(model)} rows"
              f"   ({c['unverified']} unverified, excluded)")
        print(f"    hits {c['hit']}   misses {c['miss']}   false alarms {c['false']}"
              f"   correct negatives {c['correct_neg']}")
        print(f"    POD {_fmt(pod)}    FAR {_fmt(far)}    CSI {_fmt(csi)}")
        if n == 0:
            print("    -> nothing verified at this level. No score exists. This is not")
            print("       a score of zero; it is the absence of one.")
        elif n < min_sample:
            print(f"    -> SAMPLE TOO SMALL (n={n} < {min_sample}). These numbers will")
            print("       move a lot on the next event. Do not quote them.")

    obs_only = [b for b in {r['basin_id'] for r in model} if b not in OBSERVED_BY]
    if obs_only:
        print("\n" + "!" * 78)
        print(f"COVERAGE WARNING: {len(obs_only)} of "
              f"{len({r['basin_id'] for r in model})} basins have no measured source and")
        print("cannot contribute a verification sample. Any score above describes the")
        print("basins that CAN be observed, not the system.")
        print("Unverifiable: " + ", ".join(sorted(obs_only)))
        print("!" * 78)
    if total_scored == 0:
        print("\nNo events have been verified. That is the honest state of the record.")
    return 0


def cmd_propose(rows, path_out, window_min):
    """Emit CANDIDATE outcomes to a separate file. Never writes to the ledger.

    Only proposes from a PRESENT observation record. An absence of obs rows produces
    no proposal at all — never a 'no_flood'. That distinction is the whole point: a
    quiet gauge is evidence; a missing gauge is not.
    """
    model = [r for r in rows if r.get("kind") == "model"]
    obs = [r for r in rows if r.get("kind") == "obs"]
    by_basin = collections.defaultdict(list)
    for r in obs:
        t = _t(r.get("valid_utc"))
        if t:
            by_basin[r["basin_id"]].append((t, r))
    for b in by_basin:
        by_basin[b].sort(key=lambda x: x[0])

    win = dt.timedelta(minutes=window_min)
    props, skipped = [], collections.Counter()
    for r in model:
        b = r["basin_id"]
        t0 = _t(r.get("issued_utc") or r.get("valid_utc"))
        if t0 is None:
            skipped["unparseable timestamp"] += 1
            continue
        if b not in OBSERVED_BY:
            skipped["no measured source for this basin"] += 1
            continue
        cover = [(t, o) for t, o in by_basin.get(b, []) if t0 <= t <= t0 + win]
        if not cover:
            skipped["no observation covering the window"] += 1
            continue
        worst = max(RANK.get(o.get("level", "NORMAL"), 0) for _, o in cover)
        props.append({
            "basin_id": b, "issued_utc": r.get("issued_utc"),
            "predicted_level": r.get("level"),
            "observed_peak_level": LEVELS[worst],
            "proposed_outcome": "flood" if worst >= RANK["WATCH"] else "no_flood",
            "outcome_src": OBSERVED_BY[b],
            "n_obs_in_window": len(cover),
            "window_min": window_min,
            "note": "AUTOMATED PROPOSAL — review before copying into decisions.csv",
        })

    print("=" * 78)
    print(f"OUTCOME PROPOSALS  (window {window_min} min)")
    print("=" * 78)
    if skipped:
        print("  not proposed:")
        for why, n in skipped.most_common():
            print(f"    {n:>5}  {why}")
        print("  Every one of those is correctly left EMPTY. An unobserved window is not")
        print("  a quiet one, and recording it as 'no_flood' would inflate every score.")
    if not props:
        print("\n  No proposals. Nothing in this ledger has a measured record covering it.")
        return 0
    with open(path_out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(props[0]))
        w.writeheader()
        w.writerows(props)
    agree = sum(1 for p in props
                if (p["proposed_outcome"] == "flood")
                == (RANK.get(p["predicted_level"], 0) >= RANK["WATCH"]))
    print(f"\n  {len(props)} proposal(s) -> {path_out}")
    print(f"  prediction and observation agree on {agree}/{len(props)}")
    print("\n  THIS FILE IS NOT THE LEDGER. Review each row, then copy accepted outcomes")
    print("  into decisions.csv yourself. A log that fills in its own outcomes is")
    print("  marking its own homework.")
    return 0


# --------------------------------------------------------------------------- #
def selftest():
    ok = True

    def chk(name, cond, detail=""):
        nonlocal ok
        ok &= bool(cond)
        print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))

    print("verify.py — offline self-test")
    print("=" * 78)
    R = lambda lvl, oc: {"kind": "model", "basin_id": "CC-SPD-1830",
                         "level": lvl, "outcome": oc}

    print("contingency table")
    c = contingency([R("WATCH", "flood"), R("NORMAL", "flood"),
                     R("WATCH", "no_flood"), R("NORMAL", "no_flood")], "WATCH")
    chk("hit / miss / false / correct-neg each land once",
        (c["hit"], c["miss"], c["false"], c["correct_neg"]) == (1, 1, 1, 1), str(dict(c)))
    pod, far, csi = scores(c)
    chk("POD = 1/(1+1) = 0.5", abs(pod - 0.5) < 1e-9)
    chk("FAR = 1/(1+1) = 0.5", abs(far - 0.5) < 1e-9)
    chk("CSI = 1/(1+1+1) = 0.333", abs(csi - 1 / 3) < 1e-9)

    print("\nunverified rows must not become correct negatives")
    c2 = contingency([R("NORMAL", ""), R("NORMAL", ""), R("NORMAL", "unknown")], "WATCH")
    chk("empty and 'unknown' both count as unverified", c2["unverified"] == 3, str(dict(c2)))
    chk("they do NOT appear as correct negatives", c2["correct_neg"] == 0)
    chk("no score is produced from them", scores(c2) == (None, None, None))

    print("\nadding quiet unverified rows cannot improve any score")
    base = [R("WATCH", "flood"), R("WATCH", "no_flood")]
    s1 = scores(contingency(base, "WATCH"))
    s2 = scores(contingency(base + [R("NORMAL", "")] * 500, "WATCH"))
    chk("500 unverified quiet rows leave POD/FAR/CSI identical", s1 == s2,
        f"{tuple(_fmt(x) for x in s1)} vs {tuple(_fmt(x) for x in s2)}")

    print("\nescalation ordering")
    chk("EMERGENCY counts as WATCH-or-above",
        contingency([R("EMERGENCY", "flood")], "WATCH")["hit"] == 1)
    chk("WATCH does not count as WARNING-or-above",
        contingency([R("WATCH", "flood")], "WARNING")["miss"] == 1)

    print("\nproposal safety")
    chk("only basins with a real source are proposable",
        set(OBSERVED_BY) == {"CC-SPD-1830"}, str(OBSERVED_BY))
    chk("outcome vocabulary is closed", OUTCOME_OK == {"flood", "no_flood", "unknown", ""})

    print("\n" + "=" * 78)
    print("SELF-TEST " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--propose", action="store_true")
    ap.add_argument("--out", default=DEFAULT_PROPOSALS)
    ap.add_argument("--window-min", type=int, default=DEFAULT_WINDOW_MIN)
    ap.add_argument("--min-sample", type=int, default=20,
                    help="below this, scores are printed with a do-not-quote warning")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if not (a.status or a.score or a.propose):
        a.status = True
    rows = load(a.ledger)
    rc = 0
    if a.status:
        rc |= cmd_status(rows)
    if a.score:
        rc |= cmd_score(rows, a.min_sample)
    if a.propose:
        rc |= cmd_propose(rows, a.out, a.window_min)
    return rc


if __name__ == "__main__":
    sys.exit(main())
