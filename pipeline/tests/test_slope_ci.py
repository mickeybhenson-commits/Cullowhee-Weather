"""End-to-end test for the scheduled slope-monitor pass, with no network.

ASF search and HyP3 need the internet and a NASA Earthdata login, so CI cannot
prove the real pass on every commit. What it can prove — and what actually
breaks — is everything downstream of the download: warping products onto the
analysis grid, the incremental cache, the SBAS inversion over the whole cached
network, detection, the screening policy, and the surgical rewrite of the three
published pages.

So this test synthesises HyP3-shaped products with a known moving patch and
runs the real code over them, into throwaway copies of the pages.

    python pipeline/tests/test_slope_ci.py

Exit code 0 means the plumbing is sound. It says nothing about the satellite
data itself.
"""
from __future__ import annotations

import re
import shutil
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from pipeline.src import basins, cache, ledger, render, verdicts   # noqa: E402
from pipeline.src.config import Config                             # noqa: E402
from pipeline.src.detect import detect                             # noqa: E402
from pipeline.src.stack import analysis_grid                       # noqa: E402

# The moving patch: on the plan's known strong candidate, so a real regression
# in the geometry chain shows up as a location that stops matching.
PLANT_LON, PLANT_LAT = -83.1857, 35.2641
PLANT_RATE_MM_YR = 60.0

FAILURES: list[str] = []


def check(cond: bool, what: str) -> bool:
    print(f"  [{'PASS' if cond else 'FAIL'}] {what}")
    if not cond:
        FAILURES.append(what)
    return bool(cond)


# --------------------------------------------------------------------------
# synthetic HyP3 products
# --------------------------------------------------------------------------

def synth_products(cfg, tmp: Path, n_epochs: int = 14) -> list[tuple[date, date]]:
    """Write HyP3-shaped GeoTIFF products for a synthetic 12-day stack."""
    import rasterio
    from rasterio.transform import from_bounds

    H, W = analysis_grid(cfg)
    min_lon, min_lat, max_lon, max_lat = cfg.bbox
    transform = from_bounds(min_lon, min_lat, max_lon, max_lat, W, H)

    lon = min_lon + (np.arange(W) + 0.5) / W * (max_lon - min_lon)
    lat = max_lat - (np.arange(H) + 0.5) / H * (max_lat - min_lat)
    LON, LAT = np.meshgrid(lon, lat)

    # terrain: a ridge/valley field steep enough to clear slope_min_deg
    dem = (900.0
           + 420.0 * np.sin((LON + 83.19) * 130.0)
           + 300.0 * np.cos((LAT - 35.26) * 150.0)
           + 180.0 * np.sin((LON + LAT) * 90.0)).astype(np.float32)

    patch = (np.hypot((LON - PLANT_LON) * np.cos(np.radians(35.26)),
                      LAT - PLANT_LAT) < 0.0022)
    coh = np.clip(0.62 - 0.22 * np.abs(np.sin(LON * 220.0) * np.cos(LAT * 220.0)),
                  0.1, 0.95).astype(np.float32)

    rng = np.random.default_rng(20260828)
    # epochs first, so the pair displacement is a true difference of epochs
    epochs = [date(2025, 8, 11) + timedelta(days=12 * i) for i in range(n_epochs)]
    field = {}
    for i, d in enumerate(epochs):
        t_yr = (d - epochs[0]).days / 365.25
        motion = np.where(patch, PLANT_RATE_MM_YR * t_yr, 0.0)
        noise = rng.normal(0.0, 1.2, size=(H, W))
        field[d] = (motion + noise).astype(np.float32)

    pairs = []
    for i in range(n_epochs):
        for j in (i + 1, i + 2):
            if j < n_epochs and (epochs[j] - epochs[i]).days <= 36:
                pairs.append((epochs[i], epochs[j]))

    prof = dict(driver="GTiff", height=H, width=W, count=1, dtype="float32",
                crs="EPSG:4326", transform=transform, nodata=float("nan"))

    for d0, d1 in pairs:
        name = (f"S1AA_{d0:%Y%m%d}T120000_{d1:%Y%m%d}T120000_"
                f"VVP{(d1 - d0).days:03d}_INT80_G_ueF_TEST")
        pdir = tmp / name
        pdir.mkdir(parents=True, exist_ok=True)
        # HyP3 LOS displacement is metres; the loader converts to mm
        disp_m = (field[d1] - field[d0]) / 1000.0
        with rasterio.open(pdir / f"{name}_los_disp.tif", "w", **prof) as dst:
            dst.write(disp_m.astype(np.float32), 1)
        with rasterio.open(pdir / f"{name}_corr.tif", "w", **prof) as dst:
            dst.write(coh, 1)
        with rasterio.open(pdir / f"{name}_dem.tif", "w", **prof) as dst:
            dst.write(dem, 1)
    return pairs


# --------------------------------------------------------------------------
# the tests
# --------------------------------------------------------------------------

def test_cache_roundtrip(cfg, tmp: Path):
    print("\ncache: warp, append, save, reload")
    pairs = synth_products(cfg, tmp / "products")
    pc = cache.empty_cache(cfg)
    added = cache.ingest_products(pc, cfg, tmp / "products")
    check(len(added) == len(pairs), f"warped all {len(pairs)} synthetic pairs")
    check(pc.dem is not None, "picked up a DEM from the products")
    check(pc.shape == analysis_grid(cfg), f"cache is on the analysis grid {pc.shape}")

    again = cache.ingest_products(pc, cfg, tmp / "products")
    check(not again, "re-ingesting the same products adds nothing (incremental)")

    path = tmp / "pair_cache.npz"
    cache.save(pc, path)
    back, note = cache.load(cfg, path)
    check(len(back) == len(pc), f"reloaded {len(back)} pairs ({note})")
    check(np.allclose(back.disp[0], pc.disp[0], equal_nan=True),
          "displacement survives the save/load round trip")
    check(sorted(back.epochs) == sorted(pc.epochs), "epoch list survives the round trip")

    class Widened:
        bbox = (cfg.bbox[0] - 0.05, cfg.bbox[1], cfg.bbox[2], cfg.bbox[3])
    stale, note = cache.load(Widened(), path)
    check(len(stale) == 0 and "stale" in note,
          "a widened AOI correctly invalidates the cache instead of mixing grids")
    return pc


def test_detection(cfg, pc):
    print("\nSBAS + detection over the cached network")
    stack = cache.stack_from_cache(pc, cfg)
    check(len(stack.dates) == 14, f"inverted to {len(stack.dates)} epochs")
    fields, clusters = detect(stack, cfg)
    check(bool(clusters), f"detector found {len(clusters)} cluster(s)")
    if clusters:
        d = min(np.hypot(c.centroid_lonlat[0] - PLANT_LON,
                         c.centroid_lonlat[1] - PLANT_LAT) for c in clusters)
        check(d < 0.004, f"a cluster sits on the planted patch (off by {d:.4f}°)")
    noise = cache.epoch_noise_mm(stack, fields["usable"])
    check(len(noise) == len(stack.dates) and np.isfinite(noise[-1]),
          "per-epoch noise floor computed for every epoch")
    return stack, fields, clusters


def test_basins():
    print("\nsub-basin tagging")
    roster = basins.load_roster()
    check(len(roster) == 8, f"loaded {len(roster)} sub-basins")
    check(basins.tag(PLANT_LON, PLANT_LAT, roster)[0] == "CC-MS-1100",
          "the plan's strong candidate tags to Mtn. Lower (CC-MS-1100)")
    check(basins.tag(-83.2069, 35.2955, roster)[0] == "CC-COX-097",
          "the plan's weaker candidate tags to Cox Branch (CC-COX-097)")
    check(basins.tag(-83.30, 35.40, roster) == basins.OUTSIDE,
          "a point outside the watershed is not force-tagged")


def test_pages(cfg, stack, fields, clusters):
    print("\npage rewrite into throwaway copies")
    sandbox = Path(tempfile.mkdtemp(prefix="slope-pages-"))
    originals = {}
    for name in ("slope_monitor.html", "slope_map.html", "slope_3d.html"):
        src = REPO / name
        if not src.exists():
            check(False, f"{name} exists in the repo")
            continue
        shutil.copy2(src, sandbox / name)
        originals[name] = src.read_text(encoding="utf-8")

    render.MONITOR = sandbox / "slope_monitor.html"
    render.MAP = sandbox / "slope_map.html"
    render.THREED = sandbox / "slope_3d.html"

    import pipeline.ci_update as ci
    from pipeline.src.alert import escalate, write_bulletin
    from pipeline.src.fuse import fuse

    level = escalate(stack, clusters, cfg)
    bulletin = write_bulletin(stack, clusters, level, cfg)
    hydro = {"state": "DRY", "rain_24h_mm": 0.0, "rain_72h_mm": 0.4,
             "antecedent_api_mm": 0.5, "soil_saturation": 0.63,
             "forecast_48h_mm": 0.0, "stream": "test", "source": "test",
             "reasons": ["synthetic conditions for the offline test"]}
    combined = fuse(bulletin, hydro, cfg)
    data = ci.build_page_data(cfg, stack, fields, clusters, bulletin, combined,
                              hydro, n_pairs=len(stack.dates))

    render.render_all(data)
    first = {n: (sandbox / n).read_text(encoding="utf-8") for n in originals}

    render.render_all(data)
    second = {n: (sandbox / n).read_text(encoding="utf-8") for n in originals}

    for name, html in first.items():
        check(html == second[name], f"{name}: a second run is byte-identical")

    # A different pass must move ONLY the generated regions.
    other = _mutate(data)
    render.render_all(other)
    third = {n: (sandbox / n).read_text(encoding="utf-8") for n in originals}
    for name, html in first.items():
        check(html != third[name], f"{name}: new data actually changes the page")
        check(_strip_regions(html) == _strip_regions(third[name]),
              f"{name}: scaffolding outside the regions is byte-for-byte identical "
              "across different passes")
    render.render_all(data)
    first = {n: (sandbox / n).read_text(encoding="utf-8") for n in originals}

    expected = {
        "slope_monitor.html": ["POSTURE", "TILES", "MAPCARD", "CLUSTERHEAD",
                               "CHARTS", "PROSE", "DATA", "VERDICT", "ROWS",
                               "CHARTCFG"],
        "slope_map.html": ["PANELSUB", "PANELFOOT", "CLUSTERS", "TS", "VELURI",
                           "SUBBASINS", "BOUNDS", "VERDICTMAP", "CLSOF",
                           "POPUPVERDICT"],
        "slope_3d.html": ["DATA3D", "HEADER3D"],
    }
    for name, regions in expected.items():
        if name not in first:
            continue
        missing = [r for r in regions
                   if f"<!--SLOPE:{r}-->" not in first[name]
                   or f"<!--/SLOPE:{r}-->" not in first[name]]
        check(not missing, f"{name}: every region is marker-delimited "
                           f"{'(missing ' + ', '.join(missing) + ')' if missing else ''}")

    # the hand-written scaffolding the plan says to preserve must still be there
    landmarks = {
        "slope_monitor.html": ["--abyss:#0a161a", "function chart(boxId, opts)",
                               "<h2>The evidence</h2>", "chart-noise",
                               '<a href="index.html">', "hoverLine(svg, box"],
        "slope_map.html": ["leaflet@1.9.4/dist/leaflet.js", "function sparkSVG(id)",
                           "L.control.layers(", "OpenTopoMap", "function popupHTML(p)"],
        "slope_3d.html": ["window.__HGRID__", "window.__GEO__", "THREE.WebGLRenderer",
                          "QL1 2025 lidar"],
    }
    for name, html in first.items():
        missing = [m for m in landmarks[name] if m not in html]
        check(not missing, f"{name}: hand-written scaffolding intact "
                           f"{'(lost ' + ', '.join(missing) + ')' if missing else ''}")

    banned = ["seasonal artifact", "none is a credible landslide",
              "Noise — velocity contradicts", "analyst review says",
              "re-check each pass"]
    for name, html in first.items():
        hit = [b for b in banned if b in html]
        check(not hit, f"{name}: no analyst-voice text left "
                       f"{'(found ' + ', '.join(hit) + ')' if hit else ''}")
        check(verdicts.REVIEW_LABEL in html,
              f"{name}: carries the pending-analyst-review stamp")

    mon = first.get("slope_monitor.html", "")
    check("WARNING" not in _generated_only(mon, "VERDICT"),
          "slope_monitor.html: the screener never wrote WARNING into a verdict")
    check(re.search(r'const DATA = \{.*"review"', mon, re.S) is not None,
          "slope_monitor.html: DATA carries the review stamp for the table")

    shutil.rmtree(sandbox, ignore_errors=True)


def _mutate(data: dict) -> dict:
    """Same shape, different pass — used to prove only the regions move."""
    import copy
    d = copy.deepcopy(data)
    d["meta"]["combined_level"] = "ADVISORY"
    d["meta"]["last"] = "2026-02-01"
    d["meta"]["epochs"] += 1
    d["hydro"]["state"] = "ELEVATED"
    d["clusters"] = d["clusters"][:1]
    for c in d["clusters"]:
        c["screening"]["verdict"] = "suspect artifact"
        c["screening"]["style"] = "suspect"
        c["screening"]["reason"] = "Synthetic mutation for the scaffolding test."
    d["focus"] = d["clusters"][0] if d["clusters"] else d["focus"]
    return d


def _strip_regions(html: str) -> str:
    return re.sub(r"<!--SLOPE:(\w+)-->.*?<!--/SLOPE:\1-->", r"<!--SLOPE:\1-->", html, flags=re.S)


def _generated_only(html: str, name: str) -> str:
    m = re.search(re.escape(f"<!--SLOPE:{name}-->") + "(.*?)"
                  + re.escape(f"<!--/SLOPE:{name}-->"), html, re.S)
    return m.group(1) if m else ""


def main() -> int:
    cfg = Config.load()
    print(f"AOI {cfg.aoi['name']} — analysis grid {analysis_grid(cfg)}")

    print("\nverdict policy")
    check(verdicts.selftest(), "verdict policy self-test")

    print("\nhydro classification")
    from pipeline.src.hydro import selftest as hydro_selftest
    check(hydro_selftest(), "hydro ladder self-test")

    test_basins()

    tmp = Path(tempfile.mkdtemp(prefix="slope-ci-"))
    try:
        pc = test_cache_roundtrip(cfg, tmp)
        stack, fields, clusters = test_detection(cfg, pc)
        test_pages(cfg, stack, fields, clusters)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILURES:
        print(f"FAILED — {len(FAILURES)} check(s):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
