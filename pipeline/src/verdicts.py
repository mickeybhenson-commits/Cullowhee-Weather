"""Automated cluster screening — metrics only, never analyst judgment.

This module exists because of a specific failure mode. The published pages
carry per-cluster verdicts in analyst voice ("seasonal artifact",
"candidate — re-check each pass"). Those sentences were written by a person
looking at the series. A scheduled job cannot write them and must not pretend
to: an automated run that invents "seasonal artifact" for a slope that is
actually moving is a safety failure, and one that invents "candidate" for
canopy noise burns the credibility that makes a real call actionable.

So CI computes a *screening* class from the numbers, and says so. Every verdict
this module produces is stamped `automated screening — pending analyst review`
on the pages, and no cluster is ever labelled WARNING here — WARNING comes only
from the pipeline's own escalation gates in detect/forecast/alert.

The rules are the ones fixed in PLAN.md; the operational readings of the two
phrases that needed one are documented on the functions below.

    python -m src.verdicts        # run the self-test
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np

REVIEW_LABEL = "automated screening — pending analyst review"

CANDIDATE = "candidate"
SUSPECT = "suspect artifact"
LOW = "low-confidence detection"

# Thresholds are fixed by PLAN.md's verdict policy. They are screening rules,
# not the tuned detection thresholds in config.yaml.
NET_MOTION_MM = 25.0        # |net motion| a candidate must clear
AGREEMENT_STEPS = 8         # "last-8-step direction agreement"
AGREEMENT_MIN = 0.85        # ... >= 85 %
STEP_JUMP_MM = 8.0          # a single step this big reads as an unwrapping jump
LEAF_OFF_MONTHS = (11, 12, 1, 2)


@dataclass
class Screening:
    verdict: str                 # CANDIDATE / SUSPECT / LOW
    style: str                   # map style key: hi / mid / suspect / low
    net_mm: float
    agree8: float
    max_step_mm: float
    leaf_off_mm: float | None    # same-direction motion inside Nov–Feb, mm
    reason: str                  # metric sentence, no analyst voice

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "style": self.style,
            "net_mm": round(self.net_mm, 1),
            "agree8": round(self.agree8, 2),
            "max_step_mm": round(self.max_step_mm, 1),
            "leaf_off_mm": None if self.leaf_off_mm is None else round(self.leaf_off_mm, 1),
            "reason": self.reason,
            "review": REVIEW_LABEL,
        }


# --------------------------------------------------------------------------
# the three metric tests
# --------------------------------------------------------------------------

def direction_agreement(series: np.ndarray, n: int = AGREEMENT_STEPS) -> float:
    """Fraction of the last n steps that share the sign of the net motion."""
    steps = np.diff(np.asarray(series, float))[-n:]
    steps = steps[np.isfinite(steps)]
    if steps.size == 0:
        return 0.0
    net_sign = np.sign(np.nansum(np.diff(np.asarray(series, float))))
    if net_sign == 0:
        return 0.0
    return float(np.mean(np.sign(steps) == net_sign))


def leaf_off_motion(series: np.ndarray, dates: list[date]) -> tuple[float, float] | None:
    """(same-direction motion in mm, days spanned) across leaf-off (Nov–Feb) epochs.

    PLAN.md requires "motion present in leaf-off epochs (Nov–Feb) if the record
    includes them". Read operationally: sum the steps that *land* on a Nov–Feb
    epoch, signed by the direction of the series' net motion. A positive sum
    means the slope was still moving the same way with the canopy down, which
    is the one season C-band cannot blame for the signal. Returns None when the
    record contains no such step — the clause is then not applicable, exactly as
    the plan's "if" says.
    """
    s = np.asarray(series, float)
    steps = np.diff(s)
    net_sign = np.sign(s[-1] - s[0])
    if net_sign == 0:
        net_sign = 1.0
    mm = days = 0.0
    for i in range(len(steps)):
        if dates[i + 1].month in LEAF_OFF_MONTHS:
            mm += steps[i]
            days += (dates[i + 1] - dates[i]).days
    if days == 0:
        return None
    return float(mm * net_sign), days


def single_jump(series: np.ndarray) -> tuple[float, bool]:
    """(largest signed step, does one step explain the whole record?).

    PLAN.md: "onset coincides with a single >=8 mm step between adjacent
    epochs". Read operationally, all three must hold: the biggest single step
    clears 8 mm; removing it leaves less than the 25 mm sustained-motion bar;
    and it carries at least half the record. The last two are what keep the
    test off real creep — a slope that steps 10 mm and then keeps moving still
    has its motion after the step is taken out, so it is not written off here.
    """
    steps = np.diff(np.asarray(series, float))
    if steps.size == 0:
        return 0.0, False
    biggest = float(steps[int(np.nanargmax(np.abs(steps)))])
    net = float(np.asarray(series, float)[-1] - np.asarray(series, float)[0])
    explained = (
        abs(biggest) >= STEP_JUMP_MM
        and abs(net - biggest) < NET_MOTION_MM
        and abs(biggest) >= 0.5 * abs(net)
    )
    return biggest, explained


# --------------------------------------------------------------------------
# the policy
# --------------------------------------------------------------------------

def screen(series, dates: list[date], velocity_mm_yr: float) -> Screening:
    """Apply PLAN.md's verdict policy to one cluster's displacement series.

    Order is the plan's own: candidate -> suspect artifact -> otherwise
    low-confidence. The candidate test is the affirmative one and is checked
    first, which is also the cautious direction — a moving slope stays on the
    list rather than being written off. Its three gates are strict enough that
    a record clearing them cannot also be jump-explained (removing the jump
    would have to leave under 25 mm, but the gate requires 25 mm of net motion
    with 85 % of the last 8 steps agreeing).

    Nothing safety-critical hangs on this class. Escalation to ADVISORY /
    WATCH / WARNING is decided entirely by detect / forecast / alert against
    the tuned config.yaml thresholds; screening only decides how a cluster is
    described for review.
    """
    s = np.asarray(series, float)
    net = float(s[-1] - s[0])
    agree = direction_agreement(s)
    leaf = leaf_off_motion(s, dates)
    lo = None if leaf is None else leaf[0]
    biggest, jump_explains = single_jump(s)
    v_contradicts = (
        np.isfinite(velocity_mm_yr)
        and np.sign(velocity_mm_yr) != 0
        and np.sign(net) != 0
        and np.sign(velocity_mm_yr) != np.sign(net)
    )

    leaf_off_ok = leaf is None or lo > 0
    if abs(net) >= NET_MOTION_MM and agree >= AGREEMENT_MIN and leaf_off_ok:
        # "credible" vs "weaker" is a metric split, not a judgment: compare the
        # rate the slope moved with the canopy down against its rate over the
        # whole record. A slope that kept at least half its pace through
        # leaf-off is not something summer decorrelation can produce.
        total_days = max((dates[-1] - dates[0]).days, 1)
        strong = leaf is not None and leaf[1] > 0 and (
            (lo / leaf[1]) >= 0.5 * (abs(net) / total_days)
        )
        style = "hi" if strong else "mid"
        if leaf is None:
            why = "no leaf-off epochs in the record yet"
        elif strong:
            why = (f"{lo:+.0f} mm of it through leaf-off (Nov–Feb), holding "
                   f"{lo / leaf[1] / (abs(net) / total_days):.0%} of its overall rate")
        else:
            why = f"only {lo:+.0f} mm of it through leaf-off (Nov–Feb)"
        return Screening(
            CANDIDATE, style, net, agree, biggest, lo,
            f"{net:+.0f} mm net over the record, {agree:.0%} of the last "
            f"{AGREEMENT_STEPS} steps in the same direction, {why}.",
        )

    if jump_explains or v_contradicts:
        if jump_explains:
            why = (f"a single {biggest:+.0f} mm step between adjacent epochs "
                   f"accounts for the {net:+.0f} mm record")
        else:
            why = (f"fitted velocity ({velocity_mm_yr:+.0f} mm/yr) contradicts the "
                   f"series' own net direction ({net:+.0f} mm)")
        return Screening(SUSPECT, "suspect", net, agree, biggest, lo,
                         why[0].upper() + why[1:] + ".")

    bits = []
    if abs(net) < NET_MOTION_MM:
        bits.append(f"net motion {net:+.0f} mm is under the {NET_MOTION_MM:.0f} mm bar")
    if agree < AGREEMENT_MIN:
        bits.append(f"only {agree:.0%} of the last {AGREEMENT_STEPS} steps agree in direction")
    if leaf is not None and lo <= 0:
        bits.append("no same-direction motion in the leaf-off (Nov–Feb) epochs "
                    "the record already contains")
    return Screening(LOW, "low", net, agree, biggest, lo,
                     ("Screened out: " + "; ".join(bits) + ".") if bits
                     else "Screened out: no test cleared.")


def escalation_note(alert_level: str) -> str:
    """Wording for a level the *pipeline* produced, not the screener.

    WARNING is never written by this module; it can only reach a page because
    detect/forecast/alert's own gates fired.
    """
    return {
        "WARNING": "Pipeline escalation: accelerating, persistent, and the "
                   "inverse-velocity forecast converges inside the warning horizon.",
        "WATCH": "Pipeline escalation: fast, accelerating and persistent across epochs.",
        "ADVISORY": "Pipeline flag: velocity above threshold, persistence not established.",
    }.get(alert_level, "")


# --------------------------------------------------------------------------
# self-test — the workflow runs this before it is allowed to publish
# --------------------------------------------------------------------------

def _dates(n: int, start=date(2025, 8, 11), step_days: int = 12) -> list[date]:
    from datetime import timedelta
    return [start + timedelta(days=step_days * i) for i in range(n)]


def selftest() -> bool:
    d30 = _dates(30)                     # Aug 2025 -> Aug 2026, crosses a winter
    d12 = _dates(12, date(2026, 3, 1))   # spring -> autumn 2026, no leaf-off

    steady = np.linspace(0, 51, 30)                       # +51 mm all year
    flat = np.zeros(30)
    jump = np.concatenate([np.zeros(15), np.full(15, 12.0)])
    late = np.concatenate([np.zeros(22), np.linspace(0, 36, 8)])   # onset May-ish
    wobble = np.array([0, 6, -4, 9, -7, 11, -9, 14, -12, 17, -14, 20,
                       -17, 23, -19, 26, -21, 28, -24, 30, -26, 33,
                       -28, 35, -30, 38, -32, 40, -34, 42], float)
    springonly = np.linspace(0, 40, 12)                   # no leaf-off epochs at all
    # moved, then relaxed back: net is still +30 but the fit's velocity is negative
    reversed_ = np.concatenate([np.linspace(0, 40, 20), np.linspace(40, 30, 10)])
    # creep that happens to contain one big step — must NOT read as a jump
    creep_step = np.concatenate([np.linspace(0, 20, 15),
                                 np.linspace(30, 51, 15)])

    cases = [
        ("steady all-year motion", steady, d30, 50.0, CANDIDATE, "hi"),
        ("motionless slope", flat, d30, 0.0, LOW, "low"),
        ("single unwrapping step", jump, d30, 12.0, SUSPECT, "suspect"),
        # A May-onset cluster whose winter is already in the record shows no
        # leaf-off motion, so the plan's third candidate gate fails by its own
        # terms. It stays on the list as low-confidence until a winter passes
        # under it — which is exactly what the page's own text promises.
        ("leaf-on onset, winter already in record", late, d30, 40.0, LOW, "low"),
        ("noisy sign-flipping series", wobble, d30, 40.0, LOW, "low"),
        ("short spring-only record", springonly, d12, 40.0, CANDIDATE, "mid"),
        ("motion then relaxation", reversed_, d30, -30.0, SUSPECT, "suspect"),
        ("creep containing one big step", creep_step, d30, 50.0, CANDIDATE, "hi"),
    ]
    ok = True
    for name, series, dates, v, want, want_style in cases:
        got = screen(series, dates, v)
        good = got.verdict == want and got.style == want_style
        ok &= good
        print(f"  [{'PASS' if good else 'FAIL'}] {name}: {got.verdict}/{got.style} "
              f"(expected {want}/{want_style}) — {got.reason}")

    # Structural guarantees, not just examples.
    if any(screen(s, d, v).verdict not in (CANDIDATE, SUSPECT, LOW)
           for _, s, d, v, _, _ in cases):
        print("  [FAIL] a verdict outside the three allowed classes")
        ok = False
    if "WARNING" in " ".join(screen(s, d, v).reason for _, s, d, v, _, _ in cases):
        print("  [FAIL] the screener emitted the word WARNING")
        ok = False
    if REVIEW_LABEL not in screen(steady, d30, 50.0).as_dict()["review"]:
        print("  [FAIL] the pending-analyst-review stamp is missing")
        ok = False

    print("verdict policy selftest", "PASSED" if ok else "FAILED")
    return bool(ok)


if __name__ == "__main__":
    raise SystemExit(0 if selftest() else 1)
