"""
confluence_panel.py - renders the Cullowhee Creek x Tuckasegee River confluence
status card in the NOAH Streamlit console (streamlit_app.py).

The console's own engine (flood_network) has no Cullowhee/Tuckasegee confluence
node, so the mouth never got a status. This panel adds one, driven by the live
Tuckasegee gauge (USGS 03508050 / NWS TKRN7) and the console's existing creek/
campus posture. Kept separate so it can't break the rest of the console.

WIRING (add to streamlit_app.py, right after the monitoring-sites grid, once
`lvl`, `SEV`, and `ORDER` exist):

    try:
        import confluence_panel
        confluence_panel.render(st, SEV, ORDER, creek_level=lvl)
    except Exception as _e:
        st.caption(f"Confluence panel unavailable: {_e}")

`lvl` is the console's headline creek/campus level; it is used as the creek-side
signal at the mouth. The Tuckasegee backwater side comes from the live gauge.
The card posts the WORSE of the two (max), matching confluence_status.
"""

import confluence_status as cstat


def combined(creek_level, gage_ht_ft, ORDER):
    """Confluence posture = worse of (creek-side level, Tuckasegee backwater).
    Returns (confluence, river_cat, gauge_wse, driver). Pure logic; testable."""
    river_cat, gauge_wse = cstat.backwater_posture(gage_ht_ft) if gage_ht_ft is not None else ("N/A", None)
    cand = [creek_level] + ([river_cat] if river_cat != "N/A" else [])
    conf = max(cand, key=lambda c: ORDER.index(c) if c in ORDER else 0)
    if conf == "NORMAL":
        driver = "—"
    elif river_cat in ORDER and ORDER.index(river_cat) >= ORDER.index(creek_level):
        driver = "Tuckasegee backwater"
    else:
        driver = "Cullowhee Creek"
    return conf, river_cat, gauge_wse, driver


def _fetch_gauge(st):
    @st.cache_data(ttl=300, show_spinner=False)
    def _f():
        try:
            return cstat.fetch_gauge_live()
        except Exception as e:
            return {"error": str(e)}
    return _f()


def render(st, SEV, ORDER, creek_level="NORMAL"):
    """Render the confluence card. `st` is streamlit; SEV/ORDER are the console's
    color map and severity order; creek_level is the headline creek/campus level."""
    st.markdown('<div class="eyebrow">Downstream confluence — Cullowhee Creek at the Tuckasegee River</div>',
                unsafe_allow_html=True)
    g = _fetch_gauge(st)
    gh = g.get("gage_ht_ft") if isinstance(g, dict) else None
    q = g.get("discharge_cfs") if isinstance(g, dict) else None
    conf, river_cat, gauge_wse, driver = combined(creek_level, gh, ORDER)
    cc = SEV.get(conf, "#8A97A4")
    rc = SEV.get(river_cat, "#8A97A4")
    s = cstat.NWS_FLOOD_STAGES

    if gh is not None:
        river_line = (f'Tuckasegee gauge <b class="mono">{gh:.1f} ft</b>'
                      + (f' · {q:,.0f} cfs' if q is not None else '')
                      + f' &rarr; backwater <span style="color:{rc};font-weight:700;">{river_cat}</span> '
                      f'(NWS action {s["action"]:.0f} / minor {s["minor"]:.0f} / moderate {s["moderate"]:.0f} ft)')
    else:
        err = (g.get("error", "") if isinstance(g, dict) else "") or ""
        river_line = f'Tuckasegee gauge unavailable — creek-side signal only. {err[:60]}'

    st.markdown(
        f'<div class="card" style="border-left:4px solid {cc};">'
        f'<div class="site-name">Cullowhee Creek mouth '
        f'<span class="site-role">confluence · homes at risk</span></div>'
        f'<div class="site-coord mono">35.3171°N 83.1804°W · USGS 03508050 / NWS TKRN7 (above confluence)</div>'
        f'<div class="site-level" style="color:{cc};">{conf}</div>'
        f'<div class="site-detail">Worse of two mechanisms — creek runoff '
        f'<span style="color:{SEV.get(creek_level, "#888")};font-weight:700;">{creek_level}</span> '
        f'vs {river_line}. Driver: <b>{driver}</b>.</div>'
        f'<div class="site-detail" style="color:#8A97A4;">Backwater from the Tuckasegee — not creek flow — '
        f'controls the mouth; the gauge sits upstream, so it also gives warning lead. '
        f'Receptor (home) floor elevations pending survey.</div>'
        f'</div>', unsafe_allow_html=True)
    return conf
