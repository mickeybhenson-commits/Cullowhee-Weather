"""
basins.py - Cullowhee Creek sub-basin registry (DATA, source-tagged).

One record per node on the routed mainstem. Read by flood_rating.py (the engine)
and intended to back sources.py's MEASURED > GOV_ESTIMATE > MODELED resolution.
This is the machine-readable form of the per-basin buildsheets.

PROVENANCE OF EVERY NUMBER
  reg_*   : USGS StreamStats regional regression, NC SIR 2023-5006   [GOV_ESTIMATE]
  tva_*   : TVA (1983) FPM-83/51, Tables 2/3 + profiles              [GOV_ESTIMATE]
  section : Bieger (2015) regional bankfull curves + slope + n       [MODELED]
  calib   : maps this model's TR-55/UH peak onto regression          [derived]
  thr_ft  : campus = receptor-validated; others = PLACEHOLDER        [see thr_src]
  learned / stage_sensor : empty until events / sensors arrive       [MEASURED, future]

KEY FINDINGS BAKED IN (see flood_rating.py self-test to reproduce)
  - Every reach's raw TR-55 peak runs 1.9-2.8x over regression; the bias GROWS
    as basins get smaller/flashier, so each carries its own (a,b) calibration.
  - After correction, the 6 tributaries' 10-yr flow is at bankfull (out_of_bank
    ~1.0-1.2), so their rectangular rating is VALID in-bank. Only the campus
    stays out of bank (2.4) and needs the TVA stage rating.

NESTING - these are cumulative points down ONE mainstem, NOT independent areas:
  UP-503 -> MS-1100 -> SPD-1830 -> WCU-2260 -> MOUTH-2340, with TIL, COX, LB as
  tributaries joining along the way. `downstream` encodes it. DRAINAGE AREAS
  OVERLAP and must never be summed. (Confirm exact confluence order vs flood_network.)

WHAT IS STILL OPEN (flagged per record)
  - thr_ft for the 7 non-campus reaches are placeholders; they need a surveyed
    top-of-bank or an observed receptor (the campus has the road datum, they don't).
  - `role` (warning point vs contributor) PENDING for the tributaries; set from map.
  - Long Branch discharge is soft: TVA (~445) and StreamStats (294) disagree 1.5x
    at the 10-yr (both within the StreamStats prediction interval).
"""

# AEP key -> approximate return period, for readability
AEP_RP = {0.50: 2, 0.20: 5, 0.10: 10, 0.04: 25, 0.02: 50, 0.01: 100, 0.005: 200, 0.002: 500}

REG_SRC = "USGS StreamStats regional regression, NC SIR 2023-5006 (6/2026 delineation)"

BASINS = {

 "CC-UP-503": dict(
    name="Upper Cullowhee (headwaters)", lead="limited", role="PENDING-set-from-map",
    da_sqmi=5.03, da_src="StreamStats; geom-independent 5.035 (supersedes old 5.35)",
    pour=(35.23320, -83.18689), downstream="CC-MS-1100",
    reg_q={0.50:269, 0.20:504, 0.10:705, 0.04:987, 0.02:1250, 0.01:1500, 0.005:1780, 0.002:2160},
    reg_pi={0.10:(395,1260), 0.01:(781,2880)}, reg_src=REG_SRC, tva_q=None,
    calib=(1.449, 0.815), calib_anchors=[(1984,705),(5011,1500)],   # (model 10/100-yr -> reg)
    rating="rectangular",
    section=dict(w=29.7, d=1.78, n=0.045, s=0.0888),                 # Bieger App-Highlands-D
    bankfull_curve="Appalachian Highlands D (Blue Ridge eq EXCLUDED: DA 5.03 < 5.46 floor)",
    out_of_bank_10yr=0.99, tva_wse=None, bed_ft=None, learned=None,
    thr_ft=(1.78, 2.67, 3.56), thr_src="PLACEHOLDER: bankfull x(1.0,1.5,2.0); needs surveyed receptor",
    stage_sensor=None,  # buildsheet: anchor MB7067 / confirmation A02YYUW, role-dependent
    note="n=0.045 post-Helene woody-debris (buildsheet, supersedes test_model 0.060)."),

 "CC-MS-1100": dict(
    name="Mainstem above Speedwell (Mtn. Lower)", lead="limited", role="PENDING-set-from-map",
    da_sqmi=11.0, da_src="StreamStats (matches 11.03)",
    pour=(35.28203, -83.18599), downstream="CC-SPD-1830",
    reg_q={0.50:532, 0.20:965, 0.10:1330, 0.04:1830, 0.02:2290, 0.01:2740, 0.005:3220, 0.002:3870},
    reg_pi={0.10:(746,2370), 0.01:(1430,5260)}, reg_src=REG_SRC, tva_q=None,
    calib=(2.777, 0.760), calib_anchors=[(3368,1330),(8719,2740)],
    rating="rectangular",
    section=dict(w=45.7, d=2.32, n=0.045, s=0.0446),                 # Bieger Blue Ridge P (in range)
    bankfull_curve="Blue Ridge P (DA 11 > 5.46 floor)",
    out_of_bank_10yr=1.09, tva_wse=None, bed_ft=None, learned=None,
    thr_ft=(2.32, 3.48, 4.64), thr_src="PLACEHOLDER: bankfull x(1.0,1.5,2.0); needs surveyed receptor",
    stage_sensor=None,
    note="Tc ambiguous (Kirpich 86 vs NRCS-wet 142 min); calibration absorbs the spread."),

 "CC-TIL-705": dict(
    name="Tilley Creek", lead="limited", role="PENDING-set-from-map",
    da_sqmi=7.05, da_src="StreamStats",
    pour=(35.28273, -83.18702), downstream="CC-SPD-1830",   # joins mainstem above Speedwell
    reg_q={0.50:361, 0.20:667, 0.10:927, 0.04:1290, 0.02:1620, 0.01:1950, 0.005:2300, 0.002:2780},
    reg_pi={0.10:(520,1650), 0.01:(1020,3740)}, reg_src=REG_SRC, tva_q=None,
    calib=(2.241, 0.784), calib_anchors=[(2171,927),(5604,1950)],
    rating="rectangular",
    section=dict(w=38.4, d=2.02, n=0.050, s=0.0547),                 # Bieger Blue Ridge P (in range)
    bankfull_curve="Blue Ridge P (DA 7.05 > 5.46 floor)",
    out_of_bank_10yr=1.15, tva_wse=None, bed_ft=None, learned=None,
    thr_ft=(2.02, 3.03, 4.04), thr_src="PLACEHOLDER: bankfull x(1.0,1.5,2.0); needs surveyed receptor",
    stage_sensor=None, note=""),

 "CC-SPD-1830": dict(
    name="Speedwell", lead="limited", role="PENDING-set-from-map",
    da_sqmi=18.3, da_src="StreamStats",
    pour=(35.28534, -83.18393), downstream="CC-WCU-2260",
    reg_q={0.50:829, 0.20:1470, 0.10:2010, 0.04:2740, 0.02:3410, 0.01:4050, 0.005:4740, 0.002:5660},
    reg_pi={0.10:(1130,3580), 0.01:(2110,7770)}, reg_src=REG_SRC, tva_q=None,
    calib=(3.404, 0.739), calib_anchors=[(5635,2010),(14545,4050)],
    rating="rectangular",
    section=dict(w=55.7, d=2.71, n=0.045, s=0.0425),                 # Bieger Blue Ridge P (in range)
    bankfull_curve="Blue Ridge P (DA 18.3 > 5.46 floor)",
    out_of_bank_10yr=1.07, tva_wse=None, bed_ft=None, learned=None,
    thr_ft=(2.71, 4.07, 5.42), thr_src="PLACEHOLDER: bankfull x(1.0,1.5,2.0); needs surveyed receptor",
    stage_sensor=None, note=""),

 "CC-COX-097": dict(
    name="Cox Branch (flashiest)", lead="limited", role="PENDING-set-from-map",
    da_sqmi=0.97, da_src="StreamStats",
    pour=(35.30180, -83.18324), downstream="CC-WCU-2260",
    reg_q={0.50:64.3, 0.20:129, 0.10:186, 0.04:269, 0.02:347, 0.01:426, 0.005:513, 0.002:631},
    reg_pi={0.10:(104,333), 0.01:(221,822)}, reg_src=REG_SRC, tva_q=None,
    calib=(0.600, 0.940), calib_anchors=[(446,186),(1077,426)],
    rating="rectangular",
    section=dict(w=15.0, d=1.11, n=0.045, s=0.1000),                 # Bieger App-Highlands-D
    bankfull_curve="Appalachian Highlands D (Blue Ridge eq EXCLUDED: DA 0.97 < 5.46 floor)",
    out_of_bank_10yr=1.09, tva_wse=None, bed_ft=None, learned=None,
    thr_ft=(1.11, 1.67, 2.22), thr_src="PLACEHOLDER: bankfull x(1.0,1.5,2.0); needs surveyed receptor",
    stage_sensor=None, note="Lead-limited (Tc<120). Below Bieger Blue Ridge floor."),

 "CC-LB-171": dict(
    name="Long Branch", lead="limited", role="contributor-to-campus",
    da_sqmi=1.71, da_src="StreamStats",
    pour=(35.30819, -83.18770), downstream="CC-WCU-2260",   # enters Cullowhee Ck at TVA mile 1.24
    reg_q={0.50:105, 0.20:206, 0.10:294, 0.04:421, 0.02:539, 0.01:658, 0.005:788, 0.002:964},
    reg_pi={0.10:(164,526), 0.01:(342,1270)}, reg_src=REG_SRC,
    tva_q={10:445, 100:965, 500:1470},   # TVA Table 3 @ mouth; DISAGREES w/ reg ~1.5x (in PI)
    calib=(0.677, 0.921), calib_anchors=[(734,294),(1760,658)],
    rating="rectangular",   # corrected 10-yr in-bank (1.18); TVA WSE below is cross-check only
    section=dict(w=19.0, d=1.31, n=0.045, s=0.0753),                 # Bieger App-Highlands-D
    bankfull_curve="Appalachian Highlands D (Blue Ridge eq EXCLUDED: DA 1.71 < 5.46 floor)",
    out_of_bank_10yr=1.18,
    tva_wse={10:(380,2128.2), 100:(830,2130.3), 500:(1275,2131.9)},  # Table 3 XS3 mile 0.44 (open ch)
    bed_ft=None, learned=None,
    thr_ft=(1.31, 1.97, 2.62), thr_src="PLACEHOLDER: bankfull x(1.0,1.5,2.0); needs surveyed receptor",
    stage_sensor=None,
    note="Contributor to campus, not standalone. Discharge soft: TVA 445 vs reg 294 at 10-yr."),

 "CC-WCU-2260": dict(
    name="WCU Campus (warning point)", lead="adequate", role="warning_point",
    da_sqmi=22.6, da_src="StreamStats (matches surveyed 20.8 StreamStats / 22.6 here)",
    pour=(35.30978, -83.18745), downstream="CC-MOUTH-2340",
    reg_q={0.50:996, 0.20:1750, 0.10:2380, 0.04:3230, 0.02:4010, 0.01:4760, 0.005:5560, 0.002:6630},
    reg_pi={0.10:(1330,4240), 0.01:(2480,9130)}, reg_src=REG_SRC,
    tva_q={10:2585, 100:5150, 500:7290},   # TVA Table 2; agrees w/ reg within ~10%
    calib=(4.222, 0.744), calib_anchors=[(4985,2380),(12655,4760)],
    rating="tva",   # ONLY reach genuinely out of bank after correction (2.36) -> needs surveyed rating
    section=dict(w=60.5, d=2.9, n=0.035, s=0.0050),   # Bieger ref only (unused; rating=tva)
    bankfull_curve="Blue Ridge P (ref); reach is leveed/confined, rating from TVA not bankfull",
    out_of_bank_10yr=2.36,
    tva_wse={10:(2580,2079.2), 100:(5155,2081.5), 500:(7305,2082.9)},  # Table 2 XS mile 0.89 (open ch)
    bed_ft=2070.5,   # = 100-yr WSE - 11 ft road datum; CONFIRM warning-point mile / survey thalweg
    learned=None,
    thr_ft=(7.0, 9.0, 11.0), thr_src="VALIDATED: 11 ft = water in road (field); 9/11 bracket TVA 10/100-yr",
    stage_sensor=None,  # Belk rooftop gateway nearby; ultrasonic TBD
    note="Sole TVA-rated reach. 10-yr -> WATCH/WARNING (section-dependent), 100-yr -> EMERGENCY. "
         "WATCH/WARNING split depends on warning-point mile (0.60 -> WARNING, 0.89 -> WATCH); CONFIRM."),

 "CC-MOUTH-2340": dict(
    name="Cullowhee Creek mouth (OUT OF SCOPE)", lead="adequate", role="out_of_scope",
    da_sqmi=23.4, da_src="StreamStats",
    pour=(35.31709, -83.18037), downstream=None,   # enters Tuckasegee (do not route mainstem in)
    reg_q={0.50:1030, 0.20:1800, 0.10:2450, 0.04:3320, 0.02:4120, 0.01:4880, 0.005:5710, 0.002:6800},
    reg_pi={0.10:(1370,4370), 0.01:(2540,9360)}, reg_src=REG_SRC,
    tva_q={10:2450, 100:4880},   # TVA mile 0 sections are Tuckasegee-BACKWATER controlled
    calib=(4.610, 0.742), calib_anchors=[(4724,2450),(11960,4880)],
    rating="none",   # out of scope; TVA mouth sections backwater-controlled, no clean rating
    section=dict(w=61.3, d=2.93, n=0.045, s=0.0050),
    bankfull_curve="Blue Ridge P (ref)",
    out_of_bank_10yr=3.02, tva_wse=None, bed_ft=None, learned=None,
    thr_ft=None, thr_src="N/A - out of scope, downstream bookend only",
    stage_sensor=None,
    note="Held for completeness. Needs a non-backwater section to ever go live; not a warning point."),
}


def basin(bid):
    return BASINS[bid]

def warning_points():
    return [b for b, r in BASINS.items() if r["role"] == "warning_point"]

def routed_order():
    """Topological-ish order upstream->downstream for routing/printing."""
    return ["CC-UP-503", "CC-TIL-705", "CC-MS-1100", "CC-SPD-1830",
            "CC-COX-097", "CC-LB-171", "CC-WCU-2260", "CC-MOUTH-2340"]
