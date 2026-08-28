"""Tiered alerting: escalate clusters, write GeoJSON + JSON bulletin.

Levels
  NORMAL    nothing exceeds thresholds
  ADVISORY  moving cluster detected (velocity gate)
  WATCH     cluster is fast, accelerating, and persistent
  WARNING   WATCH + inverse-velocity forecast converges within the horizon
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .detect import Cluster
from .forecast import inverse_velocity_forecast
from .stack import DisplacementStack

LEVEL_ORDER = ["NORMAL", "ADVISORY", "WATCH", "WARNING"]


def escalate(stack: DisplacementStack, clusters: list[Cluster], cfg) -> str:
    for c in clusters:
        if c.level == "WATCH":
            fc = inverse_velocity_forecast(stack, c, cfg)
            c.forecast = fc
            if fc and fc["within_warning_horizon"]:
                c.level = "WARNING"
    if not clusters:
        return "NORMAL"
    return max((c.level for c in clusters), key=LEVEL_ORDER.index)


def cluster_polygon(stack: DisplacementStack, c: Cluster) -> list:
    """Bounding polygon of the cluster in lon/lat (simple envelope)."""
    import numpy as np

    rows, cols = np.where(c.mask)
    corners = [
        stack.pixel_lonlat(rows.min(), cols.min()),
        stack.pixel_lonlat(rows.min(), cols.max()),
        stack.pixel_lonlat(rows.max(), cols.max()),
        stack.pixel_lonlat(rows.max(), cols.min()),
    ]
    ring = [[round(lon, 6), round(lat, 6)] for lon, lat in corners]
    return [ring + [ring[0]]]


def write_bulletin(stack: DisplacementStack, clusters: list[Cluster], system_level: str, cfg) -> dict:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    features = []
    for c in clusters:
        props = {
            "cluster_id": c.cluster_id,
            "alert_level": c.level,
            "n_pixels": c.n_pixels,
            "mean_los_velocity_mm_yr": round(c.mean_velocity, 1),
            "peak_los_velocity_mm_yr": round(c.peak_velocity, 1),
            "mean_los_accel_mm_yr2": round(c.mean_accel, 1),
            "mean_slope_deg": round(c.mean_slope_deg, 1),
            "centroid_lon": round(c.centroid_lonlat[0], 6),
            "centroid_lat": round(c.centroid_lonlat[1], 6),
        }
        if c.forecast:
            props["forecast"] = c.forecast
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": cluster_polygon(stack, c)},
                "properties": props,
            }
        )

    geojson = {"type": "FeatureCollection", "features": features}
    bulletin = {
        "system": "Cullowhee Creek Watershed LEWS (Sentinel-1 InSAR)",
        "issued_utc": now,
        "latest_acquisition": stack.dates[-1].isoformat(),
        "epochs_in_stack": len(stack.dates),
        "system_alert_level": system_level,
        "clusters": [f["properties"] for f in features],
        "notes": [
            "LOS velocities: negative = motion away from the satellite (downslope on east-facing slopes for descending passes).",
            "Forecast dates are inverse-velocity extrapolations with weeks-scale uncertainty; use them to prioritize ground inspection and instrumentation, not evacuation timing.",
        ],
    }

    out = cfg.output_dir
    (out / "alert_bulletin.json").write_text(json.dumps(bulletin, indent=2))
    (out / "alert_clusters.geojson").write_text(json.dumps(geojson, indent=2))
    return bulletin


def print_bulletin(bulletin: dict):
    print(f"\n{'='*64}")
    print(f"  {bulletin['system']}")
    print(f"  Issued {bulletin['issued_utc']}   latest scene {bulletin['latest_acquisition']}")
    print(f"  SYSTEM ALERT LEVEL: {bulletin['system_alert_level']}")
    print(f"{'='*64}")
    for c in bulletin["clusters"]:
        print(
            f"  [{c['alert_level']:8s}] cluster {c['cluster_id']}: "
            f"{c['n_pixels']} px @ ({c['centroid_lat']:.4f}, {c['centroid_lon']:.4f})  "
            f"v={c['mean_los_velocity_mm_yr']} mm/yr  a={c['mean_los_accel_mm_yr2']} mm/yr²  "
            f"slope={c['mean_slope_deg']}°"
        )
        if c.get("forecast"):
            f = c["forecast"]
            print(
                f"             inverse-velocity forecast: ~{f['days_to_failure']} days "
                f"({f['forecast_date']}, r²={f['r2']})"
            )
    if not bulletin["clusters"]:
        print("  No clusters above thresholds.")
