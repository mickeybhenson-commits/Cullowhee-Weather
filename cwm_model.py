#!/usr/bin/env python3
"""
cwm_model.py — runnable reconstruction of the deployed Cullowhee Creek flood
engine (faithful Python port of the JS engine in the live status page), wired to
the authoritative basin registry (basins.py values) for validation work.

Chain per basin:  rainfall -> SCS Type II hyetograph -> NRCS-CN runoff (continuous
CN from antecedent wetness) -> NRCS unit hydrograph -> peak Q -> per-basin
regression calibration -> stage (rectangular Manning or TVA rating) -> posture.
"""
import math

PRF = 484.0
DT = 0.25
API_K = 0.90
API_DAYS = 30
API_5DAY_EQUIV = (1 - API_K ** API_DAYS) / (1 - API_K) / 5.0   # 1.9152

TYPE2 = [0.000,0.011,0.022,0.035,0.048,0.064,0.080,0.098,0.120,0.147,0.181,0.235,
         0.663,0.772,0.820,0.854,0.880,0.902,0.921,0.938,0.953,0.967,0.984,1.000]

# Deployed runnable params + authoritative regression flows (reg_q) from basins.py.
# reg_q AEP keys -> return period: .50=2yr .20=5 .10=10 .04=25 .02=50 .01=100 .005=200 .002=500
BASINS = {
 "CC-UP-503":   dict(DA=5.03, Tc=40,  CN2=63, calib=(1.449,0.815), rating="rectangular",
    sec=dict(w=29.7,n=0.045,s=0.0888), thr=(1.78,2.67,3.56), qb=10.1,
    reg_q={0.50:269,0.20:504,0.10:705,0.04:987,0.02:1250,0.01:1500,0.005:1780,0.002:2160}),
 "CC-MS-1100":  dict(DA=11.03,Tc=63,  CN2=63, calib=(2.777,0.760), rating="rectangular",
    sec=dict(w=45.7,n=0.045,s=0.0446), thr=(2.32,3.48,4.64), qb=22.0,
    reg_q={0.50:532,0.20:965,0.10:1330,0.04:1830,0.02:2290,0.01:2740,0.005:3220,0.002:3870}),
 "CC-TIL-705":  dict(DA=7.05, Tc=62,  CN2=63, calib=(2.241,0.784), rating="rectangular",
    sec=dict(w=38.4,n=0.050,s=0.0547), thr=(2.02,3.03,4.04), qb=14.1,
    reg_q={0.50:361,0.20:667,0.10:927,0.04:1290,0.02:1620,0.01:1950,0.005:2300,0.002:2780}),
 "CC-SPD-1830": dict(DA=18.3, Tc=62,  CN2=63, calib=(3.404,0.739), rating="rectangular",
    sec=dict(w=55.7,n=0.045,s=0.0425), thr=(2.71,4.07,5.42), qb=36.6,
    reg_q={0.50:829,0.20:1470,0.10:2010,0.04:2740,0.02:3410,0.01:4050,0.005:4740,0.002:5660}),
 "CC-COX-097":  dict(DA=0.97, Tc=29,  CN2=66, calib=(0.600,0.940), rating="rectangular",
    sec=dict(w=15.0,n=0.045,s=0.1000), thr=(1.11,1.67,2.22), qb=1.9,
    reg_q={0.50:64.3,0.20:129,0.10:186,0.04:269,0.02:347,0.01:426,0.005:513,0.002:631}),
 "CC-LB-171":   dict(DA=1.71, Tc=36,  CN2=65, calib=(0.677,0.921), rating="rectangular",
    sec=dict(w=19.0,n=0.045,s=0.0753), thr=(1.31,1.97,2.62), qb=3.4,
    reg_q={0.50:105,0.20:206,0.10:294,0.04:421,0.02:539,0.01:658,0.005:788,0.002:964}),
 "CC-WCU-2260": dict(DA=22.6, Tc=127, CN2=64, calib=(4.222,0.744), rating="tva",
    tva_wse={10:(2580,2079.2),100:(5155,2081.5),500:(7305,2082.9)}, bed=2070.5,
    thr=(7.0,9.0,11.0), qb=45.2, floor=4.0,
    reg_q={0.50:996,0.20:1750,0.10:2380,0.04:3230,0.02:4010,0.01:4760,0.005:5560,0.002:6630}),
 "CC-MOUTH-2340":dict(DA=23.4,Tc=147, CN2=64, calib=(4.610,0.742), rating="none",
    thr=None, qb=None,
    reg_q={0.50:1030,0.20:1800,0.10:2450,0.04:3320,0.02:4120,0.01:4880,0.005:5710,0.002:6800}),
}
ORDER = ["CC-UP-503","CC-TIL-705","CC-MS-1100","CC-SPD-1830","CC-COX-097","CC-LB-171","CC-WCU-2260","CC-MOUTH-2340"]

def cn_bounds(cn2):
    return (cn2/(2.281-0.01281*cn2), cn2, cn2/(0.427+0.00573*cn2))   # ARC I, II, III
def cn_from_wetness(cn2, w):
    b = cn_bounds(cn2); w = max(0.0, min(1.0, w))
    return b[0]+(b[1]-b[0])*(w/0.5) if w < 0.5 else b[1]+(b[2]-b[1])*((w-0.5)/0.5)

def storm_hyetograph(total, dt=DT):
    steps = round(24.0/dt); cum=[]
    for k in range(steps+1):
        h=k*dt; i=min(int(h),23); frac=h-i
        cum.append((TYPE2[i]+frac*(TYPE2[min(i+1,23)]-TYPE2[i]))*total)
    return [cum[i+1]-cum[i] for i in range(len(cum)-1)]

def runoff_depth(P, CN):
    S = 1000.0/CN - 10.0
    return 0.0 if P <= 0.2*S else (P-0.2*S)**2/(P+0.8*S)
def incremental_runoff(hyeto, CN):
    cumP=[]; s=0.0
    for p in hyeto: s+=p; cumP.append(s)
    cumQ=[runoff_depth(P,CN) for P in cumP]
    inc=[cumQ[0]]
    for i in range(1,len(cumQ)): inc.append(cumQ[i]-cumQ[i-1])
    return inc, cumQ[-1], cumP[-1]

def unit_hydrograph(DA, TcHr, dt=DT):
    Tp=0.6*TcHr+dt/2.0; Tb=2.67*Tp; qp=PRF*DA/Tp; ords=[]; t=0.0
    while t<=Tb:
        ords.append(max(qp*t/Tp if t<=Tp else qp*(Tb-t)/(Tb-Tp),0.0)); t+=dt
    return ords

def peak_discharge(hyeto, CN, DA, TcHr):
    incr,_,_ = incremental_runoff(hyeto,CN); uh=unit_hydrograph(DA,TcHr)
    h=[0.0]*(len(incr)+len(uh))
    for i,r in enumerate(incr):
        if r<=0: continue
        for j,u in enumerate(uh): h[i+j]+=r*u
    return max(h)

def calibrate_peak(q, bid):
    a,b = BASINS[bid]["calib"]; return a*q**b if q>0 else 0.0

def rect_q(d, sec):
    A=sec["w"]*d; P=sec["w"]+2*d; R=A/P if P>0 else 0
    return (1.49/sec["n"])*A*R**(2.0/3.0)*sec["s"]**0.5
def rect_depth(q, sec, dmax=30.0):
    lo,hi=0.0,dmax
    if rect_q(hi,sec)<q: return hi
    for _ in range(60):
        m=0.5*(lo+hi)
        if rect_q(m,sec)<q: lo=m
        else: hi=m
    return 0.5*(lo+hi)
def tva_rating(rec):
    bed=rec["bed"]; pts=[(q,wse-bed) for q,wse in rec["tva_wse"].values()]
    lx=[math.log(d) for _,d in pts]; ly=[math.log(q) for q,_ in pts]; n=len(lx)
    mx=sum(lx)/n; my=sum(ly)/n
    B=sum((x-mx)*(y-my) for x,y in zip(lx,ly))/sum((x-mx)**2 for x in lx)
    return math.exp(my-B*mx), B
def depth_from_q(q, bid):
    rec=BASINS[bid]
    if q is None or q<=0: return 0.0
    if rec["rating"]=="tva":
        C,B=tva_rating(rec); return (q/C)**(1.0/B)
    if rec["rating"]=="rectangular": return rect_depth(q, rec["sec"])
    return None

def posture(depth, bid):
    t=BASINS[bid]["thr"]
    if t is None or depth is None: return "N/A"
    if depth>=t[2]: return "EMERGENCY"
    if depth>=t[1]: return "WARNING"
    if depth>=t[0]: return "WATCH"
    return "NORMAL"

def stage_total(cq, bid):
    b=BASINS[bid]
    if b["rating"]=="none": return None
    t=depth_from_q((cq or 0)+(b.get("qb") or 0), bid)
    return None if t is None else max(t, b.get("floor",0.0))

def assess(bid, qpf, wetness):
    b=BASINS[bid]; CN=cn_from_wetness(b["CN2"], wetness)
    hyeto=storm_hyetograph(qpf)
    incr, runoff_in, _ = incremental_runoff(hyeto, CN)
    qp=peak_discharge(hyeto, CN, b["DA"], b["Tc"]/60.0)
    cq=calibrate_peak(qp, bid)
    stage=stage_total(cq, bid)
    return dict(bid=bid, CN=round(CN,1), qpf=qpf, wet=wetness,
                runoff_in=round(runoff_in,2), runoff_ratio=round(runoff_in/qpf,2) if qpf else None,
                qp_raw=round(qp), calib_q=round(cq), stage=round(stage,1) if stage is not None else None,
                posture=posture(stage,bid))

def reg_return_period(q, bid):
    """Where does discharge q fall on the regression flood-frequency curve? (interpolated years)"""
    aep_rp=[(0.50,2),(0.20,5),(0.10,10),(0.04,25),(0.02,50),(0.01,100),(0.005,200),(0.002,500)]
    rq=BASINS[bid]["reg_q"]
    pts=sorted(((rq[a],rp) for a,rp in aep_rp), key=lambda t:t[0])
    if q<=pts[0][0]: return f"<{pts[0][1]}"
    if q>=pts[-1][0]: return f">{pts[-1][1]}"
    for i in range(len(pts)-1):
        (q0,r0),(q1,r1)=pts[i],pts[i+1]
        if q0<=q<=q1:
            f=(math.log(q)-math.log(q0))/(math.log(q1)-math.log(q0))
            return round(r0+f*(r1-r0))
    return "?"
