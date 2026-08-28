"""Regenerate the three published pages in place.

The pages are hand-designed and stay that way. This module never rewrites one
from a template; it swaps the *data-dependent* regions of the file that is
already in the repo and leaves every other byte alone.

Each region is delimited by marker comments:

    <!--SLOPE:DATA-->  ...generated...  <!--/SLOPE:DATA-->

On the first rebuild the markers do not exist yet, so each region also carries
an anchor pattern that matches the hand-written block it replaces; the
generated text goes in wrapped in fresh markers. Every run after that is a
plain substitution between markers, and a region whose anchor stops matching
raises instead of silently leaving stale numbers on a live page.

Everything written here is metric-derived. The analyst-voice sentences that
used to sit in these regions ("seasonal artifact", "none is a credible
landslide yet") are replaced by the screening text from verdicts.py, stamped
"automated screening — pending analyst review".
"""
from __future__ import annotations

import base64
import html as htmllib
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .config import ROOT
from .verdicts import REVIEW_LABEL

REPO = ROOT.parent

MONITOR = REPO / "slope_monitor.html"
MAP = REPO / "slope_map.html"
THREED = REPO / "slope_3d.html"

# Page palette, lifted from the pages' own :root block so the generated
# matplotlib images sit in the same design.
ABYSS = "#0a161a"
MIST = "#e9f1ef"
SLATE = "#8aa6a6"
FAINT = "#5c7576"
CURRENT = "#45d0c0"
WATCH = "#e2b52b"
WARNING = "#f2882d"
LINE = "#20363c"

PIXEL_ACRES = 80.0 * 80.0 / 4046.86      # one ~80 m analysis cell


class RegionError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# marker machinery
# --------------------------------------------------------------------------

def _open(name: str) -> str:
    return f"<!--SLOPE:{name}-->"


def _close(name: str) -> str:
    return f"<!--/SLOPE:{name}-->"


def replace_region(html: str, name: str, body: str, anchor: str) -> tuple[str, bool]:
    """Swap the region `name` for `body`. Returns (html, wrapped_this_run).

    Bodies are normalised to sit on their own lines between the markers, so
    adjacent regions never fight over the newline that separates them — the
    bug that made the first rebuild of slope_monitor.html eat its own anchors.
    """
    body = body.lstrip("\n")
    if not body.endswith("\n"):
        body += "\n"
    o, c = _open(name), _close(name)
    if o in html and c in html:
        pat = re.compile(re.escape(o) + ".*?" + re.escape(c), re.S)
        return pat.sub(lambda _: o + "\n" + body + c, html, count=1), False

    m = re.search(anchor, html, re.S)
    if not m:
        raise RegionError(
            f"region {name}: no markers and the first-rebuild anchor did not "
            f"match. The page's scaffolding changed shape — fix the anchor in "
            f"render.py rather than letting stale numbers stand."
        )
    tail = "" if html[m.end() - 1:m.end()] == "\n" else "\n"
    return (html[: m.start()] + o + "\n" + body + c + "\n" + tail
            + html[m.end():]), True


def apply_regions(path: Path, regions: list[tuple[str, str, str]]) -> tuple[bool, list[str]]:
    """Apply (name, body, anchor) triples to a page. Returns (changed, wrapped)."""
    before = path.read_text(encoding="utf-8")
    html = before
    wrapped = []
    for name, body, anchor in regions:
        html, first = replace_region(html, name, body, anchor)
        if first:
            wrapped.append(name)
    if html != before:
        path.write_text(html, encoding="utf-8")
    return html != before, wrapped


# --------------------------------------------------------------------------
# images (matplotlib, dark, matching the pages)
# --------------------------------------------------------------------------

def _hillshade(dem: np.ndarray, pixel_m: float = 80.0,
               az: float = 315.0, alt: float = 45.0) -> np.ndarray:
    gy, gx = np.gradient(np.nan_to_num(dem, nan=float(np.nanmedian(dem))), pixel_m)
    slope = np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)
    az_r, alt_r = np.radians(360.0 - az + 90.0), np.radians(alt)
    hs = (np.sin(alt_r) * np.cos(slope)
          + np.cos(alt_r) * np.sin(slope) * np.cos(az_r - aspect))
    return np.clip(hs, 0, 1)


def _vmax(velocity: np.ndarray, significant: np.ndarray) -> float:
    v = np.abs(velocity[significant & np.isfinite(velocity)])
    if v.size == 0:
        return 50.0
    return float(max(25.0, np.nanpercentile(v, 98)))


def monitor_map_png(stack, fields, clusters, roster_gj) -> str:
    """The wide overview map on slope_monitor.html — base64 PNG."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    min_lon, min_lat, max_lon, max_lat = stack.transform
    extent = (min_lon, max_lon, min_lat, max_lat)
    sig = fields["usable"] & (np.abs(fields["velocity"]) > 3.0 * fields["se_velocity"])
    vmax = _vmax(fields["velocity"], sig)

    fig, ax = plt.subplots(figsize=(9.54, 6.53), dpi=100)
    fig.patch.set_facecolor(ABYSS)
    ax.set_facecolor(ABYSS)

    ax.imshow(_hillshade(stack.dem), extent=extent, cmap="gray",
              vmin=0, vmax=1.15, interpolation="bilinear", zorder=1)
    masked = np.where(sig, fields["velocity"], np.nan)
    im = ax.imshow(masked, extent=extent, cmap="RdBu_r", vmin=-vmax, vmax=vmax,
                   interpolation="nearest", alpha=0.85, zorder=2)

    for f in roster_gj.get("features", []):
        for ring in _geom_rings(f["geometry"]):
            xs = [p[0] for p in ring]
            ys = [p[1] for p in ring]
            ax.plot(xs, ys, color=CURRENT, lw=0.9, alpha=0.85, zorder=3)

    for c in clusters:
        colour = WARNING if c["screening"]["verdict"] == "candidate" else WATCH
        lo0, la0, lo1, la1 = c["bbox_lonlat"]
        ax.add_patch(plt.Rectangle((lo0, la0), lo1 - lo0, la1 - la0, fill=False,
                                   edgecolor=colour, lw=1.6, zorder=4))
        ax.text(lo1 + 0.0012, la1 + 0.0008, str(c["cluster_id"]), color=colour,
                fontsize=8.5, zorder=5)

    ax.set_xlim(min_lon, max_lon)
    ax.set_ylim(min_lat, max_lat)
    ax.set_xlabel("Longitude", color=SLATE, fontsize=10)
    ax.set_ylabel("Latitude", color=SLATE, fontsize=10)
    ax.tick_params(colors=SLATE, labelsize=8.5)
    for s in ax.spines.values():
        s.set_color(LINE)

    cb = fig.colorbar(im, ax=ax, fraction=0.036, pad=0.02)
    cb.set_label("LOS velocity (mm/yr) — positive = toward satellite",
                 color=SLATE, fontsize=9)
    cb.ax.tick_params(colors=SLATE, labelsize=8)
    cb.outline.set_edgecolor(LINE)

    return _png_b64(fig)


def velocity_overlay_png(stack, fields, scale: int = 3) -> str:
    """Transparent RdBu_r velocity wash for the Leaflet map — base64 PNG."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import colors

    sig = fields["usable"] & (np.abs(fields["velocity"]) > 3.0 * fields["se_velocity"])
    vmax = _vmax(fields["velocity"], sig)
    norm = colors.Normalize(-vmax, vmax)
    rgba = matplotlib.colormaps["RdBu_r"](norm(np.where(sig, fields["velocity"], 0.0)))
    rgba[..., 3] = np.where(sig, 0.95, 0.0)
    big = np.repeat(np.repeat(rgba, scale, axis=0), scale, axis=1)

    H, W = big.shape[:2]
    fig = plt.figure(figsize=(W / 100, H / 100), dpi=100)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_axis_off()
    ax.imshow(big, interpolation="nearest")
    return _png_b64(fig, transparent=True)


def _png_b64(fig, transparent: bool = False) -> str:
    import matplotlib.pyplot as plt

    buf = io.BytesIO()
    fig.savefig(buf, format="png",
                facecolor="none" if transparent else fig.get_facecolor(),
                transparent=transparent, bbox_inches="tight" if not transparent else None,
                pad_inches=0.12 if not transparent else 0)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _geom_rings(geom) -> list[list]:
    if geom["type"] == "Polygon":
        return geom["coordinates"]
    if geom["type"] == "MultiPolygon":
        return [ring for poly in geom["coordinates"] for ring in poly]
    return []


# --------------------------------------------------------------------------
# geometry helpers
# --------------------------------------------------------------------------

def mask_multipolygon(mask: np.ndarray, transform) -> list:
    """One lon/lat square per masked pixel — the shape the map page expects."""
    min_lon, min_lat, max_lon, max_lat = transform
    H, W = mask.shape
    dlon = (max_lon - min_lon) / W
    dlat = (max_lat - min_lat) / H
    polys = []
    for r, c in zip(*np.where(mask)):
        lo = min_lon + c * dlon
        la = max_lat - (r + 1) * dlat
        polys.append([[
            [round(lo, 8), round(la, 8)],
            [round(lo, 8), round(la + dlat, 8)],
            [round(lo + dlon, 8), round(la + dlat, 8)],
            [round(lo + dlon, 8), round(la, 8)],
            [round(lo, 8), round(la, 8)],
        ]])
    return polys


def mask_bbox_lonlat(mask: np.ndarray, transform) -> tuple[float, float, float, float]:
    min_lon, min_lat, max_lon, max_lat = transform
    H, W = mask.shape
    rows, cols = np.where(mask)
    dlon = (max_lon - min_lon) / W
    dlat = (max_lat - min_lat) / H
    return (
        min_lon + cols.min() * dlon,
        max_lat - (rows.max() + 1) * dlat,
        min_lon + (cols.max() + 1) * dlon,
        max_lat - rows.min() * dlat,
    )


# --------------------------------------------------------------------------
# text helpers — all metric, none in analyst voice
# --------------------------------------------------------------------------

LEVEL_GLYPH = {"NORMAL": "○", "ADVISORY": "◇", "WATCH": "◆", "WARNING": "▲"}
LEVEL_VAR = {"NORMAL": "var(--current)", "ADVISORY": "var(--current)",
             "WATCH": "var(--watch)", "WARNING": "var(--warning)"}


def _esc(s) -> str:
    return htmllib.escape(str(s), quote=False)


def _fmt_date(iso: str) -> str:
    d = datetime.strptime(iso, "%Y-%m-%d")
    return d.strftime("%B %-d, %Y") if hasattr(d, "strftime") else iso


def posture_paragraph(meta: dict, clusters: list[dict], hydro: dict | None) -> str:
    n_cand = sum(1 for c in clusters if c["screening"]["verdict"] == "candidate")
    n_susp = sum(1 for c in clusters if c["screening"]["verdict"] == "suspect artifact")
    n_low = len(clusters) - n_cand - n_susp

    bits = [
        f"The detector flagged <strong>{len(clusters)} clusters</strong> "
        f"(of {meta['n_clusters']} candidate regions) in the "
        f"{_fmt_date(meta['last'])} pass."
    ]
    bits.append(
        f"Automated screening reads <strong>{n_cand} candidate"
        f"{'' if n_cand == 1 else 's'}</strong>, {n_susp} suspect artifact"
        f"{'' if n_susp == 1 else 's'} and {n_low} low-confidence detection"
        f"{'' if n_low == 1 else 's'} — <em>{REVIEW_LABEL}</em>, "
        "not an analyst's call."
    )
    if n_cand:
        named = [
            f"<strong>cluster {c['cluster_id']}</strong> "
            f"({c['screening']['net_mm']:+.0f} mm net) in "
            f"<strong>{_esc(c['basin_name'])}"
            f"{'' if c['basin_id'] == 'outside roster' else ' (' + c['basin_id'] + ')'}</strong>"
            for c in clusters if c["screening"]["verdict"] == "candidate"
        ]
        bits.append("Screened as candidates: " + "; ".join(named) + ".")
    if hydro:
        state = hydro["state"]
        if state in ("PRIMED", "PRIMED_SEVERE"):
            bits.append(f"Hydrologic conditions are <strong>{state}</strong> — "
                        "every cluster is escalated one level.")
        else:
            bits.append(f"Hydrologic conditions are {state}, so no hydrologic "
                        "escalation applies.")
    else:
        bits.append("The hydrologic layer was unavailable this run, so the "
                    "InSAR posture passes through unconditioned.")
    return " ".join(bits)


def _tile(k: str, v: str, colour: str | None = None, sub: str | None = None) -> str:
    style = f' style="color:{colour}"' if colour else ""
    small = f' <small>{_esc(sub)}</small>' if sub else ""
    return (f'<div class="tile"><div class="k">{_esc(k)}</div>'
            f'<div class="v"{style}>{_esc(v)}{small}</div></div>')


# --------------------------------------------------------------------------
# slope_monitor.html
# --------------------------------------------------------------------------

def render_monitor(d: dict) -> tuple[bool, list[str]]:
    meta, clusters, hydro = d["meta"], d["clusters"], d["hydro"]
    level = meta["combined_level"]
    last = datetime.strptime(meta["last"], "%Y-%m-%d")

    posture = (
        f'\n    <div class="lvl" style="color:{LEVEL_VAR[level]}">'
        f'{LEVEL_GLYPH[level]} {level}</div>\n'
        f'    <p>{posture_paragraph(meta, clusters, hydro)}</p>\n  '
    )

    tiles = "\n    " + "\n    ".join([
        _tile("Slope posture", level, LEVEL_VAR[level]),
        _tile("Hydro state", hydro["state"] if hydro else "unavailable", CURRENT),
        _tile("Latest pass", last.strftime("%b %-d"), None, last.strftime("%Y")),
        _tile("Record", str(meta["epochs"]), None,
              f"scenes / {meta['span_months']} mo"),
        _tile("Usable ground", f"{meta['usable_pct']}%", None, "coh ≥ "
              f"{meta['coherence_threshold']}"),
    ]) + "\n  "

    mapcard = (
        '\n    <img src="data:image/png;base64,' + d["images"]["monitor_map"] + '">\n'
        f'    <div class="caption">grid {meta["grid_h"]}×{meta["grid_w"]} @ ~80 m · '
        f'{meta["epochs"]} epochs {_fmt_date(meta["first"])} – {_fmt_date(meta["last"])} · '
        'SBAS inversion, reference-corrected, atmosphere-filtered · '
        'positive = motion toward the satellite</div>\n  '
    )

    heading = (
        "\n  <h2>Flagged clusters — automated screening</h2>\n"
        '  <p class="sect-note">Screened from the numbers alone: net motion, '
        'direction agreement over the last eight steps, and whether the motion '
        'survives the leaf-off months. No analyst has reviewed this pass — every '
        f'row below is <em>{REVIEW_LABEL}</em>.</p>\n'
    )

    focus = d["focus"]
    charts = (
        '\n    <div class="card">\n'
        f'      <p class="chart-title">Cluster {focus["cluster_id"]} — displacement</p>\n'
        f'      <p class="chart-sub">cluster mean, mm · {_esc(focus["basin_name"])} · '
        f'{focus["screening"]["net_mm"]:+.0f} mm net over the record</p>\n'
        '      <div class="chart-box" id="chart-disp"></div>\n'
        '    </div>\n'
        '    <div class="card">\n'
        '      <p class="chart-title">Stack noise floor by epoch</p>\n'
        f'      <p class="chart-sub">spatial scatter, mm · leaf-off median '
        f'{meta["noise_leafoff"]} mm vs leaf-on {meta["noise_leafon"]} mm</p>\n'
        '      <div class="chart-box" id="chart-noise"></div>\n'
        '    </div>\n  '
    )

    prose = _prose(meta, clusters, hydro)

    payload = {
        "dates": d["dates"],
        "t_days": d["t_days"],
        "noise_mm": d["noise_mm"],
        "focus_mm": focus["series"],
        "watch": [_monitor_row(c) for c in clusters],
        "hydro": hydro,
        "meta": meta,
        "chart": d["chart"],
        "review": REVIEW_LABEL,
    }
    data_js = "\n  const DATA = " + json.dumps(payload) + ";\n"

    verdict_js = "\n  const VERDICT = " + json.dumps(
        {str(c["cluster_id"]): c["screening"]["reason"] for c in clusters},
        indent=2
    ).replace("\n", "\n  ") + ";\n"

    rows_js = '''
  document.getElementById("cluster-rows").innerHTML = DATA.watch.map(c => `
    <tr>
      <td>${c.cluster_id}</td>
      <td class="basin-tag">${c.basin_id === "outside roster" ? "outside watershed" : c.basin_id}</td>
      <td>${c.n_pixels}</td>
      <td>${c.centroid_lat.toFixed(4)}</td><td>${c.centroid_lon.toFixed(4)}</td>
      <td>${c.mean_los_velocity_mm_yr.toFixed(1)}</td><td>${c.coh.toFixed(2)}</td>
      <td class="verdict-cell${c.verdict === "candidate" ? " candidate" : ""}">${c.verdict.toUpperCase()} — ${VERDICT[c.cluster_id]}<br><span style="color:var(--faint);font-size:12px">${DATA.review}</span></td>
    </tr>`).join("");
'''

    chartcfg = _chart_config()

    return apply_regions(MONITOR, [
        ("POSTURE", posture,
         r'  <div class="verdict">\n.*?\n  </div>\n'),
        ("TILES", tiles,
         r'  <div class="tiles">\n.*?\n  </div>\n'),
        ("MAPCARD", mapcard,
         r'  <div class="card map-card">\n.*?\n  </div>\n'),
        ("CLUSTERHEAD", heading,
         r'  <h2>Flagged clusters.*?</h2>\n  <p class="sect-note">.*?</p>\n'),
        ("CHARTS", charts,
         r'  <div class="charts">\n.*?\n  </div>\n(?=\n  <h2>)'),
        ("PROSE", prose,
         r'  <div class="prose">\n.*?\n  </div>\n'),
        ("DATA", data_js, r'  const DATA = .*?;\n'),
        ("VERDICT", verdict_js, r'  const VERDICT = \{.*?\n  \};\n'),
        ("ROWS", rows_js,
         r'  document\.getElementById\("cluster-rows"\)\.innerHTML = .*?\.join\(""\);\n'),
        ("CHARTCFG", chartcfg,
         r'  chart\("chart-disp", \{.*?\n  \}\);\n\n  chart\("chart-noise", \{.*?\n  \}\);\n'),
    ])


def _monitor_row(c: dict) -> dict:
    return {
        "cluster_id": c["cluster_id"],
        "n_pixels": c["n_pixels"],
        "centroid_lat": c["centroid_lat"],
        "centroid_lon": c["centroid_lon"],
        "mean_los_velocity_mm_yr": c["mean_los_velocity_mm_yr"],
        "mean_los_accel_mm_yr2": c["mean_los_accel_mm_yr2"],
        "mean_slope_deg": c["mean_slope_deg"],
        "coh": c["coh"],
        "basin_id": c["basin_id"],
        "basin_name": c["basin_name"],
        "alert_level": c["alert_level"],
        "verdict": c["screening"]["verdict"],
    }


def _prose(meta: dict, clusters: list[dict], hydro: dict | None) -> str:
    n_cand = sum(1 for c in clusters if c["screening"]["verdict"] == "candidate")
    leaf_off = meta["leafoff_epochs"]
    first_para = (
        "<strong>This layer answers a different question on a different clock.</strong> NOAH and SKYE\n"
        "      warn in minutes and hours about rising water. The slope monitor works in weeks: it finds the\n"
        "      hillside already losing its grip, so instruments and eyes get there before the storm does.\n"
        "      It updates with each satellite pass (~12 days) and is cloud-processed — in an outage its\n"
        "      posture is <em>last known</em>, not live."
    )
    if n_cand == 0:
        second = ("<strong>Nothing cleared the candidate bar this pass.</strong> Every flagged cluster "
                  "failed at least one of the three screening tests — 25 mm of net motion, 85% direction "
                  "agreement over the last eight steps, and motion surviving the leaf-off months. That is "
                  "a screening result, not a clean bill of health: the tests are deliberately strict, and "
                  "a slope that has only just started moving will read low-confidence for several passes.")
    else:
        named = ", ".join(
            f"cluster {c['cluster_id']} ({_esc(c['basin_name'])})"
            for c in clusters if c["screening"]["verdict"] == "candidate"
        )
        second = (f"<strong>{n_cand} cluster{'' if n_cand == 1 else 's'} cleared the candidate bar:</strong> "
                  f"{named}. Clearing it means 25 mm or more of net motion, at least 85% of the last eight "
                  "steps pointing the same way, and — where the record covers a winter — motion that did not "
                  "stop when the leaves came down. That is a screening result awaiting analyst review, not a "
                  "confirmed landslide.")
    third = (
        f"<strong>Winter is the sharp season.</strong> The record holds {leaf_off} leaf-off "
        f"(Nov–Feb) epoch{'' if leaf_off == 1 else 's'}, where the noise floor "
        f"({meta['noise_leafoff']} mm) runs well under the leaf-on floor "
        f"({meta['noise_leafon']} mm). Calls that are marginal in summer become reliable there, which "
        "is why the screening asks every candidate to show its motion in those months."
    )
    fourth = (
        "<strong>Forecast dates, when they come, are extrapolations.</strong> A slope that enters\n"
        "      terminal creep gets an inverse-velocity failure window — guidance for prioritizing ground\n"
        "      inspection, never for timing an evacuation."
    )
    return ("\n    <p>" + first_para + "</p>\n    <p>" + second + "</p>\n    <p>"
            + third + "</p>\n    <p>" + fourth + "</p>\n  ")


def _chart_config() -> str:
    """Chart calls driven entirely by DATA.chart — written once, never again."""
    return '''
  chart("chart-disp", {
    label: DATA.chart.disp_label,
    x0: t[0], x1: t[t.length-1], y0: DATA.chart.disp_y0, y1: DATA.chart.disp_y1,
    yticks: DATA.chart.disp_yticks, xticks: xt, baseline: 0,
    series: DATA.focus_mm, unit: "mm",
    draw(svg, X, Y) {
      const s = DATA.focus_mm;
      const path = t.map((x,i) => (i ? "L" : "M") + X(x).toFixed(1) + " " + Y(s[i]).toFixed(1)).join("");
      svg.appendChild(mk("path", { d: path, fill: "none", stroke: C.current, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
      svg.appendChild(mk("circle", { cx: X(t[t.length-1]), cy: Y(s[s.length-1]), r: 4, fill: C.current, stroke: C.shoal, "stroke-width": 2 }));
      const on = DATA.chart.onset_idx;
      if (on !== null) {
        svg.appendChild(mk("line", { x1: X(t[on]), x2: X(t[on]), y1: Y(DATA.chart.disp_y1), y2: Y(DATA.chart.disp_y0), stroke: C.watch, "stroke-width": 1, "stroke-dasharray": "3 4" }));
        const lab = mk("text", { x: X(t[on]) - 6, y: Y(DATA.chart.disp_y1) + 14, "text-anchor": "end", "font-size": 10, fill: C.watch, "font-family": "JetBrains Mono, monospace" });
        lab.textContent = DATA.chart.onset_label; svg.appendChild(lab);
      }
    }
  });

  chart("chart-noise", {
    label: DATA.chart.noise_label,
    x0: t[0], x1: t[t.length-1], y0: 0, y1: DATA.chart.noise_y1,
    yticks: DATA.chart.noise_yticks, xticks: xt, baseline: 0,
    series: DATA.noise_mm, unit: "mm",
    pre(svg, X, Y) {
      DATA.chart.leafoff_bands.forEach(b => {
        svg.appendChild(mk("rect", { x: X(t[b[0]]), y: Y(DATA.chart.noise_y1), width: Math.max(2, X(t[b[1]]) - X(t[b[0]])), height: Y(0) - Y(DATA.chart.noise_y1), fill: "rgba(69,208,192,.07)" }));
      });
    },
    draw(svg, X, Y) {
      const s = DATA.noise_mm;
      const path = t.map((x,i) => (i ? "L" : "M") + X(x).toFixed(1) + " " + Y(s[i]).toFixed(1)).join("");
      svg.appendChild(mk("path", { d: path, fill: "none", stroke: C.current, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
      DATA.chart.leafoff_bands.forEach(b => {
        const mid = Math.floor((b[0] + b[1]) / 2);
        const lab = mk("text", { x: X(t[mid]), y: Y(DATA.chart.noise_y1) + 12, "text-anchor": "middle", "font-size": 10, fill: C.current, "font-family": "JetBrains Mono, monospace" });
        lab.textContent = "leaf-off"; svg.appendChild(lab);
      });
      s.forEach((v,i) => svg.appendChild(mk("circle", { cx: X(t[i]), cy: Y(v), r: 2.4, fill: C.current })));
    }
  });
'''


# --------------------------------------------------------------------------
# slope_map.html
# --------------------------------------------------------------------------

def render_map(d: dict) -> tuple[bool, list[str]]:
    meta = d["meta"]
    features = []
    series = {}
    for c in d["clusters"]:
        features.append({
            "type": "Feature",
            "geometry": {"type": "MultiPolygon", "coordinates": c["multipolygon"]},
            "properties": {
                "id": c["cluster_id"],
                "cls": _layer_of(c),
                "style": c["screening"]["style"],
                "basin": c["basin_id"],
                "basin_name": c["basin_name"],
                "px": c["n_pixels"],
                "acres": c["acres"],
                "v": c["mean_los_velocity_mm_yr"],
                "net": c["screening"]["net_mm"],
                "slope": c["mean_slope_deg"],
                "coh": c["coh"],
                "agree8": c["screening"]["agree8"],
                "snr": c["snr"],
                "level": c["alert_level"],
            },
        })
        series[str(c["cluster_id"])] = c["series"]

    bbox = meta["bbox"]
    bounds = [[bbox[1], bbox[0]], [bbox[3], bbox[2]]]

    panel_sub = (
        f'\n  <p class="sub">Sentinel-1 InSAR · {meta["epochs"]} scenes, '
        f'{_fmt_date(meta["first"])} – {_fmt_date(meta["last"])} · '
        'click any outlined area for its motion history</p>\n'
    )
    panel_foot = (
        '\n  <div class="foot">Red/blue wash = LOS velocity where statistically significant '
        '(red toward satellite, blue away). Cluster classes are '
        f'<b>{REVIEW_LABEL}</b> — computed from net motion, direction agreement and '
        'leaf-off persistence, not from an analyst\'s reading.<br>\n'
        '  <a href="slope_monitor.html">← full slope monitor</a></div>\n'
    )

    return apply_regions(MAP, [
        ("PANELSUB", panel_sub, r'  <p class="sub">.*?</p>\n'),
        ("PANELFOOT", panel_foot, r'  <div class="foot">Red/blue wash.*?</div>\n'),
        ("CLUSTERS",
         "\nvar CLUSTERS = " + json.dumps(
             {"type": "FeatureCollection", "features": features}) + ";\n",
         r'var CLUSTERS = .*?;\n'),
        ("TS",
         "\nvar TS = " + json.dumps({"dates": d["dates"], "series": series}) + ";\n",
         r'var TS = .*?;\n'),
        ("VELURI",
         '\nvar VEL_URI = "data:image/png;base64,' + d["images"]["velocity"] + '";\n',
         r'var VEL_URI = ".*?";\n'),
        ("SUBBASINS",
         "\nvar SUBBASINS = " + json.dumps(d["roster_geojson"]) + "\n;\n",
         r'var SUBBASINS = .*?\n;\n'),
        ("BOUNDS", "\nvar BOUNDS = " + json.dumps(bounds) + ";\n",
         r'var BOUNDS = .*?;\n'),
        ("VERDICTMAP",
         "\nvar REVIEW = " + json.dumps(REVIEW_LABEL) + ";\nvar VERDICT = "
         + json.dumps({str(c["cluster_id"]): c["screening"]["reason"]
                       for c in d["clusters"]}, indent=1) + ";\n",
         r'var VERDICT = \{.*?\n\};\n'),
        ("CLSOF", "\nfunction clsOf(p){ return p.style; }\n",
         r'function clsOf\(p\)\{.*?\}\n'),
        ("POPUPVERDICT",
         "\n  h+='<p class=\"verdict\">'+(VERDICT[p.id]||'')"
         "+'<br><span style=\"color:var(--faint)\">'+REVIEW+'</span></p></div>';\n",
         r"  h\+='<p class=\"verdict\">'\+\(VERDICT\[p\.id\]\|\|.*?</div>';\n"),
    ])


def _layer_of(c: dict) -> str:
    v = c["screening"]["verdict"]
    return {"candidate": "candidate", "suspect artifact": "suspect"}.get(v, "low")


# --------------------------------------------------------------------------
# slope_3d.html
# --------------------------------------------------------------------------

def render_3d(d: dict) -> tuple[bool, list[str]]:
    if not THREED.exists():
        print(f"::warning::{THREED.name} is not in the repo — skipping it. "
              "Nothing is fabricated in its place.")
        return False, []

    meta = d["meta"]
    payload = {
        "bbox": meta["bbox"],
        "grid": [meta["grid_h"], meta["grid_w"]],
        "vmax": d["vel_grid"]["vmax"],
        "vel": d["vel_grid"]["values"],          # row-major, null where not significant
        "dates": d["dates"],
        "clusters": [
            {
                "id": c["cluster_id"],
                "lon": c["centroid_lon"],
                "lat": c["centroid_lat"],
                "basin": c["basin_id"],
                "basin_name": c["basin_name"],
                "acres": c["acres"],
                "v": c["mean_los_velocity_mm_yr"],
                "net": c["screening"]["net_mm"],
                "style": c["screening"]["style"],
                "verdict": c["screening"]["verdict"],
                "reason": c["screening"]["reason"],
                "level": c["alert_level"],
                "series": c["series"],
            }
            for c in d["clusters"]
        ],
        "meta": meta,
        "hydro": d["hydro"],
        "review": REVIEW_LABEL,
    }
    data_js = "\n<script>window.__SLOPE__=" + json.dumps(payload) + ";</script>\n"

    level = meta["combined_level"]
    n_cand = sum(1 for c in d["clusters"]
                 if c["screening"]["verdict"] == "candidate")
    header = (
        '<div id="hdr">\n'
        '  <p class="eyebrow">Sentinel-1 InSAR · path 48 ascending · '
        f'{_fmt_date(meta["last"])} pass</p>\n'
        '  <h1>Slope Motion in 3D</h1>\n'
        f'  <p>{meta["epochs"]} epochs draped on the QL1 lidar terrain. Posture '
        f'<b>{level}</b> · {len(d["clusters"])} flagged clusters, {n_cand} '
        f'screened as candidate{"" if n_cand == 1 else "s"} — {REVIEW_LABEL}.</p>\n'
        '</div>'
    )

    return apply_regions(THREED, [
        ("DATA3D", data_js, r'<script>window\.__SLOPE__=.*?</script>\n'),
        ("HEADER3D", header, r'<div id="hdr">.*?</div>'),
    ])


# --------------------------------------------------------------------------

def render_all(d: dict) -> dict:
    out = {}
    for name, fn, path in (("slope_monitor.html", render_monitor, MONITOR),
                           ("slope_map.html", render_map, MAP),
                           ("slope_3d.html", render_3d, THREED)):
        changed, wrapped = fn(d)
        out[name] = {"changed": changed, "wrapped": wrapped}
        if wrapped:
            print(f"{name}: first rebuild — wrapped regions {', '.join(wrapped)}")
        print(f"{name}: {'updated' if changed else 'no change'}")
    return out


def issued_stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
