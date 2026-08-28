"""Submit InSAR pairs to ASF HyP3 (free on-demand processing) and download results.

HyP3 turns each Sentinel-1 pair into a geocoded interferogram + LOS displacement
GeoTIFF + coherence map, so you never need to run ISCE/GAMMA yourself.
Quota: ~10,000 credits/month per Earthdata account (1 credit ≈ 1 INSAR_GAMMA job)
— far more than this watershed needs (~6 jobs/month).

Requires: pip install hyp3_sdk ; EARTHDATA_USERNAME / EARTHDATA_PASSWORD env vars
(or a ~/.netrc for urs.earthdata.nasa.gov).

Usage:
    python -m src.process_insar submit     # find pairs, submit jobs
    python -m src.process_insar download   # fetch finished products into data/
"""
from __future__ import annotations

import sys
from pathlib import Path

from .config import ROOT, Config, earthdata_credentials
from .discover import build_pairs, find_scenes

DATA_DIR = ROOT / "data"


def _hyp3():
    import hyp3_sdk

    creds = earthdata_credentials()
    if creds:
        return hyp3_sdk.HyP3(username=creds[0], password=creds[1])
    return hyp3_sdk.HyP3()  # falls back to ~/.netrc


def submit(cfg: Config):
    hyp3 = _hyp3()
    results = find_scenes(cfg)
    pairs = build_pairs(results, cfg["sentinel1"]["max_temporal_baseline_days"])
    h = cfg["hyp3"]
    jobs = []
    for ref, sec in pairs:
        jobs.append(
            hyp3.submit_insar_job(
                granule1=ref.properties["sceneName"],
                granule2=sec.properties["sceneName"],
                name="cullowhee-lews",
                looks=h["looks"],
                include_displacement_maps=h["include_displacement_maps"],
                include_dem=h["include_dem"],
                apply_water_mask=h["water_mask"],
            )
        )
    print(f"Submitted {len(jobs)} InSAR jobs (name=cullowhee-lews).")
    print("They typically finish in under an hour; then run: python -m src.process_insar download")


def download(cfg: Config):
    import zipfile

    hyp3 = _hyp3()
    DATA_DIR.mkdir(exist_ok=True)
    batch = hyp3.find_jobs(name="cullowhee-lews")
    if not batch:
        print("No jobs named 'cullowhee-lews' found — run submit first.")
        return
    batch = hyp3.watch(batch)  # wait for running jobs
    succeeded = batch.filter_jobs(succeeded=True, running=False, failed=False)
    print(f"{len(succeeded)} of {len(batch)} jobs succeeded.")
    files = []
    skipped = expired = 0
    for job in succeeded:
        try:
            if job.expired():
                expired += 1
                continue
            fname = job.files[0]["filename"] if job.files else None
            if fname and ((DATA_DIR / fname).exists() or (DATA_DIR / fname.replace(".zip", "")).exists()):
                skipped += 1
                continue
            files.extend(job.download_files(location=DATA_DIR))
        except Exception as e:
            print(f"  skipping one job ({type(e).__name__}: {e})")
    for z in files:
        target = DATA_DIR / z.stem
        if not target.exists():
            print("unzipping", z.name)
            zipfile.ZipFile(z).extractall(DATA_DIR)
    print(f"Downloaded {len(files)} new products to {DATA_DIR} "
          f"({skipped} already on disk, {expired} expired on HyP3)")
    print("Next: python -m src.run_operational  (or double-click 4-analyze.bat)")


if __name__ == "__main__":
    cfg = Config.load()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "submit"
    {"submit": submit, "download": download}[cmd](cfg)
