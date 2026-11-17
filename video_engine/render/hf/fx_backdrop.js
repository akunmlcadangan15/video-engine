// Stage 4a P12/P14: screen-fixed grid and deterministic starfield behind the map or globe. Data comes from
// video_engine/fx.py (grid cell size, star positions from a seeded PRNG), so two renders of one scene are identical.
// The canvas is the root's first child; hf/globe.js prepends its own transparent canvas in front of it later.
(function(){
const S=window.SCENE, B=S.backdrop;
if(!B)return;
const W=S.width, H=S.height, FPS=S.fps, tl=window.__timelines.main;
const root=document.getElementById('root');
const cv=document.createElement('canvas');
cv.width=W;cv.height=H;cv.setAttribute('data-fx-backdrop','');
cv.style.cssText='position:absolute;left:0;top:0;width:'+W+'px;height:'+H+'px;pointer-events:none';
root.insertBefore(cv,root.firstChild);
const mark=document.createElement('div');mark.setAttribute('data-qa','fx:backdrop');
mark.style.cssText='position:absolute;left:0;top:0;width:0;height:0';root.appendChild(mark);
const x=cv.getContext('2d'), G=B.grid||null, ST=B.stars||null;
const grid=()=>{if(!G)return;
  x.fillStyle=G.bg;x.fillRect(0,0,W,H);
  x.strokeStyle=G.colour;x.globalAlpha=G.alpha;x.lineWidth=G.line_px;
  const c=G.cell_px, x0=(W/2)%c, y0=(H/2)%c;
  x.beginPath();
  for(let px=x0;px<=W+c;px+=c){x.moveTo(px,0);x.lineTo(px,H);}
  for(let py=y0;py<=H+c;py+=c){x.moveTo(0,py);x.lineTo(W,py);}
  x.stroke();x.globalAlpha=1;};
const viewAt=t=>{const G3=S.globe;if(!G3||!G3.views||!G3.views.length)return null;
  const i=Math.min(G3.views.length-1,Math.max(0,Math.floor(t*FPS)));return G3.views[i];};
const stars=t=>{if(!ST)return;
  const v=viewAt(t), par=ST.parallax||0;
  const dx=v?-(v[1]/180)*par*0.05*W:0, dy=v?(v[0]/90)*par*0.03*H:0;
  for(const s of ST.stars){
    let sx=s.x*W+dx, sy=s.y*H+dy;
    sx=((sx%W)+W)%W;sy=((sy%H)+H)%H;
    const tw=1+(ST.twinkle||0)*Math.sin(s.p+2*Math.PI*t/s.s);
    x.globalAlpha=Math.max(0,Math.min(1,s.b*tw));
    x.fillStyle='#ffffff';
    x.beginPath();x.arc(sx,sy,Math.max(0.5,s.r),0,Math.PI*2);x.fill();
  }
  x.globalAlpha=1;};
const draw=t=>{x.clearRect(0,0,W,H);grid();stars(t);};
const D=S.n_frames/FPS, drv={t:0};
tl.fromTo(drv,{t:0},{t:D,duration:D,ease:'none',immediateRender:true,onUpdate:()=>draw(drv.t)},0);
draw(0);
// P12 graticule: latitude/longitude lines in map pixels over the map, below regions and pins; they follow the camera
if(G&&G.graticule&&G.lines&&G.lines.length){
  const map=document.getElementById('map'), NS='http://www.w3.org/2000/svg';
  const svg=document.createElementNS(NS,'svg');
  svg.setAttribute('width',S.map_size[0]);svg.setAttribute('height',S.map_size[1]);
  svg.setAttribute('data-fx-graticule','');
  svg.style.cssText='position:absolute;left:0;top:0;overflow:visible;pointer-events:none';
  for(const ln of G.lines){
    const e=document.createElementNS(NS,'path');
    e.setAttribute('d','M'+ln[0][0]+' '+ln[0][1]+' L'+ln[1][0]+' '+ln[1][1]);
    e.setAttribute('fill','none');e.setAttribute('stroke',G.graticule_colour||G.colour);
    e.setAttribute('stroke-opacity',G.graticule_alpha??G.alpha);e.setAttribute('stroke-width',G.line_px);
    e.setAttribute('vector-effect','non-scaling-stroke');svg.appendChild(e);
  }
  if(map)map.after(svg);
}
})();
