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


_NODE_OFFSET = {"belk": [0, -26], "speedwell": [0, 26],
                "double_springs": [0, 22], "aahp": [-10, 22]}
_REACH_LABEL_OFFSET = {"speedwell": [-84, 0], "double_springs": [-88, 0], "aahp": [-10, -22]}


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


# Real USGS stream network (NLDI upstream-tributaries flowlines from the campus
# outlet), reconstructed into main stem vs. tributaries and simplified.  REAL.
STREAM_MAIN = [[[-83.18286, 35.30109], [-83.18297, 35.30173], [-83.18451, 35.30544], [-83.18457, 35.30608], [-83.18577, 35.30729], [-83.18761, 35.30828], [-83.18786, 35.30825]], [[-83.1862, 35.28289], [-83.18567, 35.28415], [-83.18447, 35.28474], [-83.18285, 35.28614], [-83.18221, 35.28646], [-83.18076, 35.28768], [-83.18064, 35.28823], [-83.18235, 35.28921], [-83.18263, 35.28958], [-83.18285, 35.29095], [-83.18191, 35.29502], [-83.18132, 35.29599], [-83.18126, 35.29665], [-83.1802, 35.29786], [-83.1802, 35.29839], [-83.18093, 35.2994], [-83.18222, 35.2994], [-83.18275, 35.30018], [-83.18286, 35.30109]], [[-83.18149, 35.25645], [-83.18149, 35.25725], [-83.18227, 35.25826], [-83.1823, 35.25931], [-83.18288, 35.25963], [-83.1828, 35.26034], [-83.18247, 35.2605], [-83.18247, 35.26107], [-83.18277, 35.26142], [-83.18261, 35.26188], [-83.1821, 35.26229], [-83.18222, 35.26339], [-83.18202, 35.26467], [-83.18286, 35.26581], [-83.18272, 35.26639], [-83.18289, 35.26726], [-83.1827, 35.26792], [-83.18289, 35.26845], [-83.18211, 35.26879], [-83.18219, 35.26977], [-83.182, 35.27142], [-83.18273, 35.27309], [-83.1827, 35.27348], [-83.18234, 35.27387], [-83.18236, 35.27428], [-83.18276, 35.27488], [-83.18287, 35.27678], [-83.18312, 35.27714], [-83.18362, 35.27733], [-83.18429, 35.2784], [-83.18575, 35.28124], [-83.1862, 35.28289]], [[-83.18636, 35.24696], [-83.18627, 35.24792], [-83.1858, 35.24856], [-83.18549, 35.24975], [-83.18411, 35.25137], [-83.18377, 35.25148], [-83.18321, 35.25251], [-83.18327, 35.25313], [-83.18241, 35.25583], [-83.18149, 35.25645]], [[-83.18733, 35.24375], [-83.18669, 35.24504], [-83.18636, 35.24696]], [[-83.18732, 35.2326], [-83.18701, 35.2329], [-83.18618, 35.23544], [-83.18618, 35.23673], [-83.18573, 35.23755], [-83.18587, 35.23973], [-83.18557, 35.24103], [-83.18557, 35.24176], [-83.18618, 35.24284], [-83.18733, 35.24375]], [[-83.19195, 35.23086], [-83.19083, 35.23073], [-83.19041, 35.23091], [-83.18913, 35.2324], [-83.1888, 35.23256], [-83.18732, 35.2326]], [[-83.20054, 35.22419], [-83.19875, 35.22536], [-83.19708, 35.22536], [-83.19588, 35.22575], [-83.19538, 35.2264], [-83.19418, 35.22672], [-83.19359, 35.22766], [-83.19267, 35.22846], [-83.19265, 35.22967], [-83.19195, 35.23086]], [[-83.21218, 35.2107], [-83.21043, 35.21366], [-83.21015, 35.21533], [-83.21037, 35.21581], [-83.20971, 35.21752], [-83.20974, 35.21872], [-83.20901, 35.21988], [-83.20765, 35.22078], [-83.20667, 35.2219], [-83.20572, 35.2224], [-83.20494, 35.22323], [-83.2045, 35.22344], [-83.20355, 35.22321], [-83.20299, 35.22328], [-83.20254, 35.22344], [-83.20193, 35.2241], [-83.20054, 35.22419]]]
STREAM_TRIB = [[[-83.20709, 35.2935], [-83.20603, 35.29444], [-83.20544, 35.29524], [-83.20472, 35.29563], [-83.2031, 35.29558], [-83.19992, 35.2962], [-83.19846, 35.29595], [-83.1971, 35.29595], [-83.19612, 35.29559], [-83.19444, 35.29554], [-83.19079, 35.29589], [-83.18945, 35.29671], [-83.18766, 35.29711], [-83.18579, 35.29841], [-83.18518, 35.29944], [-83.1847, 35.29965], [-83.18381, 35.30063], [-83.18286, 35.30109]], [[-83.19186, 35.28096], [-83.19091, 35.2811], [-83.18971, 35.28211], [-83.18865, 35.28204], [-83.18784, 35.2825], [-83.1862, 35.28289]], [[-83.16307, 35.25625], [-83.16368, 35.25621], [-83.16524, 35.25556], [-83.16734, 35.25543], [-83.16987, 35.25414], [-83.17205, 35.25355], [-83.17331, 35.25279], [-83.17459, 35.25256], [-83.17585, 35.25272], [-83.1768, 35.25332], [-83.17842, 35.25393], [-83.17934, 35.25453], [-83.18149, 35.25645]], [[-83.23591, 35.24509], [-83.23544, 35.24584], [-83.23527, 35.24678], [-83.23505, 35.24708], [-83.23416, 35.24758], [-83.23396, 35.24795], [-83.23282, 35.24834], [-83.23215, 35.24894], [-83.23193, 35.24942], [-83.23048, 35.25011], [-83.22884, 35.25129], [-83.22689, 35.252], [-83.22605, 35.25278], [-83.22494, 35.25329], [-83.2243, 35.25386], [-83.22357, 35.25512], [-83.22176, 35.25565], [-83.21947, 35.25496], [-83.21841, 35.25544], [-83.21069, 35.26293], [-83.20971, 35.26367], [-83.20779, 35.26438], [-83.20572, 35.26477], [-83.20438, 35.26553], [-83.20179, 35.26656], [-83.19958, 35.26718], [-83.19746, 35.26812], [-83.1971, 35.26869], [-83.19649, 35.26915], [-83.19643, 35.2699], [-83.19587, 35.27087], [-83.19585, 35.27116], [-83.19621, 35.27151], [-83.19621, 35.27228], [-83.19392, 35.27599], [-83.19404, 35.27675], [-83.19379, 35.27739], [-83.19286, 35.27847], [-83.19217, 35.27982], [-83.19222, 35.28037], [-83.19186, 35.28096]], [[-83.21504, 35.28505], [-83.21353, 35.28452], [-83.21021, 35.28443], [-83.20814, 35.28427], [-83.20753, 35.28407], [-83.20448, 35.28393], [-83.2025, 35.28315], [-83.20152, 35.28299], [-83.19812, 35.2835], [-83.1972, 35.28318], [-83.19611, 35.28325], [-83.19452, 35.28259], [-83.19186, 35.28096]], [[-83.17189, 35.23783], [-83.17279, 35.23884], [-83.17368, 35.24067], [-83.17617, 35.24298], [-83.17706, 35.24486], [-83.17737, 35.24516], [-83.1822, 35.24625], [-83.18636, 35.24696]], [[-83.20511, 35.24731], [-83.20433, 35.24722], [-83.19992, 35.24565], [-83.19559, 35.24359], [-83.19366, 35.24364], [-83.18953, 35.24325], [-83.18733, 35.24375]], [[-83.18379, 35.21583], [-83.18468, 35.21699], [-83.18452, 35.21791], [-83.18201, 35.22025], [-83.18117, 35.22164], [-83.1812, 35.22343], [-83.18145, 35.22441], [-83.18193, 35.2253], [-83.1819, 35.22789], [-83.18149, 35.22881], [-83.18157, 35.22924], [-83.18252, 35.2303], [-83.18313, 35.23039], [-83.18447, 35.23107], [-83.18715, 35.23203], [-83.18732, 35.2326]], [[-83.21292, 35.24328], [-83.21278, 35.24135], [-83.213, 35.23904], [-83.21096, 35.23607], [-83.2097, 35.23584], [-83.20649, 35.23577], [-83.20529, 35.23541], [-83.20345, 35.23525], [-83.20049, 35.23436], [-83.19837, 35.23415], [-83.19485, 35.23313], [-83.19368, 35.23255], [-83.19195, 35.23086]], [[-83.2068, 35.20988], [-83.20663, 35.21077], [-83.20543, 35.21286], [-83.20371, 35.21389], [-83.20047, 35.21504], [-83.19922, 35.2162], [-83.19919, 35.21689], [-83.19872, 35.21769], [-83.19863, 35.21877], [-83.19911, 35.21998], [-83.19911, 35.22133], [-83.20054, 35.22419]]]


def map_streams_main():
    return [{"path": p} for p in STREAM_MAIN]


def map_streams_trib():
    return [{"path": p} for p in STREAM_TRIB]


BASIN_FEATURE = [{"polygon": BASIN_POLYGON, "name": "Cullowhee Creek watershed (20.8 mi\u00b2, surveyed)"}]
