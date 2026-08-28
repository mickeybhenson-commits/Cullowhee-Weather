"""Scheduled updater for the Cullowhee Creek slope monitor.

One entry point, driven by .github/workflows/slope-monitor.yml:

    python pipeline/ci_update.py check       # is there a new scene? (seconds)
    python pipeline/ci_update.py update      # normal pass: new scene -> new pages
    python pipeline/ci_update.py bootstrap   # first run: backfill the whole cache
    python pipeline/ci_update.py rebuild     # re-analyse + re-render from the cache

`check` is what makes a daily cron affordable. Sentinel-1 comes back over this
watershed about every 12 days, so most days there is nothing to do, and ASF
scene search is free and needs no login — the run answers "nothing new" and
exits without ever touching HyP3.

`update` is the real pass: submit the new interferogram pairs to HyP3, wait,
download only what is new, warp it into pipeline/state/pair_cache.npz, delete
the rasters, re-invert the whole cached network, re-detect, re-fuse with the
hydrologic layer, and rewrite the data regions of the three published pages.

`rebuild` does everything from `update` except talk to ASF or HyP3. It is how
you re-render after changing the pages or the screening code, and it is what
the workflow's --dry-run smoke test uses.

Nothing in here decides an alert level. Levels come from detect / forecast /
alert against the tuned thresholds in config.yaml; cluster descriptions come
from src/verdicts.py, which is metric-only and stamps every line
"automated screening — pending analyst review".
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.src import basins, cache, ledger, render          # noqa: E402
from pipeline.src.alert import escalate, print_bulletin, write_bulletin  # noqa: E402
from pipeline.src.config import ROOT, Config, earthdata_credentials      # noqa: E402
from pipeline.src.detect import detect                          # noqa: E402
from pipeline.src.discover import build_pairs, find_scenes      # noqa: E402
from pipeline.src.fuse import fuse                              # noqa: E402
from pipeline.src.verdicts import REVIEW_LABEL, screen          # noqa: E402

STATE = ledger.STATE_DIR
WORK = ROOT / "data" / "_ci"          # scratch for raw HyP3 downloads; never committed
JOB_NAME = "cullowhee-lews"
DEFAULT_BUDGET_S = int(3.5 * 3600)    # PLAN: <= 3.5 h, Actions caps the job at 6 h


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def out(key: str, value) -> None:
    """Write a workflow output (and echo it to the log)."""
    print(f"  -> {key}={value}")
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as f:
            f.write(f"{key}={value}\n")


def summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as f:
            f.write(text + "\n")
    print(text)


def _granules(job) -> tuple[str, str] | None:
    g = (job.job_parameters or {}).get("granules")
    if not g or len(g) < 2:
        return None
    return g[0], g[1]


# --------------------------------------------------------------------------
# step 1 — discovery against the ledger
# --------------------------------------------------------------------------

def discover(cfg, led: dict):
    """(all asf results, new results). Search is free and needs no login."""
    results = find_scenes(cfg)
    s1 = cfg["sentinel1"]
    # PLAN: path 48 ASCENDING only — never mix geometries in one time series.
    # flight_direction is already filtered by the search; keep one path so a
    # newly-catalogued neighbouring path cannot silently join the stack.
    paths = {r.properties["pathNumber"] for r in results}
    if len(paths) > 1:
        keep = max(paths, key=lambda p: sum(
            1 for r in results if r.properties["pathNumber"] == p))
        print(f"scenes span paths {sorted(paths)} — keeping path {keep} only")
        results = [r for r in results if r.properties["pathNumber"] == keep]
    new = ledger.new_scenes(led, results)
    return results, new


# --------------------------------------------------------------------------
# step 2/3 — HyP3 submit, wait, download, warp, delete
# --------------------------------------------------------------------------

def _hyp3():
    import hyp3_sdk

    creds = earthdata_credentials()
    if creds:
        return hyp3_sdk.HyP3(username=creds[0], password=creds[1])
    return hyp3_sdk.HyP3()          # ~/.netrc fallback


def pairs_to_process(cfg, results, led, pc, only_new_scenes=None, limit=None):
    """Pairs the cache is missing, newest first (so a truncated run still helps)."""
    pairs = build_pairs(results, cfg["sentinel1"]["max_temporal_baseline_days"])
    cached = cache_keys(pc)
    failed = ledger.failed_pairs(led)
    new_names = ({r.properties["sceneName"] for r in only_new_scenes}
                 if only_new_scenes is not None else None)

    todo = []
    for ref, sec in pairs:
        rn, sn = ref.properties["sceneName"], sec.properties["sceneName"]
        key = ledger.pair_key(rn, sn)
        if key in cached or key in failed:
            continue
        if new_names is not None and rn not in new_names and sn not in new_names:
            continue
        todo.append((ref, sec))

    todo.sort(key=lambda p: p[1].properties["startTime"], reverse=True)
    if limit:
        todo = todo[:limit]
    return todo


def cache_keys(pc) -> set[str]:
    return set(pc.keys)


def submit_and_collect(cfg, todo, led, pc, budget_s: int) -> int:
    """Submit missing pairs, wait, download, warp into the cache, delete rasters."""
    if not todo:
        print("no pairs to submit")
        return 0

    hyp3 = _hyp3()
    h = cfg["hyp3"]
    wanted: dict[str, tuple[str, str]] = {}     # pair key -> (d0, d1)

    existing = {}
    try:
        for job in hyp3.find_jobs(name=JOB_NAME):
            g = _granules(job)
            if g:
                existing.setdefault(ledger.pair_key(*g), []).append(job)
    except Exception as e:
        print(f"could not list existing HyP3 jobs ({type(e).__name__}: {e})")

    submitted = 0
    for ref, sec in todo:
        rn, sn = ref.properties["sceneName"], sec.properties["sceneName"]
        key = ledger.pair_key(rn, sn)
        d0, d1 = ref.properties["startTime"][:10], sec.properties["startTime"][:10]
        wanted[key] = (d0, d1)

        prior = [j for j in existing.get(key, []) if not j.failed()]
        live = [j for j in prior if not (j.succeeded() and j.expired())]
        if live:
            print(f"  reusing HyP3 job for {d0} -> {d1}")
            ledger.set_pair(led, rn, sn, d0, d1, "submitted")
            continue
        if prior:
            print(f"  resubmitting expired pair {d0} -> {d1}")

        try:
            hyp3.submit_insar_job(
                granule1=rn, granule2=sn, name=JOB_NAME,
                looks=h["looks"],
                include_displacement_maps=h["include_displacement_maps"],
                include_dem=h["include_dem"],
                apply_water_mask=h["water_mask"],
            )
            submitted += 1
            ledger.set_pair(led, rn, sn, d0, d1, "submitted")
        except Exception as e:
            print(f"::warning::submit failed for {d0} -> {d1}: {type(e).__name__}: {e}")
            ledger.set_pair(led, rn, sn, d0, d1, "failed", f"submit: {type(e).__name__}")

    print(f"submitted {submitted} new HyP3 job(s); waiting up to {budget_s // 60} min")

    deadline = time.time() + budget_s
    batch = hyp3.find_jobs(name=JOB_NAME)
    try:
        batch = hyp3.watch(batch, timeout=max(60, int(deadline - time.time())), interval=60)
    except Exception as e:
        print(f"::warning::stopped waiting on HyP3 ({type(e).__name__}: {e}) — "
              "collecting whatever finished; the next run picks up the rest")
        batch = hyp3.refresh(hyp3.find_jobs(name=JOB_NAME))

    added = collect(cfg, batch, wanted, led, pc)
    return added


def collect(cfg, batch, wanted: dict, led: dict, pc) -> int:
    """Download finished jobs for the pairs we want, warp them, delete the rasters."""
    WORK.mkdir(parents=True, exist_ok=True)
    added_total = 0

    for job in batch:
        g = _granules(job)
        if not g:
            continue
        key = ledger.pair_key(*g)
        if key not in wanted or pc.has(key):
            continue
        if job.failed():
            d0, d1 = wanted[key]
            print(f"  HyP3 job FAILED for {d0} -> {d1} — not retried")
            ledger.set_pair(led, g[0], g[1], d0, d1, "failed", "hyp3 job failed")
            continue
        if not job.succeeded():
            continue
        try:
            if job.expired():
                print(f"  product expired on HyP3 for {wanted[key][0]} -> {wanted[key][1]}")
                continue
            files = job.download_files(location=WORK)
        except Exception as e:
            print(f"  download failed ({type(e).__name__}: {e})")
            continue

        for z in files:
            if z.suffix == ".zip":
                zipfile.ZipFile(z).extractall(WORK)
                z.unlink()

        added = cache.ingest_products(pc, cfg, WORK, key_for=_key_map(batch))
        added_total += len(added)
        d0, d1 = wanted[key]
        ledger.set_pair(led, g[0], g[1], d0, d1,
                        "cached" if key in pc.keys else "submitted")
        # the raw archive never survives the step that warped it
        shutil.rmtree(WORK, ignore_errors=True)
        WORK.mkdir(parents=True, exist_ok=True)

    shutil.rmtree(WORK, ignore_errors=True)
    return added_total


def _key_map(batch) -> dict[str, str]:
    """HyP3 product directory name -> ledger pair key."""
    m = {}
    for job in batch:
        g = _granules(job)
        if not g or not job.files:
            continue
        for f in job.files:
            stem = str(f.get("filename", "")).replace(".zip", "")
            if stem:
                m[stem] = ledger.pair_key(*g)
    return m


# --------------------------------------------------------------------------
# step 4/5 — analyse the cached stack and build the page payload
# --------------------------------------------------------------------------

def analyse(cfg, pc):
    stack = cache.stack_from_cache(pc, cfg)
    print(f"\nStack ready: {stack.shape[0]} epochs, grid {stack.shape[1]}x{stack.shape[2]}")
    fields, clusters = detect(stack, cfg)
    level = escalate(stack, clusters, cfg)
    bulletin = write_bulletin(stack, clusters, level, cfg)
    bulletin["data_source"] = "Sentinel-1 / ASF HyP3 (real)"
    (cfg.output_dir / "alert_bulletin.json").write_text(json.dumps(bulletin, indent=2))
    print_bulletin(bulletin)
    return stack, fields, clusters, bulletin, level


def hydro_conditions(cfg):
    try:
        from pipeline.src.hydro import get_conditions
        return get_conditions(cfg).as_dict()
    except Exception as e:
        print(f"hydro layer unavailable ({type(e).__name__}: {e}) — continuing without it")
        return None


def build_page_data(cfg, stack, fields, clusters, bulletin, combined, hydro,
                    n_pairs: int) -> dict:
    roster = basins.load_roster()
    roster_gj = basins.roster_geojson()
    dates = [d.isoformat() for d in stack.dates]
    t_days = [round((d - stack.dates[0]).days * 1.0, 1) for d in stack.dates]
    noise = cache.epoch_noise_mm(stack, fields["usable"])

    by_id = {c["cluster_id"]: c for c in combined["clusters"]}
    rows = []
    for c in clusters:
        series = np.nanmean(stack.disp[:, c.mask], axis=1)
        series = [round(float(v), 2) for v in series]
        sc = screen(series, stack.dates, float(np.nanmean(fields["velocity"][c.mask])))
        lon, lat = c.centroid_lonlat
        bid, bname = basins.tag(lon, lat, roster)
        se = float(np.nanmean(fields["se_velocity"][c.mask]))
        rows.append({
            "cluster_id": c.cluster_id,
            "n_pixels": c.n_pixels,
            "centroid_lat": round(lat, 6),
            "centroid_lon": round(lon, 6),
            "mean_los_velocity_mm_yr": round(c.mean_velocity, 1),
            "mean_los_accel_mm_yr2": round(c.mean_accel, 1),
            "mean_slope_deg": round(c.mean_slope_deg, 1),
            "coh": round(float(np.nanmean(stack.coherence[c.mask])), 2),
            "acres": round(c.n_pixels * render.PIXEL_ACRES, 1),
            "snr": round(abs(c.mean_velocity) / se, 1) if se > 0 else 0.0,
            "basin_id": bid,
            "basin_name": bname,
            # the level is the pipeline's, hydro-conditioned — never the screener's
            "alert_level": by_id.get(c.cluster_id, {}).get("alert_level", c.level),
            "screening": sc.as_dict(),
            "series": series,
            "multipolygon": render.mask_multipolygon(c.mask, stack.transform),
            "bbox_lonlat": [round(v, 6) for v in render.mask_bbox_lonlat(c.mask, stack.transform)],
        })

    rows.sort(key=lambda r: (
        {"candidate": 0, "suspect artifact": 1}.get(r["screening"]["verdict"], 2),
        -abs(r["screening"]["net_mm"]),
    ))

    leafoff = [i for i, d in enumerate(stack.dates) if d.month in (11, 12, 1, 2)]
    noise_ok = [n for n in noise if np.isfinite(n)]
    lo_noise = [noise[i] for i in leafoff if np.isfinite(noise[i])]
    ln_noise = [n for i, n in enumerate(noise) if i not in leafoff and np.isfinite(n)]

    span_days = (stack.dates[-1] - stack.dates[0]).days
    H, W = stack.shape[1], stack.shape[2]
    # candidate regions = every connected component of `hot`, before the
    # min_cluster_pixels filter drops the specks. It is the denominator the
    # page quotes ("6 of 77"), so compute it rather than guess.
    from scipy import ndimage
    _, n_regions = ndimage.label(fields["hot"], structure=np.ones((3, 3)))

    meta = {
        "issued_utc": render.issued_stamp(),
        "epochs": len(stack.dates),
        "pairs": n_pairs,
        "first": dates[0],
        "last": dates[-1],
        "span_months": max(1, round(span_days / 30.44)),
        "n_clusters": int(n_regions),
        "n_flagged": len(clusters),
        "usable_pct": int(round(100.0 * float(np.mean(fields["usable"])))),
        "coh_mean": round(float(np.nanmean(stack.coherence)), 2),
        "coherence_threshold": cfg.analysis["coherence_threshold"],
        "insar_level": bulletin["system_alert_level"],
        "combined_level": combined["system_alert_level"],
        "hydro_state": hydro["state"] if hydro else None,
        "grid_h": H,
        "grid_w": W,
        "bbox": [round(v, 6) for v in stack.transform],
        "leafoff_epochs": len(leafoff),
        "noise_leafoff": round(float(np.median(lo_noise)), 1) if lo_noise else None,
        "noise_leafon": round(float(np.median(ln_noise)), 1) if ln_noise else None,
        "noise_median": round(float(np.median(noise_ok)), 1) if noise_ok else None,
        "review": REVIEW_LABEL,
        "escalations": combined.get("escalations", []),
    }
    focus = rows[0] if rows else _placeholder_focus(dates)
    chart = _chart_bounds(focus, noise, leafoff)

    sig = fields["usable"] & (np.abs(fields["velocity"]) > 3.0 * fields["se_velocity"])
    vmax = render._vmax(fields["velocity"], sig)
    vel = np.where(sig, fields["velocity"], np.nan)
    vel_list = [None if not np.isfinite(v) else round(float(v), 1)
                for v in vel.ravel()]

    return {
        "dates": dates,
        "t_days": t_days,
        "noise_mm": noise,
        "clusters": rows,
        "focus": focus,
        "chart": chart,
        "meta": meta,
        "hydro": hydro,
        "roster_geojson": roster_gj,
        "vel_grid": {"vmax": round(vmax, 1), "values": vel_list},
        "images": {
            "monitor_map": render.monitor_map_png(stack, fields, rows, roster_gj),
            "velocity": render.velocity_overlay_png(stack, fields),
        },
    }


def _placeholder_focus(dates):
    return {
        "cluster_id": 0, "basin_name": "—",
        "series": [0.0] * len(dates),
        "screening": {"net_mm": 0.0, "verdict": "low-confidence detection"},
    }


def _tick(v: float):
    v = float(v)
    return int(round(v)) if abs(v - round(v)) < 1e-9 else round(v, 2)


def _nice_step(span: float, want: int = 4) -> float:
    """A round tick step — charts should never show an axis labelled 21.2."""
    if span <= 0:
        return 1.0
    raw = span / max(want, 1)
    mag = 10.0 ** np.floor(np.log10(raw))
    for m in (1.0, 2.0, 2.5, 5.0, 10.0):
        if raw <= m * mag:
            return m * mag
    return 10.0 * mag


def _chart_bounds(focus, noise, leafoff) -> dict:
    s = np.asarray(focus["series"], float)
    lo, hi = float(np.nanmin(s)), float(np.nanmax(s))
    pad = max(2.0, 0.10 * max(hi - lo, 1.0))
    step = _nice_step((hi + pad) - (lo - pad))
    y0 = float(np.floor((lo - pad) / step) * step)
    y1 = float(np.ceil((hi + pad) / step) * step)
    ticks = list(np.arange(y0, y1 + step / 2, step))
    steps = np.abs(np.diff(s))
    onset = int(np.argmax(steps)) + 1 if steps.size and np.nanmax(steps) > 0 else None

    bands, run = [], None
    for i in range(len(noise)):
        if i in leafoff and run is None:
            run = i
        elif i not in leafoff and run is not None:
            bands.append([run, i - 1])
            run = None
    if run is not None:
        bands.append([run, len(noise) - 1])

    nmax = max([n for n in noise if np.isfinite(n)] or [12.0])
    nstep = _nice_step(nmax * 1.15, want=3)
    ny1 = float(np.ceil(nmax * 1.15 / nstep) * nstep)
    nticks = [t for t in np.arange(nstep, ny1 + nstep / 2, nstep)]
    return {
        "disp_label": (f"Cluster {focus['cluster_id']} mean displacement over the record, "
                       f"net {focus['screening']['net_mm']:+.0f} millimetres"),
        "disp_y0": y0, "disp_y1": y1,
        "disp_yticks": [_tick(t) for t in ticks],
        "onset_idx": onset,
        "onset_label": "largest single step",
        "noise_label": ("Stack noise floor per epoch: leaf-off months run about half the "
                        "leaf-on scatter"),
        "noise_y1": ny1,
        "noise_yticks": [_tick(t) for t in nticks],
        "leafoff_bands": bands,
    }


# --------------------------------------------------------------------------
# outputs
# --------------------------------------------------------------------------

def write_state(bulletin, combined, page_data) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "alert_bulletin.json").write_text(json.dumps(bulletin, indent=2) + "\n")
    (STATE / "combined_bulletin.json").write_text(json.dumps(combined, indent=2) + "\n")
    slim = {
        "meta": page_data["meta"],
        "hydro": page_data["hydro"],
        "clusters": [
            {k: c[k] for k in ("cluster_id", "basin_id", "basin_name", "n_pixels",
                               "acres", "centroid_lat", "centroid_lon",
                               "mean_los_velocity_mm_yr", "mean_los_accel_mm_yr2",
                               "coh", "snr", "alert_level", "screening")}
            for c in page_data["clusters"]
        ],
    }
    (STATE / "last_pass.json").write_text(json.dumps(slim, indent=2) + "\n")


def commit_message(meta, clusters) -> str:
    n = sum(1 for c in clusters if c["screening"]["verdict"] == "candidate")
    return (f"slope: {meta['last']} pass — level {meta['combined_level']}, "
            f"{n} candidate{'' if n == 1 else 's'}")


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_check(args) -> int:
    cfg = Config.load()
    led = ledger.load()
    pc, note = cache.load(cfg)
    print(f"cache: {note}")
    results, new = discover(cfg, led)
    print(f"ASF: {len(results)} scenes on the AOI; {len(new)} not in the ledger")
    needs = bool(new) or len(pc) == 0
    if new:
        for r in new[:10]:
            print(f"  new scene {r.properties['startTime'][:10]} "
                  f"{r.properties['sceneName']}")
    out("new_scenes", len(new))
    out("latest_scene", max((r.properties["startTime"][:10] for r in results),
                            default="none"))
    out("needs_run", "true" if needs else "false")
    if not needs:
        print("nothing new since the last processed epoch — exiting quietly")
    return 0


def cmd_run(args) -> int:
    cfg = Config.load()
    led = ledger.load()
    pc, note = cache.load(cfg)
    print(f"cache: {note}")

    bootstrap = args.command == "bootstrap"
    offline = args.command == "rebuild"

    if not offline:
        results, new = discover(cfg, led)
        print(f"ASF: {len(results)} scenes; {len(new)} new")
        if not new and len(pc) and not bootstrap and not args.force:
            out("changed", "false")
            out("level", "")
            print("no new scene — nothing to do")
            return 0

        todo = pairs_to_process(
            cfg, results, led, pc,
            only_new_scenes=None if (bootstrap or not len(pc)) else new,
            limit=args.max_pairs,
        )
        print(f"{len(todo)} pair(s) to process this run")
        added = submit_and_collect(cfg, todo, led, pc, args.budget)
        print(f"{added} pair(s) added to the cache")

        if added:
            cache.save(pc)
        ledger.add_scenes(led, [ledger.scene_record(r) for r in results])
        for key in pc.keys:
            row = next((p for p in led["pairs"]
                        if ledger.pair_key(p["ref"], p["sec"]) == key), None)
            if row:
                row["status"] = "cached"
        ledger.record_run(led, args.command,
                          f"{len(new)} new scenes, {added} pairs cached, "
                          f"{len(pc)} in cache")
        ledger.save(led)

        remaining = pairs_to_process(cfg, results, led, pc)
        if remaining:
            print(f"::notice::{len(remaining)} pair(s) still missing from the cache — "
                  "dispatch this workflow again to continue")
        out("pairs_remaining", len(remaining))

    if len(pc) < 3:
        print("::warning::fewer than 3 cached pairs — no time series yet, pages untouched")
        out("changed", "false")
        return 0

    stack, fields, clusters, bulletin, level = analyse(cfg, pc)
    hydro = hydro_conditions(cfg)
    combined = fuse(bulletin, hydro, cfg)
    (cfg.output_dir / "combined_bulletin.json").write_text(json.dumps(combined, indent=2))
    print(f"\nCOMBINED ALERT LEVEL: {combined['system_alert_level']}")
    for e in combined.get("escalations", []):
        print(f"  ! {e}")

    data = build_page_data(cfg, stack, fields, clusters, bulletin, combined,
                           hydro, n_pairs=len(pc))
    write_state(bulletin, combined, data)

    if args.no_pages:
        print("--no-pages: state written, pages left alone")
        out("changed", "false")
        return 0

    result = render.render_all(data)
    changed = any(v["changed"] for v in result.values())

    meta = data["meta"]
    n_cand = sum(1 for c in data["clusters"]
                 if c["screening"]["verdict"] == "candidate")
    out("changed", "true" if changed else "false")
    out("level", meta["combined_level"])
    out("insar_level", meta["insar_level"])
    out("hydro_state", meta["hydro_state"] or "unavailable")
    out("candidates", n_cand)
    out("latest_pass", meta["last"])
    out("commit_message", commit_message(meta, data["clusters"]))
    out("notify", "true" if meta["combined_level"] in ("WATCH", "WARNING") else "false")

    summary(
        f"### Slope monitor — {meta['last']} pass\n\n"
        f"- combined level: **{meta['combined_level']}** "
        f"(InSAR {meta['insar_level']}, hydro {meta['hydro_state'] or 'unavailable'})\n"
        f"- {len(data['clusters'])} flagged clusters; {n_cand} screened as candidates "
        f"_({REVIEW_LABEL})_\n"
        f"- {meta['epochs']} epochs, {len(pc)} cached pairs, "
        f"{meta['usable_pct']}% usable ground\n"
    )
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["check", "update", "bootstrap", "rebuild"])
    p.add_argument("--force", action="store_true",
                   help="run the full pass even if no new scene was found")
    p.add_argument("--max-pairs", type=int, default=None,
                   help="cap pairs submitted this run (bootstrap in chunks)")
    p.add_argument("--budget", type=int, default=DEFAULT_BUDGET_S,
                   help="seconds to wait on HyP3 before collecting what finished")
    p.add_argument("--no-pages", action="store_true",
                   help="write state only; do not touch the published pages")
    args = p.parse_args(argv)

    started = datetime.now(timezone.utc)
    try:
        rc = cmd_check(args) if args.command == "check" else cmd_run(args)
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    print(f"\n{args.command} finished in "
          f"{(datetime.now(timezone.utc) - started).total_seconds():.0f}s")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
