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

# Cadence the collector is CONFIGURED for, from .github/workflows/ledger-stage.yml
# (cron "*/30 * * * *"). This is the declared value, never an observed one — the
# whole point of the sampling report below is that the two differ.
NOMINAL_CADENCE_MIN = 30

# Operational lead requirement, from basins.LEAD_REQ_MIN. A reach whose time of
# concentration is below this cannot make actionable lead on observation alone.
LEAD_REQ_MIN = 120
# The number the whole project is aimed at: ~90 minutes of warning cuts flood
# fatalities by more than 90%. Cited everywhere in this repo and, until now,
# measured nowhere.
LEAD_BENCHMARK_MIN = 90

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
def sampling(rows):
    """How often did the collector ACTUALLY look?

    WHY THIS IS NOT A DETAIL
    ------------------------
    Every correct negative in this ledger is one run where something looked and saw
    nothing. So the correct-negative count is proportional to HOW OFTEN THE COLLECTOR
    FIRED — and FAR and CSI both move with it, in the flattering direction, with no
    change whatsoever in the warning system.

    The module docstring already names that trap for unverified rows. This measures
    the other half of it: a cadence that silently drifts is a denominator that
    silently drifts. `--status` printing "span 19.9 h" invites a reader to assume
    19.9 hours were observed. If the collector fired 18 times in those hours against
    a 30-minute schedule, roughly half that span was never looked at.

    GitHub Actions documents that scheduled runs are delayed or dropped under load,
    so this is expected behaviour of the platform, not a bug to be fixed in code.
    What is fixable is reporting it instead of assuming it away.
    """
    ts = sorted({t for t in (_t(r.get("issued_utc")) for r in rows
                             if r.get("kind") == "model") if t})
    if len(ts) < 2:
        return {"n": len(ts), "gaps": [], "span_h": 0.0}
    gaps = [(b - a).total_seconds() / 60.0 for a, b in zip(ts, ts[1:])]
    gaps_sorted = sorted(gaps)
    span_min = (ts[-1] - ts[0]).total_seconds() / 60.0
    expected = span_min / NOMINAL_CADENCE_MIN + 1
    # A sample "covers" one nominal cadence window. Anything beyond that, nobody saw.
    unobserved = max(0.0, span_min - len(ts) * NOMINAL_CADENCE_MIN)
    return {
        "n": len(ts), "first": ts[0], "last": ts[-1], "span_h": span_min / 60.0,
        "gaps": gaps,
        "median_gap": gaps_sorted[len(gaps_sorted) // 2],
        "max_gap": max(gaps),
        "missed": sum(1 for g in gaps if g > 1.5 * NOMINAL_CADENCE_MIN),
        "expected": expected,
        "delivery": len(ts) / expected if expected else 0.0,
        "unobserved_h": unobserved / 60.0,
        "unobserved_frac": unobserved / span_min if span_min else 0.0,
    }


def lead(rows):
    """Nominal lead per model row: valid_utc - issued_utc.

    fetch_stage stamps `valid = issued + peak_hr`, so this is the modelled
    time-to-peak of the forcing the decision was made on. It is the honest
    horizon the system had, and it is NOT delivered warning time — see the
    caveat printed with it.

    Rows where the model produced no peak are EXCLUDED, not counted as zero
    lead. A dry hour has no lead to report, and averaging its zero into the
    distribution would drag the statistic toward zero in exactly the weather
    where lead does not matter. The count of them is reported instead.
    """
    per, dry = {}, 0
    for r in rows:
        if r.get("kind") != "model":
            continue
        a, b = _t(r.get("issued_utc")), _t(r.get("valid_utc"))
        if not a or not b:
            continue
        mins = (b - a).total_seconds() / 60.0
        q = r.get("q_cfs")
        try:
            no_peak = float(q) <= 0 if q not in (None, "") else True
        except ValueError:
            no_peak = True
        if mins <= 0 or no_peak:
            dry += 1
            continue
        per.setdefault(r.get("basin_id"), []).append(mins)
    return per, dry


def print_lead(rows):
    """Printed with the coverage block, for the same reason: a skill score
    without its lead is a claim about being right, not about being useful."""
    per, dry = lead(rows)
    print()
    print("=" * 78)
    print("LEAD  — how much warning the system actually had")
    print("=" * 78)
    total = sum(len(v) for v in per.values())
    if not total:
        print(f"  no model row carries a nonzero lead yet ({dry} rows had no modelled")
        print("  peak, which is the correct reading of dry weather, not zero lead).")
        print()
        print("  This stays empty until it rains hard enough for the engine to produce a")
        print("  peak. It is not a fault, and it is not evidence of anything either.")
        return per
    print(f"  {'basin':<16}{'n':>5}{'min':>8}{'median':>9}{'max':>8}   vs 120-min requirement")
    print("  " + "-" * 62)
    for bid in sorted(per):
        v = sorted(per[bid])
        med = v[len(v) // 2]
        short = sum(1 for x in v if x < LEAD_REQ_MIN)
        print(f"  {bid:<16}{len(v):>5}{min(v):>8.0f}{med:>9.0f}{max(v):>8.0f}"
              f"   {short} of {len(v)} below")
    allv = sorted(x for v in per.values() for x in v)
    under_bench = sum(1 for x in allv if x < LEAD_BENCHMARK_MIN)
    print("  " + "-" * 62)
    print(f"  {dry} row(s) excluded: no modelled peak, so no lead to report.")
    print(f"  {under_bench} of {total} decisions carried less than the {LEAD_BENCHMARK_MIN}-min")
    print(f"  benchmark at which warning stops changing outcomes.")
    print()
    print("  READ THIS AS A HORIZON, NOT AS DELIVERED WARNING. It is the modelled")
    print("  time-to-peak of the forcing, which is the most lead the system COULD have")
    print("  offered. Delivered lead is the gap between the first WATCH and the water")
    print("  arriving, and it cannot be computed until an event happens on a basin that")
    print("  has an observation source. Seven of eight do not.")
    return per


def print_sampling(rows):
    """Printed BEFORE any score, per the contract in the module docstring."""
    s = sampling(rows)
    print("=" * 78)
    print("SAMPLING CONTINUITY  — how often the collector actually looked")
    print("=" * 78)
    if s["n"] < 2:
        print(f"  {s['n']} sample(s). Nothing to say about cadence yet.")
        return s
    print(f"  samples         : {s['n']} over {s['span_h']:.1f} h "
          f"({s['first']:%Y-%m-%d %H:%M}Z .. {s['last']:%Y-%m-%d %H:%M}Z)")
    print(f"  configured every: {NOMINAL_CADENCE_MIN} min  "
          f"-> {s['expected']:.0f} expected")
    print(f"  delivered       : {100*s['delivery']:.0f}% of scheduled fires")
    print(f"  gap  median     : {s['median_gap']:.0f} min")
    print(f"  gap  worst      : {s['max_gap']:.0f} min")
    print(f"  gaps > {1.5*NOMINAL_CADENCE_MIN:.0f} min    : {s['missed']} of {len(s['gaps'])}")
    print(f"  never looked at : {s['unobserved_h']:.1f} h "
          f"({100*s['unobserved_frac']:.0f}% of the span)")
    if s["delivery"] < 0.9:
        print()
        print("  READ THE SPAN WITH CARE. The span above is NOT the observed period.")
        print("  Correct negatives are counted per run, so a cadence this far below")
        print("  its schedule shrinks the denominator — and FAR and CSI both improve")
        print("  when it does, with no change in the warning system. Compare skill")
        print("  scores across periods only when this line is comparable too.")
    return s


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

    print()
    print_sampling(rows)
    print_lead(rows)

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
    # Coverage before scores. A number without its denominator is not evidence.
    print_sampling(rows)
    print_lead(rows)
    print()
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
        elif c["hit"] + c["miss"] + c["false"] == 0:
            # The quiet-record trap. n is large, every row is verified, and every
            # single one is a correct negative — so all three scores are undefined
            # and STAY undefined however many more quiet rows arrive. Without this
            # branch the output is three n/a's beside a healthy-looking "verified
            # 35 / 296", and n >= min_sample so even the small-sample warning is
            # silent. A reader could easily take that for progress toward a score.
            print(f"    -> {c['correct_neg']} verified rows and NOTHING HAPPENED in any")
            print("       of them. POD, FAR and CSI need an event; with no hits, misses")
            print("       or false alarms there is no ratio to form. This does not")
            print("       improve by logging longer — only by a flood, or by a warning")
            print("       that turns out to be wrong. Quiet is not skill.")
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
    events = sum(1 for p in props
                 if p.get("proposed_outcome") == "flood"
                 or RANK.get(p.get("predicted_level", "NORMAL"), 0) > 0)
    if props and events == 0:
        print("  ...but all of them are QUIET periods: nothing was predicted above")
        print("  NORMAL and nothing happened. Accepting all of them raises outcome")
        print("  coverage without producing a single POD, FAR or CSI, because every")
        print("  one becomes a correct negative. Worth accepting — the denominator is")
        print("  real — but it is not progress toward a skill score.")
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

    print("\nlead")
    def _row(bid, iss, val, q):
        return {"kind": "model", "basin_id": bid, "issued_utc": iss,
                "valid_utc": val, "q_cfs": q}
    T0 = "2026-08-15T00:00:00Z"

    dry = [_row("CC-COX-097", T0, T0, "0.0") for _ in range(50)]
    per, n_dry = lead(dry)
    chk("50 dry rows produce NO lead statistic at all",
        per == {} and n_dry == 50, f"per={per} dry={n_dry}")
    chk("...because a dry hour has no lead, and averaging its zero in would drag "
        "the median toward zero exactly where lead does not matter", per == {})

    wet = [_row("CC-COX-097", T0, "2026-08-15T00:45:00Z", "410"),
           _row("CC-COX-097", T0, "2026-08-15T03:00:00Z", "520"),
           _row("CC-WCU-2260", T0, "2026-08-15T06:00:00Z", "2200")]
    per, n_dry = lead(dry + wet)
    chk("wet rows are counted and dry ones excluded",
        sorted(per) == ["CC-COX-097", "CC-WCU-2260"] and n_dry == 50, f"{per} {n_dry}")
    chk("lead is minutes from issued to valid",
        sorted(per["CC-COX-097"]) == [45.0, 180.0], per["CC-COX-097"])

    # a positive lead with a zero peak is still dry: q_cfs is what decides.
    per, n_dry = lead([_row("CC-COX-097", T0, "2026-08-15T05:00:00Z", "0")])
    chk("a long horizon with NO modelled peak is still not lead", per == {} and n_dry == 1)

    per, n_dry = lead([_row("CC-COX-097", T0, "2026-08-15T05:00:00Z", "")])
    chk("a missing q_cfs is treated as no peak, not as lead", per == {} and n_dry == 1)

    import io as _io2, contextlib as _cl2
    _b2 = _io2.StringIO()
    with _cl2.redirect_stdout(_b2):
        print_lead(dry + wet)
    txt2 = _b2.getvalue()
    chk("the report refuses to be read as delivered warning time",
        "NOT AS DELIVERED WARNING" in txt2 and "Seven of eight do not" in txt2)
    chk("...and counts decisions below the 90-min benchmark",
        f"{LEAD_BENCHMARK_MIN}-min" in txt2 and " of 3 decisions carried less" in txt2
        or "1 of 3" in txt2, txt2.splitlines()[-6:])

    print("\nsampling continuity")
    def _series(minutes):
        base = dt.datetime(2026, 8, 12, 0, 0, tzinfo=dt.timezone.utc)
        return [{"kind": "model", "basin_id": "CC-SPD-1830", "level": "NORMAL",
                 "outcome": "", "issued_utc": (base + dt.timedelta(minutes=m)
                                               ).strftime("%Y-%m-%dT%H:%M:%SZ")}
                for m in minutes]

    on_time = sampling(_series(range(0, 60 * 12, NOMINAL_CADENCE_MIN)))
    chk("a perfectly on-schedule series reports ~100% delivery",
        0.95 <= on_time["delivery"] <= 1.05, f"{100*on_time['delivery']:.0f}%")
    chk("...and no unobserved time",
        on_time["unobserved_frac"] < 0.02, f"{100*on_time['unobserved_frac']:.1f}%")
    chk("...and flags no missed fires", on_time["missed"] == 0)

    half = sampling(_series(range(0, 60 * 12, NOMINAL_CADENCE_MIN * 2)))
    chk("a half-rate series reports ~50% delivery",
        0.45 <= half["delivery"] <= 0.60, f"{100*half['delivery']:.0f}%")
    chk("...and flags every interval as a missed fire",
        half["missed"] == len(half["gaps"]), f"{half['missed']}/{len(half['gaps'])}")
    chk("...and reports ~half the span never looked at",
        0.40 <= half["unobserved_frac"] <= 0.55, f"{100*half['unobserved_frac']:.0f}%")

    # The one that matters: a long outage must not hide inside a clean median.
    ragged = sampling(_series([0, 30, 60, 90, 570, 600, 630, 660]))
    chk("a single long outage is visible in the worst gap even when the median is clean",
        ragged["median_gap"] <= NOMINAL_CADENCE_MIN and ragged["max_gap"] > 400,
        f"median {ragged['median_gap']:.0f} min, worst {ragged['max_gap']:.0f} min")
    chk("...and that outage is counted as unobserved time",
        ragged["unobserved_h"] > 4.0, f"{ragged['unobserved_h']:.1f} h")

    chk("cadence comes from the declared schedule, never inferred from the data",
        NOMINAL_CADENCE_MIN == 30)

    print("\nthe quiet-record trap")
    quiet = [R("NORMAL", "no_flood") for _ in range(35)]
    c = contingency(quiet, "WATCH")
    pod, far, csi = scores(c)
    chk("35 quiet verified rows give 35 correct negatives and nothing else",
        (c["hit"], c["miss"], c["false"], c["correct_neg"]) == (0, 0, 0, 35))
    chk("...so all three scores are undefined, not zero",
        pod is None and far is None and csi is None)
    import io as _io, contextlib as _cl
    _b = _io.StringIO()
    with _cl.redirect_stdout(_b):
        cmd_score(quiet, 20)
    txt = _b.getvalue()
    chk("...and --score says so out loud rather than printing three bare n/a's",
        "NOTHING HAPPENED" in txt and "Quiet is not skill" in txt)
    chk("...even though n=35 clears the small-sample floor and would otherwise "
        "print no caveat at all", "SAMPLE TOO SMALL" not in txt)
    _b = _io.StringIO()
    with _cl.redirect_stdout(_b):
        cmd_score(quiet + [R("WATCH", "flood")], 20)
    chk("one real event removes the warning", "Quiet is not skill" not in _b.getvalue())

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
