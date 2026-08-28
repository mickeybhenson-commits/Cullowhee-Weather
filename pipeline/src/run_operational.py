"""Run the LEWS on real downloaded HyP3 data.

    python -m src.run_operational

Loads the displacement rasters from data/, runs SBAS + detection + forecasting,
writes the alert bulletin, and saves the full field stack to
outputs/real_fields.npz for mapping/dashboarding.
"""
from __future__ import annotations

import numpy as np

from .alert import escalate, print_bulletin, write_bulletin
from .config import ROOT, Config
from .detect import detect
from .stack import load_hyp3_stack


def main():
    cfg = Config.load()
    stack = load_hyp3_stack(ROOT / "data", cfg)
    print(f"\nStack ready: {stack.shape[0]} epochs, grid {stack.shape[1]}x{stack.shape[2]}")
    usable_pct = 100.0 * np.mean(stack.coherence >= cfg.analysis["coherence_threshold"])
    print(f"Pixels above coherence threshold: {usable_pct:.0f}% "
          "(low is normal over forest — winter scenes improve it)")

    fields, clusters = detect(stack, cfg)
    level = escalate(stack, clusters, cfg)
    bulletin = write_bulletin(stack, clusters, level, cfg)
    # mark this bulletin as real data (the demo one is simulated)
    import json
    bulletin["data_source"] = "Sentinel-1 / ASF HyP3 (real)"
    (cfg.output_dir / "alert_bulletin.json").write_text(json.dumps(bulletin, indent=2))
    print_bulletin(bulletin)

    np.savez_compressed(
        cfg.output_dir / "real_fields.npz",
        velocity=fields["velocity"], accel=fields["accel"],
        se_velocity=fields["se_velocity"], rmse=fields["rmse"],
        coherence=stack.coherence, dem=stack.dem, slope=stack.slope,
        disp=stack.disp, dates=[d.isoformat() for d in stack.dates],
        usable=fields["usable"], hot=fields["hot"],
        cluster_masks=(np.array([c.mask for c in clusters])
                       if clusters else np.zeros((0,) + stack.shape[1:], bool)),
        cluster_levels=[c.level for c in clusters],
    )
    print(f"\nFields saved to {cfg.output_dir / 'real_fields.npz'}")
    print("Share the cullowhee-lews folder with Claude to map and dashboard these results.")


if __name__ == "__main__":
    main()
