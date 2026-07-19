"""
test_flood_network_upwind.py — proves the upwind-outlook wiring in
flood_network.tiered_posture:
  1. measured upwind rain raises an Outlook WATCH with ZERO headwater sensors,
  2. it is capped at WATCH and never overrides a measured stream WARNING,
  3. below-threshold upwind rain does nothing,
  4. omitting `upwind` reproduces the original behavior.

Runs against the real flood_network in the repo (needs flood_engine present).

Run:  python test_flood_network_upwind.py
"""

import flood_network as fn

FAILS = []


def check(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILS.append(name)


def _bare_rw():
    """A RoutedWarning with nothing deployed: no campus gauge, no upstream inputs."""
    return fn.RoutedWarning("belk", local=None, upstream=[],
                            combined_probability=0.0, lead_time_hr=None, note="")


def _rw_with_stream(level="WARNING"):
    """A RoutedWarning carrying a MEASURED upstream stream level."""
    c = fn.UpstreamContribution(site_id="double_springs", name="Double Springs",
                                eta_hr=1.3, level=level, ew_prob=0.8)
    return fn.RoutedWarning("belk", local=None, upstream=[c],
                            combined_probability=0.8, lead_time_hr=1.3, note="")


UPWIND_WATCH = {
    "risk": 0.62, "level": "WATCH", "lead_min": 48,
    "contributors": [{"area": "Raingage at Franklin", "dir": "SW", "h1": 0.9,
                      "h3": 1.9, "score": 0.95, "upwind": True, "eta_min": 48}],
    "note": "measured rain approaching from Franklin",
}
UPWIND_QUIET = {"risk": 0.10, "level": "NORMAL", "lead_min": None,
                "contributors": [], "note": "below threshold"}


def test_upwind_raises_watch_with_no_sensors():
    print("upwind raises WATCH with zero headwater sensors")
    tp = fn.tiered_posture(_bare_rw(), "belk", upwind=UPWIND_WATCH)
    check("headline WATCH", tp.headline == "WATCH")
    check("driver = outlook", tp.driver == "outlook")
    check("outlook level WATCH", tp.outlook_level == "WATCH")
    check("stream still NORMAL (unconfirmed)", tp.stream_level == "NORMAL")
    check("upwind lead surfaced", tp.upwind_lead_min == 48)
    check("headline mentions approach/measured",
          "approach" in tp.headline_statement.lower()
          or "measured" in tp.outlook_note.lower())


def test_upwind_capped_below_stream_warning():
    print("upwind never overrides a measured stream WARNING")
    tp = fn.tiered_posture(_rw_with_stream("WARNING"), "belk", upwind=UPWIND_WATCH)
    check("headline WARNING (stream wins)", tp.headline == "WARNING")
    check("driver = stream", tp.driver == "stream")
    check("outlook still capped at WATCH", tp.outlook_level == "WATCH")


def test_quiet_upwind_does_nothing():
    print("below-threshold upwind rain does not trip WATCH")
    tp = fn.tiered_posture(_bare_rw(), "belk", upwind=UPWIND_QUIET)
    check("headline NORMAL", tp.headline == "NORMAL")
    check("driver none", tp.driver == "none")


def test_no_upwind_arg_is_backward_compatible():
    print("omitting upwind reproduces original behavior")
    tp = fn.tiered_posture(_bare_rw(), "belk")
    check("bare + no upwind -> NORMAL", tp.headline == "NORMAL")
    check("upwind_lead_min stays None", tp.upwind_lead_min is None)


if __name__ == "__main__":
    for t in (test_upwind_raises_watch_with_no_sensors,
              test_upwind_capped_below_stream_warning,
              test_quiet_upwind_does_nothing,
              test_no_upwind_arg_is_backward_compatible):
        t()
    print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
    raise SystemExit(1 if FAILS else 0)
