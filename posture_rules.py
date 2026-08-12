"""
posture_rules.py — the monotonicity rules that govern how evidence changes a posture.

ONE RULE, stated once:

    Absent corroboration must never downgrade a posture. It may only fail to upgrade.

This is not a style preference. FloodNet (87 nodes, 3 years, NYC) documented the exact
trap in production: a rule requiring recent precipitation to confirm a stage rise "can
incorrectly suppress actual flood onset during rain gaps", and sparse data from poor
connectivity bypassed their filters entirely. The conditions that knock out a rain-gauge
link — an intense storm, a downed relay, a saturated backhaul — are the conditions that
produce the flood. Any rule of the form "raise the level only if X ALSO agrees" hands X
a veto, and X is offline precisely when it matters.

So: an input may RAISE a level. An input may NOT lower one, and its ABSENCE may not
block a raise justified by other evidence.

Corollary for the source ladder: `sources.resolve()` falling back from a stale sensor to
a MODELED value is legitimate — but a MODELED value must never populate the Confirmation
tier, because the tier\'s whole claim is "this was measured".

Usage:

    from posture_rules import combine, escalate, corroborate, ORDER

    level = combine(outlook_level, stream_level)        # monotone max, None ignored
    level = escalate(level, "WARNING", evidence_present=rain_gauge_reporting)
    level = corroborate(level, corroborated=rain_seen, available=rain_gauge_reporting)

Stdlib only. No side effects. Every function is total: given any input it returns a
valid level, and never one lower than it was handed.
"""

ORDER = ["NORMAL", "WATCH", "WARNING", "EMERGENCY"]
_IDX = {lv: i for i, lv in enumerate(ORDER)}


class PostureRuleViolation(AssertionError):
    """Raised only by check_monotone(), which is a test/audit helper. Operational code
    uses the combining functions, which cannot violate the rule by construction."""


def _i(level):
    """Index of a level. None / unknown / "N/A" sort as NORMAL — i.e. contributes
    nothing — never as severe, and never as a reduction."""
    if level is None:
        return 0
    return _IDX.get(str(level).upper(), 0)


def normalise(level):
    return ORDER[_i(level)]


def combine(*levels):
    """Monotone maximum over whatever evidence is present.

    None, "N/A", unknown strings and missing arguments all contribute NOTHING. They
    cannot pull the result down, because the result is a max and their contribution
    floors at NORMAL. This is the safe direction: an offline sensor is silent, not
    reassuring.
    """
    return ORDER[max([_i(l) for l in levels], default=0)]


def escalate(current, candidate, *, evidence_present):
    """Raise `current` toward `candidate` if the evidence for it is actually present.

    evidence_present=False means we could not look — NOT that we looked and saw
    nothing. It returns `current` unchanged. It never returns something lower.
    """
    cur = normalise(current)
    if not evidence_present:
        return cur
    return combine(cur, candidate)


def corroborate(level, *, corroborated, available):
    """Apply a corroborating (confirming) input, which may only ever CONFIRM.

    available=False   -> the corroborating source is offline. Pass through unchanged.
                         This is the FloodNet trap and the reason this module exists.
    corroborated=False-> we could look and it did not agree. Still pass through: a
                         disagreeing secondary source lowers CONFIDENCE, not LEVEL.
                         Report the disagreement to the operator instead.

    So this function is deliberately identity-on-level. It exists to make the rule
    impossible to violate at a call site while keeping the call site readable, and to
    give the disagreement somewhere to be recorded.
    """
    return normalise(level)


def confidence_note(*, corroborated, available, source_name="corroborating source"):
    """The text that carries what `corroborate()` refuses to encode in the level."""
    if not available:
        return (f"{source_name} unavailable — level unchanged. Absence of corroboration "
                f"is not evidence against the hazard.")
    if not corroborated:
        return (f"{source_name} does not agree — level unchanged, confidence reduced. "
                f"Two sources disagreeing is a fault to investigate, not a downgrade.")
    return f"{source_name} agrees."


def check_monotone(fn, inputs, *, primary=(), name="rule"):
    """Audit helper: dropping a CORROBORATING input must never lower the result.

    `fn` takes a dict of inputs and returns a level. `inputs` is that dict.
    `primary` names the inputs that carry evidence in their own right — losing one of
    those may legitimately lower the posture, because the evidence itself is gone.
    Every input NOT named primary is treated as a corroborator, and a corroborator that
    can lower the result is a veto.

    The distinction is the whole point. "Requiring rain to confirm a stage rise" makes
    rain a corroborator with veto power, and the storm that causes the flood is what
    takes the rain gauge offline. Naming your primaries forces you to state which
    inputs you are actually willing to be blind to.
    """
    primary = set(primary)
    base = _i(fn(inputs))
    for k in inputs:
        if k in primary:
            continue                       # losing real evidence may lower it: correct
        reduced = dict(inputs)
        reduced[k] = None
        got = _i(fn(reduced))
        if got < base:
            raise PostureRuleViolation(
                f"{name}: dropping corroborating input {k!r} lowered the posture "
                f"{ORDER[base]} -> {ORDER[got]}. This is the FloodNet veto bug: the "
                f"source that would confirm the hazard is offline BECAUSE of the "
                f"hazard. If {k!r} really is primary evidence, pass it in primary=()."
            )
    return True
