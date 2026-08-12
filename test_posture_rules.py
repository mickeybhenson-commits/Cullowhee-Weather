"""
test_posture_rules.py — exhaustive proof that absent evidence cannot lower a posture.

These are not example-based tests. Every function is checked over the FULL cross product
of levels and presence flags, because the failure mode being guarded against is exactly
the corner nobody thought to write an example for.

    python test_posture_rules.py          # no pytest needed
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
