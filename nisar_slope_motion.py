#!/usr/bin/env python3
"""
nisar_slope_motion.py — pull line-of-sight ground motion for the NC 107 / WCU slope
out of NISAR L2 GUNW interferograms (ASF DAAC), one number per pass pair plus a map.

Usage
    pip install h5py numpy pyproj matplotlib
    python nisar_slope_motion.py NISAR_L2_PR_GUNW_*.h5 [--radius 2500] [--out nisar_out]

Inputs are the ~2.3 GB GUNW .h5 files from https://search.asf.alaska.edu (dataset NISAR,
product GUNW, point -83.1825 35.312; free Earthdata login needed). Only a small window
around the road is read, so it runs in seconds even on the full files.

What it does, per interferogram
  1. finds the 80 m unwrapped phase, coherence and connected-components layers
  2. cuts a window of --radius metres around the road midpoint
  3. converts phase to LOS displacement:  d = -phi * lambda / (4*pi)   (lambda ~ 23.8 cm)
  4. references it to a patch of flat ground on campus (so the number is relative motion)
  5. masks low coherence / unconnected pixels
  6. reports median displacement on the hillside EAST of 107 and WEST of 107 separately,
     writes a PNG map, a CSV of the window, and a summary.json
If several pairs from the same track are given it also chains them into a time series.

Caveats you should know before quoting a number
  * Sign: this script reports POSITIVE = ground moved TOWARD the satellite (LOS). Check the
    'displacement sign convention' line it prints per file against the product's own metadata.
  * Each pass only sees the slope facing its beam: descending (T026, T127) lights the
    hillside EAST of 107; ascending (T047) lights the hillside WEST of 107 above campus.
  * 80 m pixels, provisional calibration (Jun 2026 on), tropospheric noise of a few mm is
    normal for one 12-day pair. Trust trends across several pairs, not one pair.
"""
import argparse, json, math, os, sys
import numpy as np

try:
    import h5py
    from pyproj import Transformer
except ImportError as e:
    sys.exit("missing dependency: %s  (pip install h5py numpy pyproj matplotlib)" % e)

# ---- site definition (same as NISAR.html) ------------------------------------------------
ROAD = [(35.3250, -83.1885), (35.3190, -83.1870), (35.3140, -83.1840),
        (35.3090, -83.1805), (35.3040, -83.1780), (35.2990, -83.1765)]   # approximate NC 107 trace
TARGET = (35.3120, -83.1825)      # road midpoint
REF = (35.3095, -83.1826)         # flat campus ground used as the zero-motion reference
LAMBDA_DEFAULT = 0.2384           # m, NISAR L-band (1257 MHz)
C = 299792458.0


def find(h5, name, prefer=("unwrappedInterferogram", "frequencyA")):
    """Return the first dataset whose basename == name, preferring paths containing `prefer`."""
    hits = []
    def visit(p, obj):
        if isinstance(obj, h5py.Dataset) and p.split("/")[-1] == name:
            hits.append(p)
    h5.visititems(visit)
    if not hits:
        return None
    hits.sort(key=lambda p: -sum(k in p for k in prefer))
    return hits[0]


def epsg_of(h5, proj_path):
    ds = h5[proj_path]
    for k in ("epsg_code", "spatial_epsg", "epsg"):
        if k in ds.attrs:
            return int(np.asarray(ds.attrs[k]).ravel()[0])
    v = ds[()]
    try:
        return int(np.asarray(v).ravel()[0])
    except Exception:
        return int(str(v).split(":")[-1])


def side_of_road(x, y, road_xy):
    """+1 = east of the road (right of a southbound traveller), -1 = west."""
    best, bestd = 0, 1e30
    for (x1, y1), (x2, y2) in zip(road_xy[:-1], road_xy[1:]):
        dx, dy = x2 - x1, y2 - y1
        t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
        px, py = x1 + t * dx, y1 + t * dy
        d = (x - px) ** 2 + (y - py) ** 2
        if d < bestd:
            bestd = d
            cross = dx * (y - y1) - dy * (x - x1)
            best = 1 if cross < 0 else -1        # road runs N->S in ROAD order, so east is cross<0
    return best


def process(path, radius, outdir, summary):
    name = os.path.basename(path)
    parts = name.split("_")
    # NISAR_L2_PR_GUNW_028_047_A_019_029_4000_SH_<ref>_<ref>_<sec>_<sec>_...
    track, direction = int(parts[5]), parts[6]
    ref_date, sec_date = parts[11][:8], parts[13][:8]
    with h5py.File(path, "r") as h5:
        p_phase = find(h5, "unwrappedPhase")
        p_coh = find(h5, "coherenceMagnitude")
        p_cc = find(h5, "connectedComponents")
        grp = "/".join(p_phase.split("/")[:-1])
        # x/y coordinates live beside the layer (or one level up); take the nearest match
        def near(nm):
            g = grp
            while g:
                cand = g + "/" + nm
                if cand in h5:
                    return cand
                g = "/".join(g.split("/")[:-1])
            return find(h5, nm)
        p_x, p_y, p_proj = near("xCoordinates"), near("yCoordinates"), near("projection")
        p_f = find(h5, "centerFrequency")
        lam = C / float(h5[p_f][()]) if p_f else LAMBDA_DEFAULT
        epsg = epsg_of(h5, p_proj)
        X = h5[p_x][()]; Y = h5[p_y][()]
        if X.ndim > 1: X = X[0]
        if Y.ndim > 1: Y = Y[:, 0]

        # pick the phase layer that matches this coordinate grid (80 m posting)
        ph_ds = h5[p_phase]
        if ph_ds.shape != (len(Y), len(X)):
            sys.exit("%s: phase grid %s != coord grid %s — check paths" % (name, ph_ds.shape, (len(Y), len(X))))

        to_map = Transformer.from_crs("EPSG:4326", "EPSG:%d" % epsg, always_xy=True)
        tx, ty = to_map.transform(TARGET[1], TARGET[0])
        rx, ry = to_map.transform(REF[1], REF[0])
        road_xy = [to_map.transform(lo, la) for la, lo in ROAD]

        # window indices
        ix = np.where((X >= tx - radius) & (X <= tx + radius))[0]
        iy = np.where((Y >= ty - radius) & (Y <= ty + radius))[0]
        if not len(ix) or not len(iy):
            print("  %s: window outside product footprint — skipped" % name)
            return
        x0, x1, y0, y1 = ix.min(), ix.max() + 1, iy.min(), iy.max() + 1
        phase = ph_ds[y0:y1, x0:x1].astype("f8")
        coh = h5[p_coh]
        coh = coh[y0:y1, x0:x1] if coh.shape == ph_ds.shape else None
        cc = h5[p_cc]
        cc = cc[y0:y1, x0:x1] if cc.shape == ph_ds.shape else None
        xs, ys = X[x0:x1], Y[y0:y1]

    disp = -phase * lam / (4 * math.pi) * 1000.0          # mm, + = toward satellite
    mask = np.isfinite(disp)
    if coh is not None: mask &= coh >= 0.3
    if cc is not None:  mask &= cc > 0
    disp[~mask] = np.nan

    # reference to flat campus ground (median of a 3x3 box)
    jx = int(np.argmin(np.abs(xs - rx))); jy = int(np.argmin(np.abs(ys - ry)))
    refv = np.nanmedian(disp[max(0, jy - 1):jy + 2, max(0, jx - 1):jx + 2])
    if not np.isfinite(refv):
        refv = np.nanmedian(disp)
        print("  reference patch is masked; using window median instead")
    disp -= refv

    # east / west hillside statistics (within 600 m of the road, excluding the road corridor itself)
    XX, YY = np.meshgrid(xs, ys)
    sides = np.vectorize(side_of_road)(XX, YY, road_xy) if XX.size < 40000 else np.zeros_like(XX)
    dist = np.full(XX.shape, np.inf)
    for (ax, ay), (bx, by) in zip(road_xy[:-1], road_xy[1:]):
        dx, dy = bx - ax, by - ay
        t = np.clip(((XX - ax) * dx + (YY - ay) * dy) / (dx * dx + dy * dy), 0, 1)
        dist = np.minimum(dist, np.hypot(XX - (ax + t * dx), YY - (ay + t * dy)))
    band = (dist > 80) & (dist < 600)
    east = np.nanmedian(disp[band & (sides > 0)]); west = np.nanmedian(disp[band & (sides < 0)])
    span = (np.datetime64("%s-%s-%s" % (sec_date[:4], sec_date[4:6], sec_date[6:])) -
            np.datetime64("%s-%s-%s" % (ref_date[:4], ref_date[4:6], ref_date[6:]))).astype(int)
    lit = "hillside EAST of 107" if direction == "D" else "hillside WEST of 107 (above campus)"
    rec = dict(file=name, track=track, direction=direction, ref=ref_date, sec=sec_date, span_days=int(span),
               wavelength_m=lam, epsg=epsg, pixels_used=int(np.isfinite(disp).sum()),
               los_mm_east_of_107=None if np.isnan(east) else round(float(east), 1),
               los_mm_west_of_107=None if np.isnan(west) else round(float(west), 1),
               beam_lit_side=lit, sign="positive = toward satellite (LOS)")
    summary.append(rec)
    print("  T%03d %s  %s -> %s (%d d)  east-of-107 %s mm  west-of-107 %s mm  [%s illuminated]  px=%d"
          % (track, direction, ref_date, sec_date, span, rec["los_mm_east_of_107"],
             rec["los_mm_west_of_107"], lit, rec["pixels_used"]))

    # CSV of the window
    to_ll = Transformer.from_crs("EPSG:%d" % epsg, "EPSG:4326", always_xy=True)
    lon, lat = to_ll.transform(XX, YY)
    tag = "T%03d%s_%s_%s" % (track, direction, ref_date, sec_date)
    csv = os.path.join(outdir, tag + ".csv")
    with open(csv, "w") as f:
        f.write("lat,lon,x,y,los_mm,coherence,side_of_107\n")
        for j in range(disp.shape[0]):
            for i in range(disp.shape[1]):
                if np.isfinite(disp[j, i]):
                    f.write("%.5f,%.5f,%.1f,%.1f,%.2f,%s,%s\n" % (
                        lat[j, i], lon[j, i], XX[j, i], YY[j, i], disp[j, i],
                        "" if coh is None else "%.2f" % coh[j, i], "E" if sides[j, i] > 0 else "W"))

    # PNG map
    try:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        v = np.nanpercentile(np.abs(disp), 98) if np.isfinite(disp).any() else 10
        fig, ax = plt.subplots(figsize=(6.4, 6), dpi=150)
        im = ax.imshow(disp, extent=[xs[0], xs[-1], ys[-1], ys[0]], cmap="RdBu_r", vmin=-v, vmax=v)
        rxs, rys = zip(*road_xy)
        ax.plot(rxs, rys, "k-", lw=2, label="NC 107 (approx.)")
        ax.plot(rx, ry, "k^", ms=8, label="reference (campus)")
        ax.set_title("NISAR T%03d %s  %s → %s\nLOS displacement, mm (+ toward satellite) · beam lights %s"
                     % (track, direction, ref_date, sec_date, lit), fontsize=9)
        ax.set_xlabel("EPSG:%d x (m)" % epsg); ax.set_ylabel("y (m)")
        fig.colorbar(im, ax=ax, shrink=0.8, label="mm")
        ax.legend(fontsize=7, loc="lower left")
        fig.tight_layout(); fig.savefig(os.path.join(outdir, tag + ".png")); plt.close(fig)
    except Exception as e:
        print("  (map skipped: %s)" % e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--radius", type=float, default=2500, help="half-width of window, m")
    ap.add_argument("--out", default="nisar_out")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    summary = []
    for f in a.files:
        print(os.path.basename(f))
        try:
            process(f, a.radius, a.out, summary)
        except Exception as e:
            print("  FAILED: %s" % e)

    # chain same-track pairs into a cumulative series
    series = {}
    for r in sorted(summary, key=lambda r: r["ref"]):
        k = "T%03d%s" % (r["track"], r["direction"])
        key = "los_mm_east_of_107" if r["direction"] == "D" else "los_mm_west_of_107"
        if r[key] is None: continue
        s = series.setdefault(k, [{"date": r["ref"], "cum_mm": 0.0}])
        if s[-1]["date"] == r["ref"]:
            s.append({"date": r["sec"], "cum_mm": round(s[-1]["cum_mm"] + r[key], 1)})
    for k, s in series.items():
        if len(s) > 1:
            d0 = np.datetime64("%s-%s-%s" % (s[0]["date"][:4], s[0]["date"][4:6], s[0]["date"][6:]))
            d1 = np.datetime64("%s-%s-%s" % (s[-1]["date"][:4], s[-1]["date"][4:6], s[-1]["date"][6:]))
            days = (d1 - d0).astype(int)
            print("%s cumulative %s -> %s: %.1f mm over %d d  (%.0f mm/yr LOS)"
                  % (k, s[0]["date"], s[-1]["date"], s[-1]["cum_mm"], days, s[-1]["cum_mm"] * 365.0 / max(days, 1)))
    with open(os.path.join(a.out, "summary.json"), "w") as f:
        json.dump({"pairs": summary, "series": series, "site": {"target": TARGET, "reference": REF}}, f, indent=1)
    print("wrote", os.path.join(a.out, "summary.json"))


if __name__ == "__main__":
    main()
