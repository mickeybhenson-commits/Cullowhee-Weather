"""
flood_profile.py — reach-based corridor state (simulated until sensors deploy)

The watershed is represented as REACHES between sensor nodes, not interpolated
points. Each reach is anchored by its two endpoint nodes. In simulation the
endpoint depth/discharge come from drainage-area scaling (campus anchored to the
SURVEYED 20.8 mi^2); the reach carries the RANGE between its ends. As sensors
come online at the nodes, each endpoint becomes a live measurement and the reach
range is anchored by real data — no interpolation of interior points is claimed.

This matches the deployed architecture: sensors all over the watershed, each
defining a reach boundary. Adding a sensor simply splits a reach in two.
"""

import math
import flood_engine as fe
import flood_network as fn

CAMPUS_AREA_SQMI = 20.8                  # surveyed (StreamStats) — REAL
NODE_AREA_SQMI = {                       # cumulative drainage area per node (mi^2)
    "double_springs": 3.2,               # [placeholder — delineate]
    "aahp":           2.1,               # [placeholder — delineate]
    "speedwell":      12.0,              # [placeholder — delineate]
    "belk":           CAMPUS_AREA_SQMI,
}
AREA_EXP     = 0.8
REF_STAGE_FT = 9.0

# Per-node stage thresholds (watch, warning, emergency) in ft.
# Campus mirrors the constructed-channel thresholds; the rest are placeholders
# to set from each reach's bankfull / cross-section.  [SET]
NODE_THRESHOLDS = {
    "belk":           (7.0, 9.0, 11.0),   # campus constructed channel
    "speedwell":      (6.0, 8.0, 10.0),   # [placeholder]
    "double_springs": (4.0, 5.0, 6.0),    # [placeholder]
    "aahp":           (4.0, 5.0, 6.0),    # [placeholder]
}
# Stream name carried by each reach (keyed by the reach's UPSTREAM node).
# The main stem to the campus is Cullowhee Creek (USGS) — confident; the two
# tributary names are editable placeholders, set them to the real USGS names. [SET]
STREAM_NAMES = {
    "speedwell":      "Cullowhee Creek",        # main stem -> campus (USGS)
    "double_springs": "Cullowhee Creek",        # upper main stem (USGS)
    "aahp":           "Tilley Creek",           # tributary (USGS)
}
# Short display names for the schematic (full names can be long)
DISP_NAME = {
    "double_springs": "Double Springs",
    "aahp":           "AAHP ridge",
    "speedwell":      "Speedwell (confluence)",
    "belk":           "WCU Campus",
}
LEVEL_ORDER = ["NORMAL", "WATCH", "WARNING", "EMERGENCY"]
SEV = {"NORMAL": "#1A7A52", "WATCH": "#C08A00", "WARNING": "#C2410C", "EMERGENCY": "#B42318"}


def classify(depth_ft, node_id):
    w, wa, e = NODE_THRESHOLDS.get(node_id, (7.0, 9.0, 11.0))
    if depth_ft >= e:  return "EMERGENCY"
    if depth_ft >= wa: return "WARNING"
    if depth_ft >= w:  return "WATCH"
    return "NORMAL"


def _worse(a, b):
    return a if LEVEL_ORDER.index(a) >= LEVEL_ORDER.index(b) else b


def _ref_discharge():
    return fe.mannings_discharge_cfs(REF_STAGE_FT)


def _invert_depth(Q):
    lo, hi = 0.05, 16.0
    for _ in range(60):
        m = 0.5 * (lo + hi)
        if fe.mannings_discharge_cfs(m) < Q:
            lo = m
        else:
            hi = m
    return 0.5 * (lo + hi)


def discharge_for_area(area_sqmi):
    return _ref_discharge() * (area_sqmi / CAMPUS_AREA_SQMI) ** AREA_EXP


def node_state(node_id):
    a = NODE_AREA_SQMI.get(node_id, 1.0)
    Q = discharge_for_area(a)
    depth = round(_invert_depth(Q), 2)
    return {"node": node_id, "name": fn.SITES[node_id]["name"],
            "area_sqmi": round(a, 2), "discharge_cfs": round(Q),
            "depth_ft": depth, "level": classify(depth, node_id), "measured": False}


def _reach_edges():
    """Topology edges that lie on the path to the campus = the reaches."""
    edges = []
    for sid in fn.contributing_sites("belk"):
        dn = fn.SITES[sid]["downstream"]
        if dn is not None:
            edges.append((sid, dn))
    # order downstream-last for readability
    return sorted(edges, key=lambda e: fn.path_travel_hr(e[0], "belk") or 0.0)


def reaches():
    """One record per reach, anchored by its two endpoint nodes."""
    out = []
    for up, dn in _reach_edges():
        u, d = node_state(up), node_state(dn)
        length_mi = fn.REACH_LENGTH_FT.get(up, 0.0) / 5280.0
        out.append({
            "up": up, "dn": dn,
            "name": f'{u["name"]} \u2192 {d["name"]}',
            "length_mi": round(length_mi, 2),
            "up_depth_ft": u["depth_ft"], "dn_depth_ft": d["depth_ft"],
            "up_discharge_cfs": u["discharge_cfs"], "dn_discharge_cfs": d["discharge_cfs"],
            "up_area_sqmi": u["area_sqmi"], "dn_area_sqmi": d["area_sqmi"],
            "up_level": u["level"], "dn_level": d["level"],
            "level": _worse(u["level"], d["level"]),
            "stream": STREAM_NAMES.get(up, ""),
            "measured": u["measured"] and d["measured"],
        })
    return out


# ---------------------------------------------------------------------
# SCHEMATIC PLAN-VIEW SVG — reaches as channel segments
# ---------------------------------------------------------------------
_NODE_XY = {"double_springs": (95, 95), "aahp": (95, 495),
            "speedwell": (485, 300), "belk": (825, 300)}


def _depth_color(d):
    return ("#9CC4E8" if d < 4 else "#5E9BD6" if d < 5.5 else
            "#3B7DC4" if d < 7 else "#234F86" if d < 8.5 else "#123F6E")


def _reach_width(meanQ):
    return 5 + math.sqrt(max(meanQ, 0.0)) * 0.22


def corridor_svg():
    rs = reaches()
    s = ['<svg width="100%" viewBox="0 0 940 600" xmlns="http://www.w3.org/2000/svg" '
         'font-family="Inter,system-ui,sans-serif">']
    s.append('<text x="28" y="30" font-size="16" font-weight="700" fill="#1B2A38">'
             'Corridor reaches \u2014 Watch / Warning / Emergency</text>')
    s.append('<text x="28" y="52" font-size="11.5" fill="#6B7C8C">'
             'Each reach takes the more severe of its two ends. '
             'Per-reach thresholds (campus 7 / 9 / 11 ft; upstream placeholder).</text>')

    # --- reach segments ------------------------------------------------
    for r in rs:
        p, q = _NODE_XY[r["up"]], _NODE_XY[r["dn"]]
        meanQ = 0.5 * (r["up_discharge_cfs"] + r["dn_discharge_cfs"])
        w = _reach_width(meanQ)
        col = SEV[r["level"]]
        s.append(f'<line x1="{p[0]}" y1="{p[1]}" x2="{q[0]}" y2="{q[1]}" '
                 f'stroke="{col}" stroke-width="{w:.1f}" stroke-linecap="round"/>')

    # --- reach label blocks (stream name / level / range), placed off the line
    def reach_block(up, dn, side):
        r = next(x for x in rs if x["up"] == up and x["dn"] == dn)
        p, q = _NODE_XY[up], _NODE_XY[dn]
        mx, my = (p[0] + q[0]) / 2, (p[1] + q[1]) / 2
        col = SEV[r["level"]]
        if side == "above":   y0 = my - 44
        elif side == "below": y0 = my + 18
        else:                 y0 = my - 44
        s.append(f'<text x="{mx:.0f}" y="{y0:.0f}" font-size="11.5" font-weight="700" '
                 f'text-anchor="middle" fill="#1B2A38">{r["stream"]}</text>')
        s.append(f'<text x="{mx:.0f}" y="{y0+16:.0f}" font-size="11" font-weight="700" '
                 f'text-anchor="middle" fill="{col}">{r["level"]}</text>')
        s.append(f'<text x="{mx:.0f}" y="{y0+31:.0f}" font-size="9.5" '
                 f'text-anchor="middle" fill="#3C4C5A">{r["up_depth_ft"]:.1f}\u2192{r["dn_depth_ft"]:.1f} ft'
                 f' &#183; {r["up_discharge_cfs"]:,}\u2192{r["dn_discharge_cfs"]:,} cfs</text>')
    reach_block("double_springs", "speedwell", "above")
    reach_block("aahp", "speedwell", "below")
    reach_block("speedwell", "belk", "above")

    # --- node markers + labels ----------------------------------------
    def node(nid, name_dy, val_dy, anchor, tx_off):
        ns = node_state(nid)
        cx, cy = _NODE_XY[nid]
        s.append(f'<circle cx="{cx}" cy="{cy}" r="7" fill="{SEV[ns['level']]}" '
                 f'stroke="#fff" stroke-width="2"/>')
        tx = cx + tx_off
        s.append(f'<text x="{tx}" y="{cy+name_dy}" font-size="13" font-weight="700" '
                 f'fill="#1B2A38" text-anchor="{anchor}">{DISP_NAME[nid]}</text>')
        s.append(f'<text x="{tx}" y="{cy+val_dy}" font-size="10.5" fill="#5B6B7A" '
                 f'text-anchor="{anchor}">{ns["level"]} &#183; {ns["depth_ft"]:.1f} ft &#183; '
                 f'{ns["discharge_cfs"]:,} cfs</text>')
    # Double Springs: labels above the node (line leaves downward)
    node("double_springs", -30, -14, "start", -6)
    # AAHP ridge: labels below the node (line leaves upward)
    node("aahp", 28, 44, "start", -6)
    # Speedwell: labels below the node, centred
    node("speedwell", 34, 50, "middle", 0)
    # WCU Campus: name above, value below, centred (away from the reach label)
    node("belk", -22, 26, "middle", 0)

    # --- severity legend ----------------------------------------------
    x = 28
    for lv in LEVEL_ORDER:
        s.append(f'<rect x="{x}" y="556" width="13" height="13" rx="2" fill="{SEV[lv]}"/>')
        s.append(f'<text x="{x+19}" y="567" font-size="10.5" fill="#5B6B7A">{lv.title()}</text>')
        x += 96
    s.append('<text x="28" y="590" font-size="9.5" fill="#8A97A4" font-style="italic">'
             'Simulated \u2014 endpoint depth from area scaling; line thickness = discharge. '
             'Cullowhee Creek is the USGS main stem; tributary names are placeholders to confirm.</text>')
    s.append('</svg>')
    return "\n".join(s)


if __name__ == "__main__":
    fn.recompute_travel_times()
    for r in reaches():
        print(f'{r["name"]:<40} {r["length_mi"]:4.1f} mi  '
              f'depth {r["up_depth_ft"]:.1f}->{r["dn_depth_ft"]:.1f} ft  '
              f'Q {r["up_discharge_cfs"]}->{r["dn_discharge_cfs"]} cfs')
    print("\nSVG chars:", len(corridor_svg()))


# =====================================================================
# REAL-BASEMAP DATA  (pydeck)
# =====================================================================
# Surveyed watershed boundary (StreamStats delineation, simplified) — REAL
BASIN_POLYGON = [[-83.24549, 35.25504], [-83.24397, 35.25666], [-83.23764, 35.25688], [-83.23691, 35.26012], [-83.22615, 35.26801], [-83.22754, 35.27142], [-83.22697, 35.27408], [-83.22206, 35.2764], [-83.22526, 35.2819], [-83.22277, 35.28628], [-83.21376, 35.28907], [-83.21328, 35.29139], [-83.20867, 35.2956], [-83.20232, 35.29936], [-83.18676, 35.30411], [-83.18449, 35.30691], [-83.18575, 35.30976], [-83.17969, 35.30939], [-83.17433, 35.29522], [-83.16692, 35.28805], [-83.1667, 35.28566], [-83.16937, 35.28087], [-83.16871, 35.27792], [-83.16693, 35.27452], [-83.16111, 35.27092], [-83.16212, 35.26907], [-83.15906, 35.26415], [-83.15923, 35.2596], [-83.15741, 35.25744], [-83.15943, 35.25374], [-83.15833, 35.24998], [-83.16379, 35.24451], [-83.16347, 35.24032], [-83.16584, 35.23545], [-83.16877, 35.23378], [-83.17451, 35.234], [-83.17362, 35.2304], [-83.17493, 35.22656], [-83.17361, 35.22231], [-83.17601, 35.22017], [-83.1762, 35.21793], [-83.181, 35.21546], [-83.1808, 35.21356], [-83.18328, 35.2129], [-83.18514, 35.20995], [-83.18694, 35.21376], [-83.19235, 35.21325], [-83.19584, 35.20884], [-83.20052, 35.20991], [-83.20364, 35.20807], [-83.20556, 35.20437], [-83.21632, 35.20837], [-83.21596, 35.21309], [-83.21836, 35.21671], [-83.21847, 35.22084], [-83.21649, 35.22338], [-83.22171, 35.22889], [-83.22413, 35.23871], [-83.23275, 35.24022], [-83.24294, 35.23887], [-83.24549, 35.25504]]

# Node coordinates [lon, lat].  Campus outlet is the surveyed StreamStats point
# (REAL); the others are APPROXIMATE placeholders — replace each with the actual
# sensor GPS coordinate when the node is sited.  [SET]
NODE_COORDS = {
    "belk":           [-83.18483, 35.30661],   # REAL — StreamStats outlet
    "speedwell":      [-83.19000, 35.28500],   # [approx — set to sensor GPS]
    "double_springs": [-83.18350, 35.21200],   # [approx — set to sensor GPS]
    "aahp":           [-83.23400, 35.25300],   # [approx — set to sensor GPS]
}


_NODE_OFFSET = {"belk": [0, -26], "speedwell": [98, 4],
                "double_springs": [0, 20], "aahp": [0, 22]}
_REACH_LABEL_OFFSET = {"speedwell": [72, 0], "double_springs": [82, 0], "aahp": [-8, -20]}


def _hex_rgb(h):
    h = h.lstrip("#")
    return [int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)]


def map_nodes():
    out = []
    for nid, xy in NODE_COORDS.items():
        ns = node_state(nid)
        real = (nid == "belk")
        out.append({
            "name": ns["name"], "position": xy, "level": ns["level"],
            "color": _hex_rgb(SEV[ns["level"]]) + [235],
            "radius": 6 + math.sqrt(max(ns["discharge_cfs"], 0)) * 0.22,
            "label": f'{DISP_NAME.get(nid, ns["name"])}\n{ns["depth_ft"]:.1f} ft \u00b7 {ns["discharge_cfs"]:,} cfs',
            "off": _NODE_OFFSET.get(nid, [0, 18]),
            "tip": f'{ns["level"]} \u00b7 {ns["depth_ft"]:.1f} ft \u00b7 {ns["discharge_cfs"]:,} cfs'
                   + ("" if real else "  (approx location)"),
        })
    return out


def map_reaches():
    out = []
    for r in reaches():
        p, q = NODE_COORDS.get(r["up"]), NODE_COORDS.get(r["dn"])
        if not p or not q:
            continue
        meanQ = 0.5 * (r["up_discharge_cfs"] + r["dn_discharge_cfs"])
        out.append({
            "name": r["name"], "level": r["level"], "path": [p, q],
            "color": _hex_rgb(SEV[r["level"]]) + [120],
            "width": 4 + math.sqrt(max(meanQ, 0)) * 0.15,
            "label": f'{r["up_depth_ft"]:.1f}\u2192{r["dn_depth_ft"]:.1f} ft  '
                     f'{r["up_discharge_cfs"]:,}\u2192{r["dn_discharge_cfs"]:,} cfs',
            "tip": f'{r["level"]} \u00b7 {r["up_depth_ft"]:.1f}\u2192{r["dn_depth_ft"]:.1f} ft '
                   f'\u00b7 {r["up_discharge_cfs"]:,}\u2192{r["dn_discharge_cfs"]:,} cfs',
        })
    return out


def map_reach_labels():
    """Stream-name labels placed at each reach midpoint (for the map TextLayer)."""
    out = []
    for r in reaches():
        p, q = NODE_COORDS.get(r["up"]), NODE_COORDS.get(r["dn"])
        if not p or not q:
            continue
        out.append({
            "position": [(p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0],
            "text": r["stream"],
            "level": r["level"],
            "color": _hex_rgb(SEV[r["level"]]) + [255],
            "off": _REACH_LABEL_OFFSET.get(r["up"], [0, -16]),
        })
    return out


BASIN_FEATURE = [{"polygon": BASIN_POLYGON, "name": "Cullowhee Creek watershed (20.8 mi\u00b2, surveyed)"}]
