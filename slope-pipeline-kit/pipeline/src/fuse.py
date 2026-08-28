"""Fusion layer: combine InSAR slope alerts with hydrologic conditions.

The satellite layer says WHERE slopes are losing strength (weeks-scale).
The hydro layer says WHEN conditions are ripe (hours-scale).
Fusion escalates accordingly:

  hydro PRIMED / PRIMED_SEVERE  -> every InSAR cluster bumps one level
                                   (ADVISORY->WATCH, WATCH->WARNING)
  hydro PRIMED_SEVERE           -> system floor WATCH even with no clusters
                                   (short-fuse debris-flow risk is watershed-wide)
  hydro ELEVATED                -> no bump, flagged in the bulletin
  hydro unavailable             -> InSAR bulletin passes through unchanged

Reads outputs/alert_bulletin.json (from run_operational or run_demo), writes
outputs/combined_bulletin.json.

    python -m src.fuse
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .alert import LEVEL_ORDER
from .config import Config


def bump(level: str) -> str:
    i = LEVEL_ORDER.index(level)
    return LEVEL_ORDER[min(i + 1, len(LEVEL_ORDER) - 1)]


def fuse(bulletin: dict, hydro: dict | None, cfg) -> dict:
    combined = {
        "system": bulletin["system"] + " + hydrologic conditioning",
        "issued_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "insar_bulletin": bulletin,
        "hydro_conditions": hydro,
        "escalations": [],
    }

    clusters = [dict(c) for c in bulletin.get("clusters", [])]
    level = bulletin.get("system_alert_level", "NORMAL")

    if hydro is None:
        combined["note"] = "hydro layer unavailable — InSAR alerts unchanged"
    else:
        state = hydro["state"]
        if state in ("PRIMED", "PRIMED_SEVERE"):
            for c in clusters:
                old = c["alert_level"]
                c["alert_level"] = bump(old)
                if c["alert_level"] != old:
                    combined["escalations"].append(
                        f"cluster {c['cluster_id']}: {old} -> {c['alert_level']} "
                        f"(hydro {state}: {hydro['reasons'][0]})"
                    )
            if clusters:
                level = max((c["alert_level"] for c in clusters), key=LEVEL_ORDER.index)
        if state == "PRIMED_SEVERE" and LEVEL_ORDER.index(level) < LEVEL_ORDER.index("WATCH"):
            level = "WATCH"
            combined["escalations"].append(
                "system floor raised to WATCH: debris-flow conditions across the "
                "watershed (rainfall threshold exceeded)"
            )
        if state == "ELEVATED":
            combined["note"] = ("hydrologic conditions ELEVATED — no escalation yet, "
                                "re-check after the next rainfall")

    combined["clusters"] = clusters
    combined["system_alert_level"] = level
    return combined


def main():
    cfg = Config.load()
    path = cfg.output_dir / "alert_bulletin.json"
    if not path.exists():
        raise SystemExit("No alert_bulletin.json — run src.run_operational (or run_demo) first.")
    bulletin = json.loads(path.read_text())

    hydro = None
    try:
        from .hydro import get_conditions

        hydro = get_conditions(cfg).as_dict()
    except Exception as e:
        print(f"hydro layer unavailable ({type(e).__name__}: {e}) — continuing without it")

    combined = fuse(bulletin, hydro, cfg)
    out = cfg.output_dir / "combined_bulletin.json"
    out.write_text(json.dumps(combined, indent=2))

    print(f"\n{'='*64}")
    print(f"  COMBINED ALERT LEVEL: {combined['system_alert_level']}")
    if hydro:
        print(f"  hydro state: {hydro['state']}  "
              f"(rain24 {hydro['rain_24h_mm']} mm, API {hydro['antecedent_api_mm']} mm"
              + (f", sat {hydro['soil_saturation']:.0%}" if hydro.get("soil_saturation") is not None else "")
              + ")")
    for e in combined["escalations"]:
        print(f"  ! {e}")
    if combined.get("note"):
        print(f"  note: {combined['note']}")
    print(f"{'='*64}")
    print(f"written to {out}")


if __name__ == "__main__":
    main()
