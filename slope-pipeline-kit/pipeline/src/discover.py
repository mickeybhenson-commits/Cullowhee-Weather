"""Sentinel-1 scene discovery over the AOI via the ASF Search API.

Search is free and needs no login; run this on any machine with internet
access. Downloads (done later by HyP3, not here) need a NASA Earthdata login.

Usage:
    python -m src.discover            # print scene inventory for the AOI
"""
from __future__ import annotations

import json
from collections import Counter

from .config import Config


def find_scenes(cfg: Config, start: str | None = None, end: str | None = None):
    import asf_search as asf  # imported lazily: not needed for offline demo

    s1 = cfg["sentinel1"]
    results = asf.geo_search(
        platform=[asf.PLATFORM.SENTINEL1],
        processingLevel=[asf.PRODUCT_TYPE.SLC],
        beamMode=[asf.BEAMMODE.IW],
        flightDirection=s1["flight_direction"],
        intersectsWith=cfg.aoi["wkt"],
        start=start or s1["start_date"],
        end=end,
    )
    return results


def build_pairs(results, max_baseline_days: int):
    """Nearest-neighbour + skip-one interferogram pairs within one path/frame.

    Short temporal baselines are essential over the forested Appalachians:
    C-band coherence decays fast with vegetation growth.
    """
    from datetime import datetime

    by_path: dict[int, list] = {}
    for r in results:
        by_path.setdefault(r.properties["pathNumber"], []).append(r)

    pairs = []
    for path, scenes in by_path.items():
        scenes.sort(key=lambda r: r.properties["startTime"])
        for i, ref in enumerate(scenes):
            t0 = datetime.fromisoformat(ref.properties["startTime"].rstrip("Z"))
            for sec in scenes[i + 1 : i + 3]:  # nearest + skip-one
                t1 = datetime.fromisoformat(sec.properties["startTime"].rstrip("Z"))
                if (t1 - t0).days <= max_baseline_days:
                    pairs.append((ref, sec))
    return pairs


def main():
    cfg = Config.load()
    results = find_scenes(cfg)
    dates = sorted(r.properties["startTime"][:10] for r in results)
    paths = Counter(
        (r.properties["pathNumber"], r.properties["flightDirection"]) for r in results
    )
    print(f"AOI: {cfg.aoi['name']}")
    print(f"SLC scenes: {len(results)}  ({dates[0]} .. {dates[-1]})" if dates else "No scenes found")
    for (path, fd), n in sorted(paths.items()):
        print(f"  path {path} {fd}: {n} scenes")
    pairs = build_pairs(results, cfg["sentinel1"]["max_temporal_baseline_days"])
    print(f"Interferogram pairs to process: {len(pairs)}")
    inventory = [
        {
            "sceneName": r.properties["sceneName"],
            "startTime": r.properties["startTime"],
            "pathNumber": r.properties["pathNumber"],
            "flightDirection": r.properties["flightDirection"],
        }
        for r in results
    ]
    out = cfg.output_dir / "scene_inventory.json"
    out.write_text(json.dumps(inventory, indent=2))
    print(f"Inventory written to {out}")


if __name__ == "__main__":
    main()
