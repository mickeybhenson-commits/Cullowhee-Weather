"""
notify_posture.py — push a phone notification when the system's posture moves.
==============================================================================
Until now the feed was pull-only: someone had to be LOOKING at the map. This
closes that gap. Runs in the publish-feed Action after the feed is written;
compares the freshly published state against the last notified state and
POSTs to ntfy.sh (free pub/sub push; subscribe to your topic in the ntfy
app on any phone — no account, no API key, the topic name IS the secret).

WHAT TRIGGERS A PUSH
  1. OPERATIVE posture change (feed/state.json `level`, the stage-chain
     posture with hysteresis already applied by flood_engine):
       escalation    -> priority urgent (WARNING/EMERGENCY) or high (WATCH)
       de-escalation -> priority low (worth knowing, not worth waking you)
  2. OUTLOOK heads-up (feed/outlook.json, WeatherNext ensemble): campus
     P(>=WATCH) crosses OUTLOOK_P_THRESHOLD upward. Forecast evidence only,
     so it pushes at default priority with explicit "forecast tier" wording,
     and a cooldown stops it re-firing every 30-min cycle while a wet
     pattern persists. Silent until WeatherNext access is live.

WHAT NEVER TRIGGERS A PUSH
  A no-change cycle, or any exception in here — this module follows
  fiman_watch's contract: it NEVER raises, so it cannot take down the publish
  job.

  3. BLINDNESS (added 2026-08-15). The line above used to read "a stale or
     unavailable source (that is feed_meta's job to surface)". feed_meta does
     surface it — into feed_meta.json, which was read by nothing. So when every
     source went stale the posture stopped changing, trigger 1 stopped firing,
     and the system went quiet. Silence is what this notifier emits when all is
     well, so a dead watershed and a calm one looked identical on the phone.

     This fires when the feed can no longer see the creek, and again when sight
     returns. The wording is deliberately not flood wording: being blind is not
     a warning and is not an all-clear, and a push that blurred the two would be
     worse than no push.

     LIMIT, stated plainly: this runs inside publish-feed. It catches dead
     SOURCES. It cannot catch a dead WORKFLOW — on 2026-08-14 the job itself
     failed four consecutive times and any check living inside it died with it.
     A dead-man's switch cannot live inside the thing it monitors; that needs an
     external heartbeat. See noah_blind_notifier_2026-08-15.md.

STATE
  feed/notify_state.json — last notified level + last outlook alert time,
  committed with the feed like history.json/state.json (same stateless-cron
  reasoning as feed_runner's header).

SETUP (one time)
  1. Pick a hard-to-guess topic, e.g. noah-cullowhee-8k3v2 (anyone who knows
     the topic can subscribe — treat it like a password, don't publish it).
  2. Phone: install "ntfy" (iOS/Android), subscribe to that topic.
  3. GitHub: repo Settings -> Secrets -> Actions -> new secret NTFY_TOPIC.
  The workflow step passes it as env; unset topic = notifier no-ops with a
  log line, so the repo works before setup exactly as it did.

Env knobs: NTFY_TOPIC (required to send), NTFY_SERVER (default ntfy.sh),
NTFY_DRY=1 (print instead of POST — used by the self-test and safe for
rehearsals), OUTLOOK_P_THRESHOLD (default 0.4), OUTLOOK_COOLDOWN_HR (12).
Run `python notify_posture.py --selftest` for the offline self-test.
Standard library only.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

FEED = Path("feed")
STATE_IN = FEED / "state.json"
OUTLOOK_IN = FEED / "outlook.json"
META_IN = FEED / "feed_meta.json"
NOTIFY_STATE = FEED / "notify_state.json"

ORDER = ["NORMAL", "WATCH", "WARNING", "EMERGENCY"]
PRIORITY = {  # ntfy priorities: 5 urgent .. 1 min
    "EMERGENCY": "5", "WARNING": "5", "WATCH": "4", "NORMAL": "2"}
TAGS = {"EMERGENCY": "rotating_light", "WARNING": "warning",
        "WATCH": "eyes", "NORMAL": "white_check_mark"}
SITE_URL = "https://mickeybhenson-commits.github.io/Cullowhee-Weather/live.html"


def _rank(lvl):
    return ORDER.index(lvl) if lvl in ORDER else -1


def _load(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _post(topic, title, body, priority, tags):
    """One ntfy publish. Failure is logged, never raised — a notification
    outage must not look like a feed outage."""
    if os.getenv("NTFY_DRY"):
        print(f"[dry] ntfy p{priority} [{tags}] {title} :: {body}")
        return True
    server = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    req = urllib.request.Request(
        f"{server}/{topic}", data=body.encode(),
        headers={"Title": title, "Priority": priority, "Tags": tags,
                 "Click": SITE_URL})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            r.read()
        return True
    except Exception as e:                            # noqa: BLE001
        print(f"notify: ntfy POST failed ({type(e).__name__}: {e})")
        return False


def check_operative(state, prev, topic):
    """Stage-chain posture transitions. Returns the level actually notified
    (for state), or None if nothing was sent."""
    lvl = (state or {}).get("level")
    last = (prev or {}).get("level")
    if lvl not in ORDER or lvl == last:
        return None
    if last not in ORDER:
        # First run ever: establish the baseline SILENTLY unless the system
        # is already elevated — "stood down to NORMAL" from no history is
        # noise, but waking up straight into WATCH+ deserves a push.
        if _rank(lvl) == 0:
            return lvl
        last = "baseline"
        up = True
    else:
        up = _rank(lvl) > _rank(last)
    if up:
        title = f"Cullowhee Creek: {lvl}"
        body = (f"Posture escalated {last or '?'} -> {lvl} "
                f"(source: {state.get('source_tier', '?')}). "
                + ("Measured stage chain." if state.get("source_tier") == "measured"
                   else "Modeled — shadow mode; verify against gauges."))
        _post(topic, title, body, PRIORITY.get(lvl, "3"), TAGS.get(lvl, "warning"))
    else:
        _post(topic, f"Cullowhee Creek: stood down to {lvl}",
              f"Posture de-escalated {last} -> {lvl}.", "2",
              TAGS.get(lvl, "white_check_mark"))
    return lvl


def check_outlook(outlook, prev, topic, now):
    """WeatherNext ensemble heads-up: campus P(>=WATCH) crosses the threshold
    upward, with a cooldown. Returns new outlook-alert timestamp or None."""
    if not outlook or outlook.get("status") != "ok":
        return None
    c = (outlook.get("basins") or {}).get("CC-WCU-2260") or {}
    p = ((c.get("p_exceed") or {}).get("WATCH"))
    if p is None:
        return None
    thresh = float(os.getenv("OUTLOOK_P_THRESHOLD", "0.4"))
    cooldown_hr = float(os.getenv("OUTLOOK_COOLDOWN_HR", "12"))
    last_p = (prev or {}).get("outlook_p", 0.0) or 0.0
    last_ts = (prev or {}).get("outlook_alert_utc")
    if p < thresh or last_p >= thresh:
        return None                       # below, or already alerted this rise
    if last_ts:
        try:
            age_hr = (now - datetime.fromisoformat(last_ts)).total_seconds() / 3600
            if age_hr < cooldown_hr:
                return None
        except ValueError:
            pass
    q = c.get("qpf24_in") or {}
    when = c.get("worst24_start_utc") or "?"
    _post(topic, f"Outlook: {int(p * 100)}% chance of WATCH-level rain",
          (f"WeatherNext ensemble ({outlook.get('n_members', '?')} members): "
           f"P(WATCH) {int(p * 100)}%, worst 24 h QPF p50 {q.get('p50', '?')}\" "
           f"/ p90 {q.get('p90', '?')}\", arriving around {when}. "
           f"FORECAST TIER — heads-up only, not a warning."),
          "3", "crystal_ball")
    return now.isoformat()


def check_blind(meta, prev, topic, now):
    """Every site in the feed stale -> the system cannot see the creek.

    Returns (blind_now, alert_ts) where alert_ts is a new timestamp if a push
    was sent, else the previous one. Never raises.

    Fires on the transition into blindness and again on recovery, with a
    cooldown in between so a long outage does not push every 30 minutes. Both
    messages avoid posture vocabulary: a blind system is neither warning nor
    all-clear, and saying otherwise on a phone at 3am is the failure this exists
    to prevent.
    """
    try:
        n_sites = int(meta.get("site_count") or 0)
        stale = list(meta.get("stale_sites") or [])
        # blind = there are sites, and every one of them is stale. site_count 0
        # is a different fault (nothing published at all) and publish_feed's own
        # sanity step already refuses to publish an empty feed.
        blind = n_sites > 0 and len(stale) >= n_sites
        was = bool((prev or {}).get("blind"))
        last_ts = (prev or {}).get("blind_alert_utc")
        cooldown_hr = float(os.getenv("BLIND_COOLDOWN_HR", "6"))

        if blind and not was:
            _post(topic, "NOAH is blind — no live source",
                  (f"All {n_sites} site(s) in the feed are stale: "
                   f"{', '.join(stale)}. The system cannot see the creek.\n\n"
                   f"This is NOT a flood warning and NOT an all-clear. It means "
                   f"the posture on the map is not backed by current data. "
                   f"Feed generated {meta.get('generated_utc', '?')}."),
                  "4", "see_no_evil")
            return True, now.isoformat()

        if was and not blind:
            _post(topic, "NOAH can see again",
                  (f"A live source is reporting. {n_sites - len(stale)} of "
                   f"{n_sites} site(s) fresh. The map is backed by current data "
                   f"again."),
                  "2", "eyes")
            return False, now.isoformat()

        if blind and was and last_ts:                 # still blind: re-nag slowly
            try:
                age_hr = (now - datetime.fromisoformat(last_ts)).total_seconds() / 3600
            except ValueError:
                return True, last_ts
            if age_hr >= cooldown_hr:
                _post(topic, "NOAH still blind",
                      (f"Still no live source after {age_hr:.0f} h. "
                       f"{len(stale)} of {n_sites} site(s) stale. Not a warning, "
                       f"not an all-clear — the map is running without current data."),
                      "4", "see_no_evil")
                return True, now.isoformat()
        return blind, last_ts
    except Exception as e:                            # noqa: BLE001
        print(f"notify: blindness check failed ({type(e).__name__}: {e})")
        return bool((prev or {}).get("blind")), (prev or {}).get("blind_alert_utc")


def main():
    topic = os.getenv("NTFY_TOPIC")
    if not topic and not os.getenv("NTFY_DRY"):
        print("notify: NTFY_TOPIC not set — notifier idle (set the repo "
              "secret to enable pushes)")
        return 0
    topic = topic or "dry-run-topic"
    now = datetime.now(timezone.utc)
    state = _load(STATE_IN, {})
    outlook = _load(OUTLOOK_IN, {})
    meta = _load(META_IN, {})
    prev = _load(NOTIFY_STATE, {})

    notified_lvl = check_operative(state, prev, topic)
    outlook_ts = check_outlook(outlook, prev, topic, now)
    blind, blind_ts = check_blind(meta, prev, topic, now)

    cur_p = (((outlook.get("basins") or {}).get("CC-WCU-2260") or {})
             .get("p_exceed") or {}).get("WATCH") if outlook.get("status") == "ok" else None
    new_state = {
        "level": notified_lvl or prev.get("level") or state.get("level"),
        "outlook_p": cur_p if cur_p is not None else prev.get("outlook_p"),
        "outlook_alert_utc": outlook_ts or prev.get("outlook_alert_utc"),
        "blind": blind,
        "blind_alert_utc": blind_ts,
        "checked_utc": now.isoformat(),
    }
    try:
        NOTIFY_STATE.write_text(json.dumps(new_state, indent=2))
    except OSError as e:
        print(f"notify: could not persist state ({e})")
    sent = bool(notified_lvl or outlook_ts
                or blind != bool(prev.get("blind"))
                or (blind_ts and blind_ts != prev.get("blind_alert_utc")))
    print(f"notify: level={state.get('level')} "
          f"P(WATCH)={cur_p if cur_p is not None else 'n/a'} "
          f"blind={blind} "
          f"-> {'sent' if sent else 'no change, nothing sent'}")
    return 0


# ---------------------------------------------------------------------------
# self-test (offline: NTFY_DRY, temp feed dir)
# ---------------------------------------------------------------------------
def _selftest():
    import tempfile
    global FEED, STATE_IN, OUTLOOK_IN, META_IN, NOTIFY_STATE
    os.environ["NTFY_DRY"] = "1"
    FEED = Path(tempfile.mkdtemp())
    STATE_IN, OUTLOOK_IN = FEED / "state.json", FEED / "outlook.json"
    META_IN = FEED / "feed_meta.json"
    NOTIFY_STATE = FEED / "notify_state.json"

    def run(state, outlook=None, meta=None):
        STATE_IN.write_text(json.dumps(state))
        if outlook is not None:
            OUTLOOK_IN.write_text(json.dumps(outlook))
        if meta is not None:
            META_IN.write_text(json.dumps(meta))
        main()
        return _load(NOTIFY_STATE, {})

    def run_capture(*a, **kw):
        """Run and return (state, printed output) so a test can assert on what
        was actually pushed, not merely on the state that resulted."""
        import io
        from contextlib import redirect_stdout
        b = io.StringIO()
        with redirect_stdout(b):
            st = run(*a, **kw)
        return st, b.getvalue()

    print("-- first run, NORMAL: must not notify (no baseline jump)")
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        s = run({"level": "NORMAL", "source_tier": "modeled"})
    assert "[dry]" not in buf.getvalue(), "first NORMAL run must be silent"
    assert s["level"] == "NORMAL"

    print("-- NORMAL -> WATCH: escalation push")
    s = run({"level": "WATCH", "source_tier": "modeled"})
    assert s["level"] == "WATCH"

    print("-- WATCH again: silence")
    s = run({"level": "WATCH", "source_tier": "modeled"})

    print("-- WATCH -> EMERGENCY: urgent push")
    s = run({"level": "EMERGENCY", "source_tier": "measured"})
    assert s["level"] == "EMERGENCY"

    print("-- EMERGENCY -> NORMAL: stand-down push (low priority)")
    s = run({"level": "NORMAL", "source_tier": "measured"})

    print("-- outlook crosses 40%: heads-up push")
    ok_outlook = {"status": "ok", "n_members": 64, "basins": {"CC-WCU-2260": {
        "p_exceed": {"WATCH": 0.55}, "qpf24_in": {"p50": 3.5, "p90": 4.1},
        "worst24_start_utc": "2026-08-12T06:00:00Z"}}}
    s = run({"level": "NORMAL", "source_tier": "measured"}, ok_outlook)
    assert s["outlook_alert_utc"] is not None
    first_ts = s["outlook_alert_utc"]

    print("-- outlook still high next cycle: cooldown, silence")
    s = run({"level": "NORMAL", "source_tier": "measured"}, ok_outlook)
    assert s["outlook_alert_utc"] == first_ts

    print("-- unavailable outlook: silence")
    s = run({"level": "NORMAL", "source_tier": "measured"},
            {"status": "unavailable: x"})

    print("-- blindness: every site stale -> one push, and it is NOT flood wording")
    FRESH = {"site_count": 2, "stale_sites": [], "generated_utc": "2026-08-15T14:00:00Z"}
    BLIND = {"site_count": 2, "stale_sites": ["A", "B"], "generated_utc": "2026-08-15T14:00:00Z"}
    s2, out = run_capture({"level": "NORMAL", "source_tier": "measured"}, meta=FRESH)
    assert not s2.get("blind"), "fresh feed must not read as blind"
    s2, out = run_capture({"level": "NORMAL", "source_tier": "measured"}, meta=BLIND)
    assert s2["blind"] is True and s2["blind_alert_utc"], "going blind must push"
    assert "blind" in out.lower(), out
    low = out.lower()
    assert "not a flood warning" in low and "not an all-clear" in low, (
        "the blindness push must refuse both readings explicitly:\n" + out)
    for word in ("watch", "warning issued", "emergency"):
        assert f"title: {word}" not in low
    first = s2["blind_alert_utc"]

    print("-- still blind next cycle: cooldown, silence")
    s2, out = run_capture({"level": "NORMAL", "source_tier": "measured"}, meta=BLIND)
    assert s2["blind"] is True and s2["blind_alert_utc"] == first, "must not re-nag"
    assert "[dry]" not in out, "second blind cycle must be silent"

    print("-- sight returns: recovery push, and blind clears")
    s2, out = run_capture({"level": "NORMAL", "source_tier": "measured"}, meta=FRESH)
    assert s2["blind"] is False, "recovery must clear the blind flag"
    assert "see again" in out.lower(), out

    print("-- partial staleness is NOT blindness")
    PARTIAL = {"site_count": 2, "stale_sites": ["A"], "generated_utc": "x"}
    s2, out = run_capture({"level": "NORMAL", "source_tier": "measured"}, meta=PARTIAL)
    assert s2["blind"] is False and "[dry]" not in out

    print("-- malformed feed_meta must not raise and must not push")
    s2, out = run_capture({"level": "NORMAL", "source_tier": "measured"},
                          meta={"site_count": "banana", "stale_sites": None})
    assert s2["blind"] is False, "a broken meta must not be read as blindness"

    print("-- a blind system that also escalates still sends BOTH")
    run_capture({"level": "NORMAL", "source_tier": "measured"}, meta=FRESH)
    s2, out = run_capture({"level": "WARNING", "source_tier": "measured"}, meta=BLIND)
    assert s2["blind"] is True and s2["level"] == "WARNING", s2
    assert out.lower().count("[dry]") >= 2, "escalation and blindness are separate facts:\n" + out

    print("all notify_posture self-tests passed")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        sys.exit(main())
