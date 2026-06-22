"""
Test Model — Streamlit page for the Cullowhee flood-engine test model.
Imports test_model.py from the repo root (no logic duplicated here).

Tabs: Single case (synthetic storm), Storm × antecedent sweep, Historical replay,
and Live weather (real forecast + modeled antecedent).
Storm/antecedent controls live on the Single-case tab; only PRF is global.
"""

import json
import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import test_model as tm

st.set_page_config(page_title="Cullowhee flood — test model", layout="wide")

POSTURE_COLOR = {"NORMAL": "#1D9E75", "WATCH": "#EBA833",
                 "WARNING": "#D2691E", "EMERGENCY": "#B0282B"}

NAMES = {"CC-UP-503": "Mountain", "CC-MS-1100": "Mtn. Lower", "CC-TIL-705": "Tilley Creek",
         "CC-SPD-1830": "Speedwell", "CC-COX-097": "Cox Branch", "CC-LB-171": "Long Branch",
         "CC-WCU-2260": "WCU Campus", "CC-MOUTH-2340": "Mouth"}

def disp(bid):
    return f"{NAMES.get(bid, bid)} ({bid})"

# Fixed watershed framing (SW, NE) — guarantees the map shows the whole basin
# and can never collapse to street zoom.
# Fixed view — frames the whole Cullowhee Creek watershed. Passed straight to
# st_folium (center/zoom), which honors it reliably; folium's fit_bounds does not.
WS_CENTER = [35.263, -83.201]
WS_ZOOM = 12

# Fallback outlet markers (only if cullowhee_subbasins.geojson is missing).
BASIN_PTS = {
    "CC-UP-503": (35.241, -83.185), "CC-MS-1100": (35.265, -83.190),
    "CC-TIL-705": (35.268, -83.205), "CC-SPD-1830": (35.270, -83.190),
    "CC-COX-097": (35.302, -83.178), "CC-LB-171": (35.305, -83.195),
    "CC-WCU-2260": (35.290, -83.185), "CC-MOUTH-2340": (35.300, -83.185),
}

# Regional zoom for the incoming-weather map — wide enough to watch systems
# approach (~135 km each way ≈ several hours of lead) with the watershed outlined.
RADAR_ZOOM = 9

# Modeled-wetness palette for the ARC column (dry -> wet). These are MODELED
# values (from trailing rainfall), not soil-moisture sensor readings.
WET_COLOR = {1: ("#E0CDA9", "#5A3A1A"), 2: ("#9CC3E0", "#16384D"), 3: ("#2E6CA4", "#FFFFFF")}

# Animated precipitation radar (RainViewer, free, no key) for the dedicated
# incoming-weather map. Drawn below the watershed outline.
RADAR_JS = """
(function(){
  function init(){
    try{
      if(typeof MAPVAR==='undefined'||typeof L==='undefined'){return setTimeout(init,300);}
      var rmap=MAPVAR;
      if(!rmap.getPane('radar')){
        rmap.createPane('radar');
        rmap.getPane('radar').style.zIndex=350;
        rmap.getPane('radar').style.pointerEvents='none';
      }
      fetch('https://api.rainviewer.com/public/weather-maps.json')
        .then(function(r){return r.json();})
        .then(function(d){
          var host=d.host||'https://tilecache.rainviewer.com';
          var frames=[];
          if(d.radar){frames=(d.radar.past||[]).concat(d.radar.nowcast||[]);}
          if(!frames.length){return;}
          var layers=frames.map(function(f){
            return L.tileLayer(host+f.path+'/256/{z}/{x}/{y}/4/1_1.png',{opacity:0,pane:'radar'});
          });
          layers.forEach(function(l){l.addTo(rmap);});
          var i=frames.length-1;
          function show(k){layers.forEach(function(l,j){l.setOpacity(j===k?0.7:0);});}
          show(i);
          setInterval(function(){i=(i+1)%frames.length;show(i);},700);
        })
        .catch(function(e){console.log('radar error',e);});
    }catch(e){console.log('radar init error',e);}
  }
  init();
})();
"""


def color_posture(series):
    return [f"background-color:{POSTURE_COLOR.get(v,'')};color:white;font-weight:600"
            if v in POSTURE_COLOR else "" for v in series]


def _coords(geometry):
    t, c = geometry.get("type"), geometry.get("coordinates", [])
    pts = []
    if t == "Polygon":
        for ring in c:
            pts += [(y, x) for x, y, *_ in ring]
    elif t == "MultiPolygon":
        for poly in c:
            for ring in poly:
                pts += [(y, x) for x, y, *_ in ring]
    return pts


def _label_marker(lat, lon, text):
    return folium.map.Marker(
        [lat, lon],
        icon=folium.DivIcon(icon_size=(0, 0), icon_anchor=(0, 0), html=(
            f'<div style="transform:translate(-50%,-50%);font-size:11px;font-weight:700;'
            f'color:#161616;text-shadow:0 0 3px #fff,0 0 3px #fff,0 0 3px #fff,0 0 3px #fff;'
            f'white-space:nowrap;text-align:center">{text}</div>')))


def posture_map(postures):
    """Sub-basins colored by posture, framed to the whole watershed.
    Returns (map, have_geojson)."""
    m = folium.Map(location=WS_CENTER, zoom_start=WS_ZOOM,
                   tiles="CartoDB positron", control_scale=True)
    try:
        with open("cullowhee_subbasins.geojson") as f:
            geo = json.load(f)
    except Exception:
        geo = None

    if geo:
        # largest-first so the SMALLEST basin is on top (correct hover + clean tiles)
        order = ["CC-MOUTH-2340", "CC-WCU-2260", "CC-SPD-1830", "CC-MS-1100",
                 "CC-TIL-705", "CC-UP-503", "CC-LB-171", "CC-COX-097"]
        feats = sorted(geo.get("features", []),
                       key=lambda f: order.index(f["properties"].get("basin_id"))
                       if f["properties"].get("basin_id") in order else 99)
        for feat in feats:
            props = feat["properties"]
            bid = props.get("basin_id", "")
            post = postures.get(bid, "NORMAL")
            props["posture"] = post
            props.setdefault("title", disp(bid))
            col = POSTURE_COLOR.get(post, "#888888")
            folium.GeoJson(
                feat,
                style_function=(lambda c: (lambda f: {
                    "fillColor": c, "color": "#1b1b1b", "weight": 1.5, "fillOpacity": 0.58}))(col),
                highlight_function=lambda f: {"weight": 3, "fillOpacity": 0.78},
                tooltip=folium.GeoJsonTooltip(fields=["title", "posture"],
                                              aliases=["Sub-basin", "Status"], sticky=True),
            ).add_to(m)
            llat, llon = props.get("label_lat"), props.get("label_lon")
            if llat is not None and llon is not None:
                _label_marker(llat, llon, props.get("name", NAMES.get(bid, bid))).add_to(m)
        have = True
    else:
        for bid, (lat, lon) in BASIN_PTS.items():
            post = postures.get(bid, "NORMAL")
            folium.CircleMarker(
                [lat, lon], radius=13, color="#1b1b1b", weight=1.5, fill=True,
                fill_color=POSTURE_COLOR.get(post, "#888888"), fill_opacity=0.9,
                tooltip=f"{disp(bid)}: {post}").add_to(m)
            _label_marker(lat, lon, NAMES.get(bid, bid)).add_to(m)
        have = False

    return m, have


def radar_map():
    """Incoming-weather map: animated RainViewer radar with the watershed outlined
    for reference, at a regional zoom so approaching systems are visible. Kept
    separate from the posture map so any radar issue can't affect it."""
    m = folium.Map(location=WS_CENTER, zoom_start=RADAR_ZOOM,
                   tiles="CartoDB positron", control_scale=True)
    try:
        with open("cullowhee_subbasins.geojson") as f:
            geo = json.load(f)
    except Exception:
        geo = None
    if geo:
        mouth = next((f for f in geo.get("features", [])
                      if f["properties"].get("basin_id") == "CC-MOUTH-2340"), None)
        folium.GeoJson(mouth if mouth else geo, style_function=lambda f: {
            "fillColor": "#1b1b1b", "fillOpacity": 0.05,
            "color": "#111", "weight": 2.5}).add_to(m)
        _label_marker(WS_CENTER[0], WS_CENTER[1], "Cullowhee Creek").add_to(m)
    m.get_root().script.add_child(
        folium.Element(RADAR_JS.replace("MAPVAR", m.get_name())))
    return m


def sm_color(pct):
    """Soil-moisture scale: dry (tan) -> saturated (deep blue)."""
    if pct < 40:
        return ("#E0CDA9", "#5A3A1A")
    if pct < 70:
        return ("#9CC3E0", "#16384D")
    if pct < 90:
        return ("#4A90C2", "#FFFFFF")
    return ("#2E6CA4", "#FFFFFF")


def style_live(df):
    """Color the MODELED columns: predicted depth + posture by posture color,
    soil moisture by its dry->saturated scale. None of these are measured."""
    def _row(row):
        out = [""] * len(row)
        idx = {c: i for i, c in enumerate(row.index)}
        pc = POSTURE_COLOR.get(row.get("posture"), "")
        if pc:
            for c in ("posture", "pred. depth (ft)"):
                if c in idx:
                    out[idx[c]] = f"background-color:{pc};color:white;font-weight:600"
        sm = str(row.get("soil moisture (est. %)", "")).rstrip("%")
        if sm.isdigit() and "soil moisture (est. %)" in idx:
            bg, fg = sm_color(int(sm))
            out[idx["soil moisture (est. %)"]] = (
                f"background-color:{bg};color:{fg};font-weight:600")
        return out
    return df.style.apply(_row, axis=1)


def legend(extra=""):
    chips = "".join(
        f"<span style='display:inline-block;width:13px;height:13px;background:{c};"
        f"border-radius:3px;margin:0 5px 0 16px;vertical-align:-2px'></span>"
        f"<span style='font-size:13px'>{name.title()}</span>"
        for name, c in POSTURE_COLOR.items())
    st.markdown(f"<div style='margin:2px 0 6px 0'>{chips}{extra}</div>",
                unsafe_allow_html=True)


st.title("Cullowhee Creek flood engine — tabletop test")
st.caption("Synthetic storms + antecedent moisture. No sensors required. "
           "Placeholders live in test_model.py — replace with confirmed values.")

# ---------------------------------------------------------------- sidebar (global only)
with st.sidebar:
    st.header("Model tuning")
    prf = st.slider("Peak rate factor (PRF)", 300, 600, 484, 10,
                    help="~484 standard; raise toward 600 for steep mountain basins. "
                         "Applies to every tab.")
    st.caption("Storm & antecedent controls are on the Single-case tab. "
               "The Live tab uses real weather — no scenario knobs.")

tab1, tab2, tab3, tab4 = st.tabs(["Single case", "Storm × antecedent sweep",
                                  "Historical replay", "Live weather"])

with tab1:
    cstorm, cant = st.columns(2)
    with cstorm:
        choices = list(tm.DESIGN_DEPTH_IN.keys()) + ["custom"]
        pick = st.radio("Design storm", choices, index=1, horizontal=True)
        if pick == "custom":
            depth = st.slider("24-hr depth (in)", 0.5, 12.0, 4.8, 0.1)
        else:
            depth = tm.DESIGN_DEPTH_IN[pick]
            st.write(f"**{pick}** — {depth}\" / 24 hr")
    with cant:
        p5 = st.slider("5-day antecedent rain (in)", 0.0, 5.0, 1.7, 0.1)
        arc = tm.arc_class(p5)
        label = {1: "dry", 2: "normal", 3: "wet"}[arc]
        st.info(f"ARC class **{arc}** — {label}")

    arc, res = tm.run_case(depth, p5, PRF=prf)
    rows = []
    for bid, r in res.items():
        b = tm.BASINS[bid]
        rows.append({"sub-basin": disp(bid), "DA (mi²)": b["DA"], "lead": b["lead"],
                     "CN": round(r["CN"]), "runoff (in)": round(r["Q"], 2),
                     "peak (cfs)": round(r["qp"]), "stage (ft)": round(r["stage"], 2),
                     "posture": r["posture"]})
    df = pd.DataFrame(rows)

    st.subheader(f"{depth}\" storm · ARC-{arc} · PRF {prf}")
    c = st.columns(4)
    for i, p in enumerate(["NORMAL", "WATCH", "WARNING", "EMERGENCY"]):
        c[i].metric(p, int((df["posture"] == p).sum()))

    postures = {bid: r["posture"] for bid, r in res.items()}
    m, have_geo = posture_map(postures)
    legend()
    st_folium(m, center=WS_CENTER, zoom=WS_ZOOM,
              height=520, width=1150, returned_objects=[])
    if not have_geo:
        st.caption("Showing outlet markers. Add cullowhee_subbasins.geojson "
                   "(at the repo root) to fill each sub-watershed.")

    st.dataframe(df.style.apply(color_posture, subset=["posture"]),
                 width="stretch", hide_index=True)

with tab2:
    st.subheader("Posture grid — storms (rows) × antecedent (cols)")
    bsel = st.selectbox("Sub-basin", list(tm.BASINS.keys()), format_func=disp)
    ant = [("dry", 0.2), ("normal", 1.7), ("wet", 3.0)]
    grid = []
    for sname, sdepth in tm.DESIGN_DEPTH_IN.items():
        row = {"storm": sname}
        for aname, ap5 in ant:
            _, r = tm.run_case(sdepth, ap5, PRF=prf)
            row[aname] = r[bsel]["posture"]
        grid.append(row)
    gdf = pd.DataFrame(grid).set_index("storm")
    st.dataframe(gdf.style.apply(lambda s: color_posture(s), axis=0), width="stretch")
    st.caption("Same sub-basin, every storm × antecedent combination.")

with tab3:
    st.subheader("Replay a real daily-rain series")
    txt = st.text_area("Daily rainfall (inches, comma-separated; last value = peak day region)",
                       "0.3, 0.8, 1.2, 2.1, 1.6, 3.4, 9.8, 2.2")
    k = st.slider("API recession k", 0.80, 0.95, 0.90, 0.01)
    try:
        rains = [float(x) for x in txt.replace("\n", ",").split(",") if x.strip() != ""]
        api = tm.api_series(rains, k=k)
        peak_i = max(range(len(rains)), key=lambda i: rains[i])
        p5h = sum(rains[max(0, peak_i - 5):peak_i])
        arc_h, res_h = tm.run_case(rains[peak_i], p5h, PRF=prf)
        st.write(f"Peak day = **{rains[peak_i]}\"** · 5-day antecedent = **{p5h:.2f}\"** "
                 f"· API = **{api[peak_i]:.2f}** · **ARC-{arc_h}**")
        postures = {bid: r["posture"] for bid, r in res_h.items()}
        mh, _ = posture_map(postures)
        legend()
        st_folium(mh, center=WS_CENTER, zoom=WS_ZOOM,
                  height=480, width=1150, returned_objects=[])
        rows = [{"sub-basin": disp(bid), "stage (ft)": round(r["stage"], 2),
                 "posture": r["posture"]} for bid, r in res_h.items()]
        st.dataframe(pd.DataFrame(rows).style.apply(color_posture, subset=["posture"]),
                     width="stretch", hide_index=True)
    except ValueError:
        st.error("Enter comma-separated numbers, e.g. 0.3, 0.8, 1.2, ...")

with tab4:
    st.subheader("Live — real forecast + modeled antecedent (no sensors)")
    st.caption("Shadow mode: real weather, modeled soil wetness. "
               "Forecasts under-call mountain rainfall — for validation and awareness, "
               "not a basis for public warnings until rain gauges correct that bias.")

    @st.cache_data(ttl=1800, show_spinner="Fetching live rainfall…")
    def _live(prf):
        import live_rainfall as lr
        return lr.run_live(PRF=prf)

    if st.button("Refresh live data"):
        _live.clear()
    try:
        live = _live(prf)
        postures = {bid: v["posture"] for bid, v in live.items()}
        cc = st.columns(4)
        for i, p in enumerate(["NORMAL", "WATCH", "WARNING", "EMERGENCY"]):
            cc[i].metric(p, sum(1 for v in live.values() if v["posture"] == p))

        m, _ = posture_map(postures)
        legend()
        st_folium(m, center=WS_CENTER, zoom=WS_ZOOM,
                  height=520, width=1150, returned_objects=[])

        wl = {1: "dry", 2: "normal", 3: "wet"}
        rows = [{"sub-basin": disp(bid),
                 "antecedent 5d (in)": v["antecedent_5day"],
                 "soil moisture (est. %)": (f"{v['soil_moisture_pct']}%"
                                            if "soil_moisture_pct" in v else "n/a"),
                 "forecast storm (in)": v["storm"],
                 "pred. depth (ft)": v["stage"],
                 "posture": v["posture"]}
                for bid, v in live.items()]
        st.dataframe(style_live(pd.DataFrame(rows)), width="stretch", hide_index=True)
        st.caption("**Soil moisture** and **pred. depth** are MODELED, not measured. "
                   "Soil moisture = single-layer water-balance estimate (real rainfall − "
                   "real ET, % of assumed capacity) — trust the trend more than the exact "
                   "number until probes calibrate the capacity. Pred. depth = baseflow + "
                   "predicted storm rise (total channel depth, as a gauge would read; "
                   "baseflow is a field-anchored estimate per reach). Both cross-checkable "
                   "against NWM next, real when sensors deploy. "
                   "Rain/ET source: Open-Meteo. Cached 30 min — Refresh to update.")

        st.subheader("Incoming weather")
        st.caption("Animated precipitation radar (RainViewer), regional view so you can "
                   "watch systems approach the watershed. This is OBSERVED precip — a "
                   "different feed from the forecast driving the postures above, so rain "
                   "on radar before a basin changes color is the lead time, not a conflict.")
        st_folium(radar_map(), center=WS_CENTER, zoom=RADAR_ZOOM,
                  height=460, width=1150, returned_objects=[])
    except Exception as e:
        st.error(f"Couldn't fetch live weather (needs internet to api.open-meteo.com): {e}")

st.divider()
st.caption("Tabletop model: triangular UH + rectangular Manning rating + HDc. "
           "Validates logic and the antecedent effect — not a replacement for the "
           "five-module engine's routing.")
