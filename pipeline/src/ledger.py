"""The processed-epoch ledger: pipeline/state/epochs.json.

This is the file that makes the daily cron cheap. Sentinel-1 revisits the
watershed about every 12 days, so 11 runs out of 12 have nothing to do; the
ledger is what lets those runs answer "nothing new" from an ASF search alone
(free, no login) and exit in seconds.

It records, per scene, only what the workflow needs to decide that:
  * the scene names and acquisition dates already folded into the pair cache,
  * the pairs already cached (so a resumed bootstrap does not re-download),
  * the pairs whose HyP3 jobs failed, so they are not retried forever.

Committed to the repo — it is the pipeline's memory across runs.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT

STATE_DIR = ROOT / "state"
LEDGER_PATH = STATE_DIR / "epochs.json"

SCHEMA = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def empty() -> dict:
    return {
        "schema": SCHEMA,
        "updated_utc": None,
        "aoi_bbox": None,
        "scenes": [],       # [{sceneName, startTime, date, pathNumber}]
        "pairs": [],        # [{ref, sec, d0, d1, status, note}]
        "runs": [],         # short audit trail, newest last (capped)
    }


def load() -> dict:
    if not LEDGER_PATH.exists():
        return empty()
    led = json.loads(LEDGER_PATH.read_text())
    for k, v in empty().items():
        led.setdefault(k, v)
    return led


def save(led: dict) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    led["schema"] = SCHEMA
    led["updated_utc"] = _now()
    led["runs"] = led.get("runs", [])[-40:]
    LEDGER_PATH.write_text(json.dumps(led, indent=2) + "\n")
    return LEDGER_PATH


# --------------------------------------------------------------------------
# scenes
# --------------------------------------------------------------------------

def known_scene_names(led: dict) -> set[str]:
    return {s["sceneName"] for s in led.get("scenes", [])}


def known_epochs(led: dict) -> set[str]:
    return {s["date"] for s in led.get("scenes", [])}


def scene_record(result) -> dict:
    """Normalise one asf_search result into a ledger row."""
    p = result.properties
    return {
        "sceneName": p["sceneName"],
        "startTime": p["startTime"],
        "date": p["startTime"][:10],
        "pathNumber": p.get("pathNumber"),
    }


def new_scenes(led: dict, results) -> list:
    """asf_search results whose scenes are not yet in the ledger."""
    known = known_scene_names(led)
    return [r for r in results if r.properties["sceneName"] not in known]


def add_scenes(led: dict, records: list[dict]) -> None:
    known = known_scene_names(led)
    for rec in records:
        if rec["sceneName"] not in known:
            led["scenes"].append(rec)
            known.add(rec["sceneName"])
    led["scenes"].sort(key=lambda s: s["startTime"])


# --------------------------------------------------------------------------
# pairs
# --------------------------------------------------------------------------

def pair_key(ref_name: str, sec_name: str) -> str:
    return f"{ref_name}::{sec_name}"


def pair_status(led: dict) -> dict[str, str]:
    return {pair_key(p["ref"], p["sec"]): p.get("status", "pending")
            for p in led.get("pairs", [])}


def set_pair(led: dict, ref_name: str, sec_name: str, d0: str, d1: str,
             status: str, note: str = "") -> None:
    key = pair_key(ref_name, sec_name)
    for p in led["pairs"]:
        if pair_key(p["ref"], p["sec"]) == key:
            p["status"] = status
            p["d0"], p["d1"] = d0, d1
            if note:
                p["note"] = note
            return
    row = {"ref": ref_name, "sec": sec_name, "d0": d0, "d1": d1, "status": status}
    if note:
        row["note"] = note
    led["pairs"].append(row)


def cached_pairs(led: dict) -> set[str]:
    return {pair_key(p["ref"], p["sec"])
            for p in led.get("pairs", []) if p.get("status") == "cached"}


def failed_pairs(led: dict) -> set[str]:
    return {pair_key(p["ref"], p["sec"])
            for p in led.get("pairs", []) if p.get("status") == "failed"}


def record_run(led: dict, kind: str, summary: str) -> None:
    led.setdefault("runs", []).append(
        {"utc": _now(), "kind": kind, "summary": summary}
    )
