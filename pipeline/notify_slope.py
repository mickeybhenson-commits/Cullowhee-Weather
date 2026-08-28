"""Push a phone notification when a slope pass lands at WATCH or higher.

Mirrors notify_posture.py's conventions — ntfy.sh, Title/Priority/Tags/Click
headers, NTFY_TOPIC as the secret, NTFY_DRY=1 to print instead of POST — and
its hard-won contract: this module NEVER raises. A notification outage must
not fail the workflow that just published a correct page.

Unlike the flood notifier this one is not edge-triggered. Sentinel-1 comes
back every ~12 days, so "WATCH again this pass" is new information, not noise,
and there is no 30-minute cycle to spam.

    NTFY_TOPIC=... python pipeline/notify_slope.py
    NTFY_DRY=1     python pipeline/notify_slope.py     # print, send nothing
    python pipeline/notify_slope.py --selftest
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

STATE = Path(__file__).resolve().parent / "state" / "combined_bulletin.json"
LAST_PASS = Path(__file__).resolve().parent / "state" / "last_pass.json"
SITE_URL = "https://mickeybhenson-commits.github.io/Cullowhee-Weather/slope_monitor.html"

PRIORITY = {"WARNING": "5", "WATCH": "4"}
TAGS = {"WARNING": "warning", "WATCH": "eyes"}
NOTIFY_AT = ("WATCH", "WARNING")


def _post(topic: str, title: str, body: str, priority: str, tags: str) -> bool:
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


def message(combined: dict, last_pass: dict | None) -> tuple[str, str]:
    level = combined.get("system_alert_level", "NORMAL")
    meta = (last_pass or {}).get("meta", {})
    clusters = (last_pass or {}).get("clusters", combined.get("clusters", []))
    cands = [c for c in clusters
             if c.get("screening", {}).get("verdict") == "candidate"]

    title = f"Slope monitor: {level}"
    lines = [f"Cullowhee Creek slope layer is {level} after the "
             f"{meta.get('last', 'latest')} Sentinel-1 pass."]
    if cands:
        for c in cands[:3]:
            lines.append(
                f"• cluster {c['cluster_id']} in {c.get('basin_name', '?')}: "
                f"{c['screening']['net_mm']:+.0f} mm net, "
                f"{c.get('mean_los_velocity_mm_yr', 0):+.0f} mm/yr")
    else:
        lines.append("No cluster cleared the candidate screening bar.")
    hydro = combined.get("hydro_conditions") or {}
    if hydro.get("state"):
        lines.append(f"Hydro: {hydro['state']}.")
    for e in combined.get("escalations", [])[:2]:
        lines.append(f"! {e}")
    lines.append("Automated screening — pending analyst review.")
    return title, "\n".join(lines)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--selftest" in argv:
        return 0 if selftest() else 1

    try:
        if not STATE.exists():
            print(f"notify: no {STATE.name} — nothing to send")
            return 0
        combined = json.loads(STATE.read_text())
        last_pass = json.loads(LAST_PASS.read_text()) if LAST_PASS.exists() else None
        level = combined.get("system_alert_level", "NORMAL")
        if level not in NOTIFY_AT:
            print(f"notify: level {level} is below WATCH — no push")
            return 0

        topic = os.getenv("NTFY_TOPIC")
        if not topic and not os.getenv("NTFY_DRY"):
            print("notify: NTFY_TOPIC not set — notifier idle (set the repo "
                  "secret to enable phone pushes)")
            return 0

        title, body = message(combined, last_pass)
        ok = _post(topic or "dry", title, body,
                   PRIORITY.get(level, "4"), TAGS.get(level, "eyes"))
        print("notify: sent" if ok else "notify: delivery failed (logged, not fatal)")
    except Exception as e:                            # noqa: BLE001
        # Contract: a notifier fault never fails a run that published correctly.
        print(f"notify: skipped ({type(e).__name__}: {e})")
    return 0


def selftest() -> bool:
    combined = {
        "system_alert_level": "WATCH",
        "hydro_conditions": {"state": "PRIMED"},
        "escalations": ["cluster 3: ADVISORY -> WATCH (hydro PRIMED)"],
        "clusters": [],
    }
    last = {"meta": {"last": "2026-09-06"},
            "clusters": [{"cluster_id": 3, "basin_name": "Cox Branch",
                          "mean_los_velocity_mm_yr": 51.0,
                          "screening": {"verdict": "candidate", "net_mm": 44.0}}]}
    title, body = message(combined, last)
    ok = True
    for want in ("WATCH", "cluster 3", "Cox Branch", "+44 mm",
                 "pending analyst review"):
        good = want in title + body
        ok &= good
        print(f"  [{'PASS' if good else 'FAIL'}] message mentions {want!r}")
    quiet = message({"system_alert_level": "NORMAL", "clusters": []}, None)
    good = "No cluster cleared" in quiet[1]
    ok &= good
    print(f"  [{'PASS' if good else 'FAIL'}] a clean pass says so plainly")
    print("notify_slope selftest", "PASSED" if ok else "FAILED")
    return bool(ok)


if __name__ == "__main__":
    raise SystemExit(main())
