"""
test_posture_rules.py — exhaustive proof that absent evidence cannot lower a posture.

These are not example-based tests. Every function is checked over the FULL cross product
of levels and presence flags, because the failure mode being guarded against is exactly
the corner nobody thought to write an example for.

    python test_posture_rules.py          # no pytest needed

APPLIED TO A DEPLOYED ENGINE, 2026-08-16
----------------------------------------
Everything above the "deployed engine" section below tests posture_rules against itself
or against a toy function. That was the whole suite until today, and it left the module
100% self-tested and 0% APPLIED: posture_rules states the single most important safety
rule in this system — "absent corroboration must never downgrade a posture" — and no
engine imported it, so nothing checked that any engine obeyed it. test_wired.py has been
printing posture_rules under TEST-ONLY REACHABLE for exactly this reason.

check_monotone() was built to be pointed at a real rule. Pointing it at flood_engine —
the engine behind the published feed, and therefore behind the phone notification —
found that the LEVEL is clean (it never depended on the corroborators at all) and that
the PROBABILITY was not: absent inputs were coerced to zero, so losing a soil probe
scored as bone-dry ground and losing a rain gauge scored as no rain. Both weights are
positive. See early_warning_probability()'s docstring for the numbers.
"""
import itertools

from posture_rules import (ORDER, _i, combine, escalate, corroborate,
                           check_monotone, PostureRuleViolation)

ABSENT = [None, "N/A", "", "unknown", "n/a"]


def test_absent_never_contributes_severity():
    """Every spelling of 'absent' must floor at NORMAL — never be read as severe."""
    for a in ABSENT:
        assert combine(a) == "NORMAL", f"{a!r} was read as {combine(a)}"


def test_absent_never_lowers_a_present_level():
    """The central rule, over every level x every spelling of absent."""
    for lv, a in itertools.product(ORDER, ABSENT):
        assert combine(lv, a) == lv, f"combine({lv!r}, {a!r}) = {combine(lv, a)}"
        assert combine(a, lv) == lv, f"combine({a!r}, {lv!r}) = {combine(a, lv)}"


def test_combine_is_a_monotone_max_over_all_pairs():
    for a, b in itertools.product(ORDER, repeat=2):
        got = combine(a, b)
        want = a if _i(a) >= _i(b) else b
        assert got == want, f"combine({a},{b}) = {got}, want {want}"


def test_combine_is_order_independent():
    for a, b, c in itertools.product(ORDER, repeat=3):
        results = {combine(*p) for p in itertools.permutations((a, b, c))}
        assert len(results) == 1, f"order-dependent: {a},{b},{c} -> {results}"


def test_escalate_cannot_lower():
    for cur, cand, present in itertools.product(ORDER, ORDER, [True, False]):
        got = escalate(cur, cand, evidence_present=present)
        assert _i(got) >= _i(cur), (
            f"escalate({cur}, {cand}, present={present}) = {got} — LOWERED")


def test_escalate_without_evidence_is_identity():
    """evidence_present=False means 'we could not look', not 'we looked and saw nothing'."""
    for cur, cand in itertools.product(ORDER, repeat=2):
        assert escalate(cur, cand, evidence_present=False) == cur


def test_corroborate_never_changes_the_level():
    """A corroborating source may confirm. It may never veto, and it may never demote —
    not when it disagrees, and above all not when it is offline."""
    for lv, corr, avail in itertools.product(ORDER, [True, False], [True, False]):
        got = corroborate(lv, corroborated=corr, available=avail)
        assert got == lv, (
            f"corroborate({lv}, corroborated={corr}, available={avail}) = {got}")


def test_the_floodnet_veto_bug_is_impossible():
    """The concrete production failure this module exists to prevent.

    A stage rise says WARNING. The rain gauge that would 'confirm' it is offline —
    because the storm took the link down. The naive AND-rule suppresses the warning.
    """
    stage_says = "WARNING"
    rain_gauge_online = False        # the storm killed the link

    naive = stage_says if rain_gauge_online else "NORMAL"      # the bug
    correct = corroborate(stage_says, corroborated=False, available=rain_gauge_online)

    assert naive == "NORMAL", "control: the naive rule does suppress"
    assert correct == "WARNING", "the rule must survive an offline corroborator"


def test_check_monotone_catches_a_veto():
    def vetoing_rule(inp):
        if not inp.get("rain"):
            return "NORMAL"                       # rain acts as a veto — the bug
        return inp.get("stage") or "NORMAL"

    try:
        check_monotone(vetoing_rule, {"stage": "WARNING", "rain": "WATCH"},
                       name="vetoing_rule")
    except PostureRuleViolation:
        return
    raise AssertionError("check_monotone failed to catch a veto rule")


def test_check_monotone_passes_a_correct_rule():
    """A plain max over both inputs is safe: rain is corroborating, and dropping it
    cannot lower the result because stage already dominates."""
    def safe_rule(inp):
        return combine(inp.get("stage"), inp.get("rain"))
    check_monotone(safe_rule, {"stage": "WARNING", "rain": "WATCH"},
                   primary=("stage",), name="safe_rule")


def test_check_monotone_respects_primary_declaration():
    """Losing the SOLE primary evidence may lower the posture — that is correct, and
    must not be reported as a veto."""
    def stage_only(inp):
        return combine(inp.get("stage"))
    check_monotone(stage_only, {"stage": "WARNING"}, primary=("stage",),
                   name="stage_only")
    try:
        check_monotone(stage_only, {"stage": "WARNING"}, name="stage_only_undeclared")
    except PostureRuleViolation:
        return
    raise AssertionError("undeclared primary should have been flagged")


# =====================================================================
# APPLIED TO THE DEPLOYED ENGINE
# The tests above prove the RULE is sound. These prove an ENGINE obeys it.
# =====================================================================
def test_the_deployed_level_survives_losing_its_corroborators():
    """flood_engine.assess() is what feed_runner publishes and what the phone push
    reads. Point check_monotone at it directly.

    `stage` is declared primary — losing the stage series legitimately lowers the
    posture, because that is the evidence itself. Soil and rain are corroborators, and
    a corroborator that can lower the result is a veto. This passes, and it should:
    classify_stage() takes only stage and prev_level, so the corroborators cannot reach
    the level at all. That is worth ASSERTING rather than assuming — it is one edit
    away from not being true.
    """
    import flood_engine

    def deployed_level(inp):
        stage = inp["stage"]
        series = [(0, stage - 0.5), (900, stage - 0.25), (1800, stage)]
        return flood_engine.assess(series, prev_level="NORMAL",
                                   soil_moisture_pct=inp.get("soil"),
                                   storm_rain_in=inp.get("rain")).level

    for stage in (5.0, 6.9, 7.1, 9.5, 11.5):
        check_monotone(deployed_level,
                       {"stage": stage, "soil": 88.0, "rain": 2.4},
                       primary=("stage",),
                       name=f"flood_engine.assess at {stage} ft")


def test_the_deployed_engine_refuses_to_score_what_it_cannot_measure():
    """An absent leading indicator must produce NO probability — never a lower one.

    THE BUG THIS GUARDS, which was live until 2026-08-16: `(soil_pct or 0.0)` and
    `(storm_rain_in or 0.0)` mapped None to the most reassuring value each term can
    take. On a creek at 6.5 ft rising 0.5 ft/hr with saturated ground and 2 inches
    down, losing both sensors moved the published FX_WARN_PROBABILITY from 97.0% to
    55.1% — the storm that kills a gauge making the system look calmer, which is the
    FloodNet failure this whole module exists to prevent.
    """
    import flood_engine
    series = [(0, 6.0), (900, 6.25), (1800, 6.5)]

    full = flood_engine.assess(series, soil_moisture_pct=85.0, storm_rain_in=2.0)
    assert full.ew_probability is not None, "both sensors present must yield a number"

    for label, kw in (
            ("soil probe offline", dict(soil_moisture_pct=None, storm_rain_in=2.0)),
            ("rain gauge offline", dict(soil_moisture_pct=85.0, storm_rain_in=None)),
            ("both offline", dict(soil_moisture_pct=None, storm_rain_in=None))):
        a = flood_engine.assess(series, **kw)
        assert a.ew_probability is None, (
            f"{label}: published P(warn)={a.ew_probability} instead of null. A blend "
            f"calibrated on four terms is not the same quantity with one pinned at "
            f"zero, and a low number is believed where a null is handled.")
        assert a.level == full.level, (
            f"{label}: the LEVEL moved {full.level} -> {a.level} when a corroborating "
            f"sensor went offline. That is the veto bug in the deployed engine.")


def test_absent_soil_gives_the_neutral_curve_number_not_the_dry_one():
    """dynamic_cn() already had this right, in the same file as the bug above — absent
    soil returns AMC-II, not the dry AMC-I. Pin it so it cannot drift into its
    sibling's mistake."""
    import flood_engine
    assert flood_engine.dynamic_cn(None) == flood_engine.CN_NORMAL, (
        "absent soil moisture must read as the NEUTRAL curve number")
    assert flood_engine.dynamic_cn(0.0) < flood_engine.CN_NORMAL, (
        "control: an actual reading of 0% really is drier than neutral")


# =====================================================================
# MONOTONICITY IN THE HAZARD ITSELF
# The rule above is about ABSENT evidence. This is its twin for PRESENT
# evidence: more hazard must never produce less warning. Nothing asserted
# it before 2026-08-16, on any engine, in any variable.
# =====================================================================
_POST_ORDER = ["N/A", "NORMAL", "WATCH", "WARNING", "EMERGENCY"]
_PR = {k: i for i, k in enumerate(_POST_ORDER)}


def _no_reversal(fn, xs, label):
    """fn(x) -> posture, over an ascending sweep. Report the FIRST reversal with
    both sides of it, because a bare 'not monotone' is not actionable.

    'N/A' ranks BELOW 'NORMAL' deliberately: losing the ability to assess as the
    hazard rises is a defect of the same kind as downgrading, not an exemption.
    """
    prev = None
    for x in xs:
        got = fn(x)
        if prev is not None and _PR.get(got, 0) < _PR.get(prev[1], 0):
            raise AssertionError(
                f"{label}: {prev[0]:g} -> {prev[1]}, but {x:g} -> {got}. "
                f"More hazard produced LESS warning.")
        prev = (x, got)


def _geom(lo, hi, mult):
    x = lo
    while x < hi:
        yield x
        x *= mult


def _lin(lo, hi, step):
    x, n = lo, 0
    while x <= hi + 1e-9:
        yield x
        n += 1
        x = lo + n * step


def test_the_authoritative_posture_never_falls_as_discharge_rises():
    """flood_rating.assess() is the authoritative engine. Its posture comes from a
    return period, which comes from a regression, which carries a 90% PI band — three
    transforms, each a place a fold could hide. Swept 1 -> 60,000 cfs on all eight
    reaches.

    check_monotone() cannot be used here: assess() takes only a discharge and a basin
    id, so it has no CORROBORATING input to drop. That is worth stating rather than
    leaving as a silent gap — the FloodNet veto bug is impossible in this engine by
    construction, and this is the invariant that IS at risk instead.
    """
    import flood_rating, basins
    for bid in basins.BASINS:
        _no_reversal(lambda q, b=bid: flood_rating.assess(q, b)["posture"],
                     _geom(1.0, 60000.0, 1.02), f"flood_rating {bid} @ Q")


def test_the_posture_never_falls_as_rainfall_rises():
    """The end-to-end chain operators actually read: rain -> CN -> runoff -> unit
    hydrograph -> per-basin calibration -> return period -> posture. Swept 0.1 -> 12.0
    in at 0.05 in on all eight reaches, wetness held at the CN2 anchor."""
    import cwm_model, basins
    for bid in basins.BASINS:
        _no_reversal(lambda P, b=bid: cwm_model.assess(b, P, 0.5)["posture"],
                     _lin(0.1, 12.0, 0.05), f"cwm_model {bid} @ P (w=0.5)")


def test_the_posture_never_falls_as_the_ground_gets_wetter():
    """Wetness is the other monotone input, and a riskier one: cn_from_wetness is
    PIECEWISE, anchored at CN2 at w=0.5 with a different slope either side. A sign
    error or a mis-joined segment would show up here and nowhere else. Swept 0 -> 1 at
    0.005 on all eight reaches at a 5 in storm."""
    import cwm_model, basins
    for bid in basins.BASINS:
        _no_reversal(lambda w, b=bid: cwm_model.assess(b, 5.0, w)["posture"],
                     _lin(0.0, 1.0, 0.005), f"cwm_model {bid} @ wetness (P=5.0)")


def test_a_probability_that_could_not_be_computed_does_not_crash_its_consumers():
    """The absent-probability fix has a downstream half, and it was missed on the day.

    flood_engine.early_warning_probability began returning None on 2026-08-16 (correct:
    a blend calibrated on four terms is not the same quantity with two pinned at zero).
    But flood_network._noisy_or multiplied straight through `probs`, and
    streamlit_app.py builds its site inputs with `stage_series` ONLY — no soil_pct, no
    storm_rain_in. So every real Firestore stage series produced ew_probability=None and
    routed_assessment() raised TypeError.

    Nothing caught it: the flood_engine tests call assess() directly and never through a
    consumer. It was latent only because no sensor reports yet — it would have fired on
    the day the watershed got its first gauge, which is the day it is supposed to start
    working.

    Two assertions, because "does not crash" is not enough:
      * the call survives, and
      * the site still CONFIRMS. Losing an uncomputable probability must not cost the
        measured level, which is what actually drives the posture.
    """
    import flood_network
    series = [(i * 300, 4.0 + 0.5 * i) for i in range(8)]     # rising, plausible
    rw = flood_network.routed_assessment(
        "belk", {"double_springs": {"stage_series": series}})   # exactly line 384's shape
    tp = flood_network.tiered_posture(rw, "belk")

    assert rw.combined_probability is not None
    assert tp.stream_confirmed is True, (
        "a site with a measured stage series stopped confirming — the absent PROBABILITY "
        "cost us the measured LEVEL, which is the half that drives postures.")
    assert flood_network._noisy_or([None, None]) == 0.0
    assert flood_network._noisy_or([0.5, None]) == flood_network._noisy_or([0.5]), (
        "an absent probability must contribute nothing to the combine, exactly as an "
        "absent level contributes nothing to combine().")


def test_forecast_evidence_alone_can_never_exceed_watch():
    """The two-tier rule, asserted on the module that implements it.

    flood_network's header: forecast/soil may raise an OUTLOOK to WATCH; WARNING and
    EMERGENCY require CONFIRMATION from a stream level. This is the single most
    consequential rule in the system — it is what stops a model from issuing an
    EMERGENCY — and nothing asserted it until 2026-08-16.

    It holds by construction: outlook_level is a binary WATCH/NORMAL, so the outlook
    tier has no representation for anything higher. Worth pinning precisely BECAUSE it
    is structural — a later refactor that gave outlook_level a real ladder would break
    it silently, and the failure mode is a forecast issuing an EMERGENCY.

    What this does NOT assert, because it is not true: that a confirming level was
    MEASURED. flood_network never imports sources.py and cannot tell. See its header.
    """
    import flood_network
    rw = flood_network.routed_assessment("belk", {})        # no stream input anywhere
    for i in range(0, 301):                                 # risk 0.00 .. 3.00
        risk = i / 100.0
        tp = flood_network.tiered_posture(rw, "belk", upwind={
            "risk": risk, "level": "EMERGENCY", "lead_min": 30,
            "contributors": [], "note": ""})
        assert not tp.stream_confirmed, "no stream input, yet the tier confirmed"
        assert _PR[tp.headline] <= _PR["WATCH"], (
            f"outlook risk {risk:g} produced headline {tp.headline} with NO stream "
            f"confirmation. Forecast evidence has escaped the WATCH ceiling — a model "
            f"can now issue a warning the creek has not earned.")


def test_a_stream_level_is_what_lifts_the_ceiling():
    """Control for the test above: prove the ceiling is real, not vacuous.

    If tiered_posture could never exceed WATCH under ANY input, the test above would
    pass while asserting nothing. A stream level must actually get through.
    """
    import flood_network
    series = [(i * 300, 4.0 + 1.4 * i) for i in range(8)]    # a hard, sustained rise
    rw = flood_network.routed_assessment(
        "belk", {"double_springs": {"stage_series": series}})
    tp = flood_network.tiered_posture(rw, "belk")
    assert tp.stream_confirmed, "a stage series must confirm"
    assert _PR[tp.stream_level] >= _PR["WATCH"], (
        f"a 4.0 -> 13.8 ft rise produced stream_level {tp.stream_level}; the ceiling "
        f"test above is passing vacuously because nothing can lift it.")


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            fails += 1
            print(f"FAIL  {name}\n      {e}")
    print(f"\n{fails} failure(s)")
    raise SystemExit(1 if fails else 0)
