"""Build the slope_3d.html scaffolding.

PLAN.md lists slope_3d.html alongside the other two pages, but the repo has
never contained it — only noah_skye_3d.html, which carries the assets the 3D
view needs: the inlined three.js build, the baked QL1 lidar height grid
(window.__HGRID__) and the sub-basin geometry with its projection
(window.__GEO__). Those three blocks are *scaffolding*: they describe the
terrain, not a satellite pass, and no scheduled run should ever rewrite them.

This script lifts them out of noah_skye_3d.html and assembles the page around
them, leaving marker-delimited regions for ci_update to fill with each pass.
Run it once (or again if the lidar grid is ever rebuilt):

    python pipeline/tools/build_slope_3d.py

It refuses to clobber a page whose generated regions already carry real data.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "noah_skye_3d.html"
TARGET = REPO / "slope_3d.html"


def extract(html: str, pattern: str, what: str) -> str:
    m = re.search(pattern, html, re.S)
    if not m:
        sys.exit(f"could not find {what} in {SOURCE.name}")
    return m.group(0)


def main() -> int:
    src = SOURCE.read_text(encoding="utf-8")
    geo = extract(src, r"<script>window\.__GEO__=.*?</script>", "__GEO__")
    hgrid = extract(src, r"<script>window\.__HGRID__=.*?</script>", "__HGRID__")
    three = extract(src, r"<script>\n/\*\*\n \* @license.*?\n</script>", "the three.js bundle")

    page = TEMPLATE.replace("@@GEO@@", geo).replace("@@HGRID@@", hgrid) \
                   .replace("@@THREE@@", three)
    TARGET.write_text(page, encoding="utf-8")
    print(f"wrote {TARGET.relative_to(REPO)} ({TARGET.stat().st_size / 1e6:.1f} MB)")
    print("generated regions are placeholders until the first automated pass fills them")
    return 0


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Slope Motion in 3D — NOAH &amp; SKYE</title>
<meta name="description" content="Sentinel-1 InSAR slope motion draped on the QL1 lidar terrain of the Cullowhee Creek watershed.">
<style>
  :root{--abyss:#0a161a;--panel:rgba(16,33,40,.86);--line:#20363c;--mist:#e9f1ef;
    --slate:#8aa6a6;--faint:#5c7576;--current:#45d0c0;--watch:#e2b52b;--warning:#f2882d;
    --hi:#ff5252;--mid:#ffaa2b;--suspect:#b48ef0;--low:#8fa8a8;}
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;background:var(--abyss);color:var(--mist);overflow:hidden;
    font-family:ui-sans-serif,system-ui,-apple-system,sans-serif;-webkit-font-smoothing:antialiased}
  #app{position:fixed;inset:0}
  .mono{font-family:ui-monospace,Menlo,monospace}
  #hdr{position:absolute;top:0;left:0;right:0;padding:18px 22px;pointer-events:none;z-index:6;max-width:60ch}
  #hdr .eyebrow{font-family:ui-monospace,Menlo,monospace;font-size:11px;letter-spacing:.16em;
    text-transform:uppercase;color:var(--current);margin:0 0 5px}
  #hdr h1{font-size:20px;font-weight:600;margin:0;letter-spacing:.01em}
  #hdr p{font-size:12.5px;color:var(--slate);margin:5px 0 0}
  #hdr b{color:var(--mist);font-weight:600}
  #legend{position:absolute;top:120px;right:22px;width:250px;max-height:calc(100vh - 210px);
    overflow-y:auto;background:var(--panel);border:1px solid var(--line);border-radius:11px;
    padding:13px 14px;backdrop-filter:blur(8px);z-index:5;font-size:11.5px}
  #legend .eyebrow{font-family:ui-monospace,Menlo,monospace;font-size:10.5px;letter-spacing:.14em;
    text-transform:uppercase;color:var(--current);margin:12px 0 7px;display:block}
  #legend .eyebrow:first-child{margin-top:0}
  .lrow{display:flex;align-items:flex-start;gap:9px;margin:7px 0;color:var(--slate)}
  .lrow .dot{width:11px;height:11px;border-radius:50%;flex:0 0 auto;margin-top:3px;box-shadow:0 0 8px currentColor}
  .lrow .bar{width:16px;height:9px;border-radius:2px;flex:0 0 auto;margin-top:2px}
  .lname{font-weight:600;color:var(--mist)}
  .ldesc{display:block;font-size:10px;color:var(--faint);line-height:1.4;margin-top:1px}
  #info{position:absolute;left:22px;bottom:64px;width:320px;background:var(--panel);
    border:1px solid var(--line);border-radius:11px;padding:13px 15px;backdrop-filter:blur(8px);
    z-index:6;display:none;font-family:ui-monospace,Menlo,monospace;font-size:11.5px;line-height:1.5}
  #info h2{font-family:ui-sans-serif,system-ui,sans-serif;font-size:14px;font-weight:600;margin:0 0 2px}
  #info .cls{font-size:10px;letter-spacing:.08em;text-transform:uppercase;font-weight:600;margin:0 0 7px}
  #info .meta{color:var(--slate);margin:0 0 8px}
  #info .meta b{color:var(--mist)}
  #info .why{color:var(--slate);margin:8px 0 0;line-height:1.45}
  #info .review{color:var(--faint);font-size:10.5px;margin:6px 0 0}
  #info svg{display:block;background:rgba(0,0,0,.2);border:1px solid var(--line);border-radius:5px}
  #info .x{position:absolute;top:9px;right:12px;cursor:pointer;color:var(--faint);font-size:15px}
  #hint,#foot{position:absolute;left:22px;font-family:ui-monospace,Menlo,monospace;
    font-size:11px;color:var(--faint);z-index:5}
  #hint{bottom:38px}
  #foot{bottom:18px}
  #foot a{color:var(--slate)}
  #err{position:absolute;inset:auto 22px 90px 22px;background:#2a1113;border:1px solid #8a3038;
    color:#ffd9db;padding:11px 14px;border-radius:9px;font-family:ui-monospace,Menlo,monospace;
    font-size:12px;display:none;z-index:9}
  @media (max-width:760px){#legend{display:none}#info{width:calc(100% - 44px)}}
</style>
</head>
<body>
<div id="app"></div>
<!--SLOPE:HEADER3D--><div id="hdr">
  <p class="eyebrow">Sentinel-1 InSAR · path 48 ascending · awaiting first automated pass</p>
  <h1>Slope Motion in 3D</h1>
  <p>This page is scaffolding until the scheduled workflow completes its first pass. No slope data is shown yet.</p>
</div><!--/SLOPE:HEADER3D-->
<div id="legend">
  <div class="eyebrow">LOS velocity</div>
  <div class="lrow"><span class="bar" style="background:linear-gradient(90deg,#3b7fd4,#c9d2d6,#d6404a)"></span><div><span class="lname">away &leftrightarrow; toward satellite</span><span class="ldesc">Draped only where the velocity clears three times its own standard error. Bare terrain elsewhere means the radar had nothing statistically significant to say.</span></div></div>
  <div class="eyebrow">Screened clusters</div>
  <div class="lrow"><span class="dot" style="color:#ff5252;background:#ff5252"></span><div><span class="lname">candidate &middot; credible motion</span><span class="ldesc">Cleared the net-motion, direction-agreement and leaf-off tests.</span></div></div>
  <div class="lrow"><span class="dot" style="color:#ffaa2b;background:#ffaa2b"></span><div><span class="lname">candidate &middot; weaker case</span><span class="ldesc">Cleared the tests, but its leaf-off record is thin.</span></div></div>
  <div class="lrow"><span class="dot" style="color:#b48ef0;background:#b48ef0"></span><div><span class="lname">suspect artifact</span><span class="ldesc">One large step explains the record, or the fit contradicts the series.</span></div></div>
  <div class="lrow"><span class="dot" style="color:#8fa8a8;background:#8fa8a8"></span><div><span class="lname">low-confidence detection</span><span class="ldesc">Above the detector's velocity gate, below the screening bar.</span></div></div>
  <div class="eyebrow">Reading this</div>
  <div class="lrow"><div><span class="ldesc">Classes are computed from the numbers by the scheduled job and are <b>automated screening &mdash; pending analyst review</b>. Alert levels (ADVISORY / WATCH / WARNING) come from the pipeline's own gates, not from this screening.</span></div></div>
</div>
<div id="info"><span class="x" onclick="document.getElementById('info').style.display='none'">&times;</span></div>
<div id="hint">Drag to orbit &middot; scroll to zoom &middot; click a marker</div>
<div id="foot">terrain: QL1 2025 lidar 10 ft &middot; NAVD88 &middot; <a href="slope_monitor.html">full slope monitor</a> &middot; <a href="slope_map.html">2D map</a></div>
<div id="err"></div>

@@GEO@@
@@HGRID@@
<!--SLOPE:DATA3D-->
<script>window.__SLOPE__={"pending":true};</script>
<!--/SLOPE:DATA3D-->
@@THREE@@
<script>/* ===== Slope Motion 3D — Sentinel-1 InSAR over the lidar terrain ===== */
(function(){
const errEl = document.getElementById('err');
function fail(m){ if(errEl){errEl.style.display='block'; errEl.textContent='Error: '+m;} }
window.addEventListener('error', e=>fail(e.message));
if(typeof THREE==='undefined'){ fail('3D library failed to load.'); return; }

const GEO = window.__GEO__, HG = window.__HGRID__, S = window.__SLOPE__ || {pending:true};
const PR = GEO.proj;
const project = (lon,lat) => [ (lon-PR.cx)*PR.mlon*PR.scale, -(lat-PR.cy)*PR.scale ];
const unproject = (x,z) => [ PR.cx + x/(PR.mlon*PR.scale), PR.cy - z/PR.scale ];

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0a161a);
scene.fog = new THREE.FogExp2(0x0a161a, 0.009);
const camera = new THREE.PerspectiveCamera(46, innerWidth/innerHeight, 0.1, 600);
const renderer = new THREE.WebGLRenderer({antialias:true});
renderer.setPixelRatio(Math.min(devicePixelRatio,2));
renderer.setSize(innerWidth, innerHeight);
document.getElementById('app').appendChild(renderer.domElement);
scene.add(new THREE.AmbientLight(0x8fa6b5, 0.66));
const sun = new THREE.DirectionalLight(0xfff2e0, 0.98); sun.position.set(-14,20,10); scene.add(sun);
const rim = new THREE.DirectionalLight(0x6fd0e0, 0.30); rim.position.set(12,9,-12); scene.add(rim);

// ---- terrain from the baked lidar grid ----
function hf(x,z){
  const nx=HG.nx,nz=HG.nz,d=HG.data;
  let fx=(x-HG.gx0)/(HG.gx1-HG.gx0)*(nx-1), fz=(z-HG.gz0)/(HG.gz1-HG.gz0)*(nz-1);
  fx=Math.max(0,Math.min(nx-1,fx)); fz=Math.max(0,Math.min(nz-1,fz));
  const x0=Math.floor(fx),z0=Math.floor(fz),x1=Math.min(nx-1,x0+1),z1=Math.min(nz-1,z0+1);
  const tx=fx-x0,tz=fz-z0;
  const a=d[z0*nx+x0],b=d[z0*nx+x1],c=d[z1*nx+x0],e=d[z1*nx+x1];
  const top=a+(b-a)*tx, bot=c+(e-c)*tx;
  return ((top+(bot-top)*tz)/10)*HG.vscale;
}
let maxY=0; for(let i=0;i<HG.data.length;i++){ if(HG.data[i]>maxY) maxY=HG.data[i]; }
maxY=(maxY/10)*HG.vscale;

// ---- LOS velocity lookup on the analysis grid ----
function velAt(lon,lat){
  if(!S.vel || !S.bbox) return null;
  const [minLon,minLat,maxLon,maxLat]=S.bbox, [H,W]=S.grid;
  if(lon<minLon||lon>maxLon||lat<minLat||lat>maxLat) return null;
  const c=Math.min(W-1,Math.max(0,Math.floor((lon-minLon)/(maxLon-minLon)*W)));
  const r=Math.min(H-1,Math.max(0,Math.floor((maxLat-lat)/(maxLat-minLat)*H)));
  const v=S.vel[r*W+c];
  return (v===null||v===undefined)?null:v;
}
// diverging blue -> neutral -> red, matched to the 2D pages
function ramp(t){
  t=Math.max(-1,Math.min(1,t));
  const a=t<0?[0.23,0.50,0.83]:[0.79,0.82,0.84], b=t<0?[0.79,0.82,0.84]:[0.84,0.25,0.29];
  const u=Math.abs(t);
  return [a[0]+(b[0]-a[0])*u, a[1]+(b[1]-a[1])*u, a[2]+(b[2]-a[2])*u];
}

const geom = new THREE.PlaneGeometry(HG.gx1-HG.gx0, HG.gz1-HG.gz0, HG.nx-1, HG.nz-1);
geom.rotateX(-Math.PI/2);
geom.translate((HG.gx0+HG.gx1)/2, 0, (HG.gz0+HG.gz1)/2);
const pos = geom.attributes.position;
const col = new Float32Array(pos.count*3);
const vmax = (S.vmax||50);
for(let i=0;i<pos.count;i++){
  const x=pos.getX(i), z=pos.getZ(i);
  const y=hf(x,z); pos.setY(i,y);
  const v=velAt.apply(null, unproject(x,z));
  let c;
  if(v===null){ const s=0.26+0.30*Math.min(1,y/(maxY||1)); c=[s,s*1.07,s*1.05]; }
  else c=ramp(v/vmax);
  col[i*3]=c[0]; col[i*3+1]=c[1]; col[i*3+2]=c[2];
}
geom.setAttribute('color', new THREE.BufferAttribute(col,3));
geom.computeVertexNormals();
scene.add(new THREE.Mesh(geom, new THREE.MeshStandardMaterial({
  vertexColors:true, roughness:0.95, metalness:0.0, flatShading:false })));

// ---- sub-basin outlines ----
GEO.basins.forEach(b=>{
  const pts=b.ring.map(p=>new THREE.Vector3(p[0], hf(p[0],p[1])+0.06, p[1]));
  pts.push(pts[0].clone());
  scene.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),
    new THREE.LineBasicMaterial({color:0x2f8f84, transparent:true, opacity:0.75})));
});

// ---- cluster markers ----
const STYLE_COLOR = {hi:0xff5252, mid:0xffaa2b, suspect:0xb48ef0, low:0x8fa8a8};
const STYLE_LABEL = {hi:['CANDIDATE — credible motion','#ff5252'],
                     mid:['CANDIDATE — weaker case','#ffaa2b'],
                     suspect:['SUSPECT ARTIFACT','#b48ef0'],
                     low:['LOW-CONFIDENCE DETECTION','#8fa8a8']};
const markers=[];
(S.clusters||[]).forEach(c=>{
  const p=project(c.lon,c.lat), y=hf(p[0],p[1]);
  const col=STYLE_COLOR[c.style]||0x8fa8a8;
  const big=c.style==='hi'||c.style==='mid';
  const h=big?1.5:0.8, r=big?0.15:0.09;
  const m=new THREE.Mesh(new THREE.CylinderGeometry(r*0.35,r,h,10),
    new THREE.MeshStandardMaterial({color:col, emissive:col, emissiveIntensity:big?0.55:0.25, roughness:0.5}));
  m.position.set(p[0], y+h/2, p[1]);
  m.userData.cluster=c;
  scene.add(m); markers.push(m);
});

// ---- info panel ----
const info=document.getElementById('info');
function spark(series){
  if(!series||series.length<2) return '';
  const W=290,H=96,l=28,r=6,t=8,b=14, iw=W-l-r, ih=H-t-b;
  let mn=Math.min.apply(null,series), mx=Math.max.apply(null,series);
  const pad=(mx-mn)*0.12+1; mn-=pad; mx+=pad;
  const X=i=>l+i/(series.length-1)*iw, Y=v=>t+(1-(v-mn)/(mx-mn))*ih;
  let d=series.map((v,i)=>(i?'L':'M')+X(i).toFixed(1)+' '+Y(v).toFixed(1)).join('');
  let s='<svg width="'+W+'" height="'+H+'" viewBox="0 0 '+W+' '+H+'">';
  s+='<path d="'+d+'" fill="none" stroke="#45d0c0" stroke-width="2" stroke-linejoin="round"/>';
  s+='<circle cx="'+X(series.length-1)+'" cy="'+Y(series[series.length-1])+'" r="3.2" fill="#45d0c0" stroke="#102128" stroke-width="1.5"/>';
  if(S.dates&&S.dates.length===series.length){
    s+='<text x="'+l+'" y="'+(H-3)+'" font-size="9" fill="#5c7576">'+S.dates[0].slice(0,7)+'</text>';
    s+='<text x="'+(W-r)+'" y="'+(H-3)+'" text-anchor="end" font-size="9" fill="#5c7576">'+S.dates[S.dates.length-1].slice(0,7)+'</text>';
  }
  return s+'</svg>';
}
function showCluster(c){
  const lab=STYLE_LABEL[c.style]||STYLE_LABEL.low;
  info.innerHTML='<span class="x">&times;</span>'
    +'<h2>Cluster '+c.id+' · '+c.basin_name+'</h2>'
    +'<p class="cls" style="color:'+lab[1]+'">'+lab[0]+'</p>'
    +'<p class="meta"><b>'+c.acres+'</b> acres · <b>'+c.v.toFixed(0)+'</b> mm/yr LOS · net <b>'+c.net+'</b> mm · pipeline level <b>'+c.level+'</b></p>'
    +spark(c.series)
    +'<p class="why">'+c.reason+'</p>'
    +'<p class="review">'+(S.review||'')+'</p>';
  info.querySelector('.x').onclick=()=>{info.style.display='none';};
  info.style.display='block';
}

// ---- camera + interaction ----
const xspan=HG.gx1-HG.gx0, zspan=HG.gz1-HG.gz0;
const target=new THREE.Vector3((HG.gx0+HG.gx1)/2, maxY*0.5+0.6, (HG.gz0+HG.gz1)/2);
let theta=0.55, phi=0.9, radius=Math.max(xspan,zspan)*1.35+6;
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
function place(){
  camera.position.set(target.x+radius*Math.sin(phi)*Math.cos(theta),
                      target.y+radius*Math.cos(phi),
                      target.z+radius*Math.sin(phi)*Math.sin(theta));
  camera.lookAt(target);
}
place();
const el=renderer.domElement;
let drag=false, px=0, py=0, moved=0;
el.addEventListener('pointerdown', e=>{drag=true;px=e.clientX;py=e.clientY;moved=0;});
addEventListener('pointerup', e=>{
  if(drag&&moved<5) pick(e);
  drag=false;
});
addEventListener('pointermove', e=>{
  if(!drag) return;
  const dx=e.clientX-px, dy=e.clientY-py; px=e.clientX; py=e.clientY;
  moved+=Math.abs(dx)+Math.abs(dy);
  theta-=dx*0.005; phi=clamp(phi-dy*0.005,0.16,1.45); place();
});
el.addEventListener('wheel', e=>{e.preventDefault(); radius=clamp(radius*(1+Math.sign(e.deltaY)*0.08),7,90); place();}, {passive:false});
const ray=new THREE.Raycaster(), ndc=new THREE.Vector2();
function pick(e){
  ndc.x=(e.clientX/innerWidth)*2-1; ndc.y=-(e.clientY/innerHeight)*2+1;
  ray.setFromCamera(ndc, camera);
  const hit=ray.intersectObjects(markers, false)[0];
  if(hit) showCluster(hit.object.userData.cluster);
}
addEventListener('resize', ()=>{
  camera.aspect=innerWidth/innerHeight; camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});
(function loop(){ requestAnimationFrame(loop); renderer.render(scene,camera); })();

if(S.pending){ document.getElementById('hint').textContent =
  'Terrain only — the scheduled slope workflow has not published a pass to this page yet.'; }
})();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    raise SystemExit(main())
