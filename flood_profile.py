"""
flood_profile.py — simulated depth & discharge at sample stations along the
corridor: the two headwater tributaries + the Cullowhee Creek mainstem.

ALL VALUES SIMULATED until sensor data + surveyed channel geometry arrive:
  - discharge : drainage-area scaling, Q ~ (A / A_campus)^EXP, anchored to a
                reference event at the campus outlet
  - depth     : that discharge run back through the channel rating (flood_engine)
  - areas     : campus anchored to the SURVEYED 20.8 mi^2 (StreamStats delineation);
                the upstream split is interpolated  [placeholder until per-node
                delineation at Speedwell + the headwaters]
  - spacing   : stations laid along the placeholder reach lengths  [set from DEM]

Produces the station table AND a schematic plan-view SVG for the dashboard.
Because the channel rating is uniform everywhere (placeholder), the depth
profile is smooth; real per-section depth needs the TVA cross-sections.
"""

import flood_engine as fe
import flood_network as fn

# Surveyed campus drainage area — StreamStats delineation at the outlet (REAL)
CAMPUS_AREA_SQMI = 20.8

# Cumulative drainage area at each node (mi^2). Campus is surveyed; the rest are
# placeholders until you delineate Speedwell + the headwaters in StreamStats. [SET]
NODE_AREA_SQMI = {
    "double_springs": 3.2,            # [placeholder — delineate]
    "aahp":           2.1,            # [placeholder — delineate]
    "speedwell":      12.0,           # [placeholder — delineate; area above confluence]
    "belk":           CAMPUS_AREA_SQMI,
}

AREA_EXP        = 0.8        # discharge ~ drainage-area^0.8 (regional scaling)
REF_STAGE_FT    = 9.0        # reference event: campus at WARNING stage
STATION_SPACING_FT = 5280.0 # ~one sample station per stream-mile

# reaches as (upstream node, downstream node), per the network topology
REACHES = [
    ("double_springs", "speedwell"),
    ("aahp",           "speedwell"),
    ("speedwell",      "belk"),
]


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
    return {"area_sqmi": round(a, 2), "discharge_cfs": round(Q), "depth_ft": round(_invert_depth(Q), 2)}


def stations():
    """Sample stations along each reach with simulated depth & discharge."""
    out = []
    for up, dn in REACHES:
        length = fn.REACH_LENGTH_FT.get(up, 0.0)
        a0 = NODE_AREA_SQMI.get(up, 1.0)
        a1 = NODE_AREA_SQMI.get(dn, a0)
        n = max(2, int(round(length / STATION_SPACING_FT)) + 1)
        reach_name = f"{fn.SITES[up]['name']} \u2192 {fn.SITES[dn]['name']}"
        for k in range(n):
            t = k / (n - 1)
            area = a0 + (a1 - a0) * t
            Q = discharge_for_area(area)
            out.append({
                "reach": reach_name, "up": up, "dn": dn, "frac": t,
                "chainage_mi": round((length * t) / 5280.0, 2),
                "area_sqmi": round(area, 2),
                "discharge_cfs": round(Q),
                "depth_ft": round(_invert_depth(Q), 2),
            })
    return out


# ---------------------------------------------------------------------
# SCHEMATIC PLAN-VIEW SVG  (built from the station table)
# ---------------------------------------------------------------------
import math

_NODE_XY = {"double_springs": (70, 70), "aahp": (70, 360),
            "speedwell": (440, 215), "belk": (710, 215)}


def _depth_color(d):
    return ("#9CC4E8" if d < 4 else "#5E9BD6" if d < 5.5 else
            "#3B7DC4" if d < 7 else "#234F86" if d < 8.5 else "#123F6E")


def _radius(Q):
    return 5 + math.sqrt(max(Q, 0.0)) * 0.30


def corridor_svg():
    """Return a schematic plan-view SVG string of the corridor with stations."""
    s = ['<svg width="100%" viewBox="0 0 800 470" xmlns="http://www.w3.org/2000/svg" '
         'font-family="Inter,system-ui,sans-serif">']
    s.append('<text x="24" y="26" font-size="15" font-weight="700" fill="#1B2A38">'
             'Corridor profile \u2014 simulated depth &amp; discharge</text>')
    s.append('<text x="24" y="44" font-size="11" fill="#6B7C8C">'
             'Reference event: WARNING stage at the campus outlet '
             f'({node_state("belk")["depth_ft"]:.1f} ft / {node_state("belk")["discharge_cfs"]:,} cfs)</text>')
    # creek lines
    for up, dn in REACHES:
        p, q = _NODE_XY[up], _NODE_XY[dn]
        s.append(f'<line x1="{p[0]}" y1="{p[1]}" x2="{q[0]}" y2="{q[1]}" '
                 'stroke="#BBD3E8" stroke-width="6" stroke-linecap="round"/>')
    # stations
    sts = stations()
    by_reach = {}
    for st_ in sts:
        by_reach.setdefault((st_["up"], st_["dn"]), []).append(st_)
    for (up, dn), pts in by_reach.items():
        p, q = _NODE_XY[up], _NODE_XY[dn]
        n = len(pts)
        for k, st_ in enumerate(pts):
            t = st_["frac"]
            x = p[0] + (q[0] - p[0]) * t
            y = p[1] + (q[1] - p[1]) * t
            r = _radius(st_["discharge_cfs"])
            s.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{r:.1f}" '
                     f'fill="{_depth_color(st_["depth_ft"])}" stroke="#fff" stroke-width="1"/>')
            if 0 < k < n - 1:
                s.append(f'<text x="{x:.0f}" y="{y - r - 4:.0f}" font-size="9" '
                         f'text-anchor="middle" fill="#5B6B7A">{st_["depth_ft"]:.1f}ft</text>')
    # node labels
    def label(node, anchor):
        ns = node_state(node)
        cx, cy = _NODE_XY[node]
        tx = cx + (14 if anchor == "start" else -14)
        nm = fn.SITES[node]["name"]
        s.append(f'<text x="{tx}" y="{cy-6}" font-size="12" font-weight="700" '
                 f'fill="#1B2A38" text-anchor="{anchor}">{nm}</text>')
        s.append(f'<text x="{tx}" y="{cy+9}" font-size="10.5" fill="#234F86" '
                 f'text-anchor="{anchor}">{ns["depth_ft"]:.1f} ft &#183; {ns["discharge_cfs"]:,} cfs</text>')
    label("double_springs", "start")
    label("aahp", "start")
    # speedwell label below its node so it doesn't collide with the mainstem
    sp = node_state("speedwell")
    s.append(f'<text x="454" y="249" font-size="12" font-weight="700" fill="#1B2A38">Speedwell (confluence)</text>')
    s.append(f'<text x="454" y="264" font-size="10.5" fill="#234F86">{sp["depth_ft"]:.1f} ft &#183; {sp["discharge_cfs"]:,} cfs</text>')
    label("belk", "end")
    # legend
    s.append('<circle cx="32" cy="430" r="5" fill="#9CC4E8"/>'
             '<circle cx="50" cy="430" r="8" fill="#3B7DC4"/>'
             '<circle cx="72" cy="430" r="11" fill="#123F6E"/>')
    s.append('<text x="90" y="434" font-size="10.5" fill="#5B6B7A">'
             'size = discharge &#183; color = depth (light&#8594;dark = shallow&#8594;deep)</text>')
    s.append('<text x="24" y="452" font-size="9.5" fill="#8A97A4" font-style="italic">'
             'Simulated \u2014 area-scaled discharge + uniform placeholder channel rating; campus area surveyed (20.8 mi&#178;), '
             'upstream split interpolated. Real per-section depth needs the TVA cross-sections.</text>')
    s.append('</svg>')
    return "\n".join(s)


if __name__ == "__main__":
    for st_ in stations():
        print(f'{st_["reach"]:<34} +{st_["chainage_mi"]:4.1f} mi  '
              f'A={st_["area_sqmi"]:5.1f}  Q={st_["discharge_cfs"]:5} cfs  d={st_["depth_ft"]:4.1f} ft')
    print("\nSVG chars:", len(corridor_svg()))
