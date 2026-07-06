#!/usr/bin/env python3
"""
lidar_xsection_cutter.py — Cut channel cross-sections from NC QL2 LiDAR DEM

For the unstudied flashy headwater tributaries (Cox Creek, upper Tilley, etc.)
where no FEMA/TVA section exists. Produces station-elevation cross-sections
(HEC-RAS-style), top-of-bank estimates, and a plot per section.

DATA SOURCE (download before running):
  NC QL2 LiDAR DEM (bare-earth, ft, NAVD88) — NC Spatial Data Download
  https://sdd.nc.gov  ->  Elevation -> DEM (county tiles, GeoTIFF)
  Jackson County tiles covering the Cullowhee Creek watershed.
  Alternative entry: https://www.nconemap.gov (search "QL2 DEM").

TWO MODES
  A) Explicit lines:   --lines lines.csv
     CSV columns: section_id, lat1, lon1, lat2, lon2   (endpoints of each cut)
  B) Auto-perpendicular: --centerline centerline.csv --spacing 100 --width 200
     centerline.csv columns: lat, lon (ordered downstream or upstream);
     sections are cut perpendicular every <spacing> ft, <width> ft total.

USAGE
  pip install rasterio pyproj numpy pandas matplotlib
  python lidar_xsection_cutter.py --dem jackson_tile.tif --lines lines.csv \
         --out-dir xs_out --npts 200

OUTPUT (per section)
  xs_out/<section_id>.csv        station_ft, elev_ft, lat, lon
  xs_out/<section_id>.png        profile plot with detected banks & thalweg
  xs_out/summary.csv             thalweg elev, L/R bank elev & station,
                                 bankfull-proxy depth, section width

NOTES
  * DEM must be bare-earth. NC QL2 vertical accuracy ~10 cm RMSE — fine for
    top-of-bank threshold rungs; not a substitute for a field shot at the
    sensor itself.
  * Post-Helene caveat: QL2 acquisition in this county predates Sept 2024.
    Treat cut sections on Helene-modified reaches as provisional until a
    field check.
  * Bank detection is a heuristic (max curvature away from thalweg within
    the search window). Review every plot; override in the CSV if needed.
"""

import argparse
import math
import os
import sys

import numpy as np
import pandas as pd

try:
    import rasterio
    from pyproj import Transformer
except ImportError:
    sys.exit("pip install rasterio pyproj  (plus numpy pandas matplotlib)")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

FT_PER_M = 3.28084


def sample_line(dem, tf_ll2dem, lat1, lon1, lat2, lon2, npts):
    """Sample DEM along a geographic line; returns station(ft), elev, lat, lon."""
    lats = np.linspace(lat1, lat2, npts)
    lons = np.linspace(lon1, lon2, npts)
    xs, ys = tf_ll2dem.transform(lons, lats)
    elev = np.array([v[0] for v in dem.sample(zip(xs, ys))], dtype=float)
    nod = dem.nodata
    if nod is not None:
        elev[elev == nod] = np.nan
    # station along the line in feet (planar distance in DEM CRS)
    d = np.zeros(npts)
    d[1:] = np.cumsum(np.hypot(np.diff(xs), np.diff(ys)))
    unit_ft = FT_PER_M if _crs_is_metric(dem.crs) else 1.0
    return d * unit_ft, elev, lats, lons


def _crs_is_metric(crs):
    try:
        return crs.linear_units.lower().startswith("met")
    except Exception:
        return True


def dem_units_to_ft(elev, assume_ft=None):
    """NC QL2 county DEMs are usually in US ft; sanity-guess if not told."""
    if assume_ft is True:
        return elev
    if assume_ft is False:
        return elev * FT_PER_M
    # Heuristic: WNC elevations 600-2100 m vs 2000-6900 ft
    med = np.nanmedian(elev)
    return elev if med > 1500 else elev * FT_PER_M


def detect_banks(sta, elev, search_ft=100.0, edge_pad=6):
    """Thalweg = min elev; banks = strongest convex slope-break on each side,
    searched only within `search_ft` of the thalweg and away from the line
    ends (smoothing-window edge artifacts)."""
    i0 = int(np.nanargmin(elev))
    result = {"thalweg_sta": sta[i0], "thalweg_elev": elev[i0]}
    k_full = np.gradient(np.gradient(_smooth(elev)))
    for side in ("left", "right"):
        if side == "left":
            idx = np.arange(edge_pad, i0)
        else:
            idx = np.arange(i0 + 1, len(sta) - edge_pad)
        idx = idx[np.abs(sta[idx] - sta[i0]) <= search_ft]
        if len(idx) < 3:
            result[f"{side}_bank_sta"] = np.nan
            result[f"{side}_bank_elev"] = np.nan
            continue
        j = idx[int(np.nanargmin(k_full[idx]))]
        result[f"{side}_bank_sta"] = sta[j]
        result[f"{side}_bank_elev"] = elev[j]
    lb, rb = result.get("left_bank_elev"), result.get("right_bank_elev")
    tb = np.nanmin([lb, rb])
    result["bank_depth_ft"] = tb - result["thalweg_elev"]
    return result


def _smooth(e, w=5):
    k = np.ones(w) / w
    return np.convolve(np.nan_to_num(e, nan=np.nanmean(e)), k, mode="same")


def perpendicular_lines(centerline, spacing_ft, width_ft):
    """Generate section endpoints perpendicular to a lat/lon centerline."""
    R = 20925525.0  # earth radius in ft
    pts = centerline[["lat", "lon"]].to_numpy()
    out, acc, sid = [], 0.0, 1
    for i in range(1, len(pts)):
        la1, lo1 = pts[i - 1]
        la2, lo2 = pts[i]
        dy = math.radians(la2 - la1) * R
        dx = math.radians(lo2 - lo1) * R * math.cos(math.radians((la1 + la2) / 2))
        seg = math.hypot(dx, dy)
        if seg == 0:
            continue
        ux, uy = dx / seg, dy / seg  # along-stream unit
        px, py = -uy, ux  # perpendicular unit
        pos = spacing_ft - acc
        while pos <= seg:
            t = pos / seg
            la = la1 + t * (la2 - la1)
            lo = lo1 + t * (lo2 - lo1)
            half = width_ft / 2
            dlat = (py * half) / R
            dlon = (px * half) / (R * math.cos(math.radians(la)))
            out.append(
                dict(
                    section_id=f"XS{sid:03d}",
                    lat1=la - math.degrees(dlat),
                    lon1=lo - math.degrees(dlon),
                    lat2=la + math.degrees(dlat),
                    lon2=lo + math.degrees(dlon),
                )
            )
            sid += 1
            pos += spacing_ft
        acc = (acc + seg) % spacing_ft
    return pd.DataFrame(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dem", required=True, help="QL2 DEM GeoTIFF")
    ap.add_argument("--lines", help="CSV: section_id,lat1,lon1,lat2,lon2")
    ap.add_argument("--centerline", help="CSV: lat,lon (ordered along stream)")
    ap.add_argument("--spacing", type=float, default=100.0, help="ft between auto sections")
    ap.add_argument("--width", type=float, default=200.0, help="ft total section width")
    ap.add_argument("--npts", type=int, default=200)
    ap.add_argument("--dem-units-ft", choices=["yes", "no", "auto"], default="auto")
    ap.add_argument("--out-dir", default="xs_out")
    args = ap.parse_args()

    if not args.lines and not args.centerline:
        sys.exit("Provide --lines or --centerline")

    os.makedirs(args.out_dir, exist_ok=True)
    dem = rasterio.open(args.dem)
    tf = Transformer.from_crs("EPSG:4326", dem.crs, always_xy=True)

    if args.lines:
        lines = pd.read_csv(args.lines)
    else:
        lines = perpendicular_lines(
            pd.read_csv(args.centerline), args.spacing, args.width
        )
        lines.to_csv(os.path.join(args.out_dir, "auto_lines.csv"), index=False)

    assume = {"yes": True, "no": False, "auto": None}[args.dem_units_ft]
    summary = []
    for _, r in lines.iterrows():
        sta, elev, lats, lons = sample_line(
            dem, tf, r.lat1, r.lon1, r.lat2, r.lon2, args.npts
        )
        elev = dem_units_to_ft(elev, assume)
        pd.DataFrame(
            dict(station_ft=sta, elev_ft=elev, lat=lats, lon=lons)
        ).to_csv(os.path.join(args.out_dir, f"{r.section_id}.csv"), index=False)

        b = detect_banks(sta, elev)
        b["section_id"] = r.section_id
        b["width_ft"] = sta[-1]
        summary.append(b)

        fig, ax = plt.subplots(figsize=(8, 3.5))
        ax.plot(sta, elev, "k-", lw=1)
        ax.plot(b["thalweg_sta"], b["thalweg_elev"], "bv", label="thalweg")
        for side, c in (("left", "g"), ("right", "r")):
            ax.plot(
                b[f"{side}_bank_sta"], b[f"{side}_bank_elev"], c + "^",
                label=f"{side} bank",
            )
        ax.set_xlabel("Station (ft)")
        ax.set_ylabel("Elev (ft NAVD88)")
        ax.set_title(f"{r.section_id}  depth≈{b['bank_depth_ft']:.1f} ft")
        ax.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(os.path.join(args.out_dir, f"{r.section_id}.png"), dpi=120)
        plt.close(fig)

    pd.DataFrame(summary).to_csv(
        os.path.join(args.out_dir, "summary.csv"), index=False
    )
    print(f"{len(summary)} sections -> {args.out_dir}/  (review PNGs; banks are heuristic)")


if __name__ == "__main__":
    main()
