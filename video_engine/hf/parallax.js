// parallax layers (gsap_scene_config.layers): repeating full-frame images over the map, each moving with the camera
// at its own depth. Loaded after the template scripts of a scene with layers (hyperframes.build_dir); the per-frame
// math is video_engine/parallax.py `state` (same formula). The camera of each frame comes from the shared legs.
(function(){
const S=window.SCENE, Y=S.layers;
if(!Y||!Y.length)return;
const tl=window.__timelines.main, FPS=S.fps, W=S.width, H=S.height, C=S.camera, LG=window.__legs(C,FPS);
const camAt=t=>{let prev=C.start;
  for(const g of LG){if(t<g.t0+g.d){if(t<=g.t0)return prev;const e=g.ease((t-g.t0)/g.d),a=g.from,b=g.L.to;
    return {x:a.x+(b.x-a.x)*e,y:a.y+(b.y-a.y)*e,s:a.s+(b.s-a.s)*e};}prev=g.L.to;}
  return prev;};
const anchor=document.getElementById('props-map')||document.getElementById('cam');
const els=Y.map(e=>{const el=document.createElement('div');el.setAttribute('data-layer',e.id);
  el.style.cssText='position:absolute;left:0;top:0;width:'+W+'px;height:'+H+'px;pointer-events:none;background-image:url("'+
    e.img+'");background-repeat:repeat;opacity:'+e.op+';mix-blend-mode:'+e.blend;return el;});
els.slice().reverse().forEach(el=>anchor.after(el));   // in scene order, over the map props
const r=C.start, rx=(W/2-r.x)/r.s, ry=(H/2-r.y)/r.s;
const up=t=>{const c=camAt(t), cx=(W/2-c.x)/c.s, cy=(H/2-c.y)/c.s;
  Y.forEach((e,i)=>{const k=Math.pow(c.s/r.s,1+e.d);
    const x=W/2-r.s*k*(1+e.d)*(cx-rx)+k*e.v[0]*t, y=H/2-r.s*k*(1+e.d)*(cy-ry)+k*e.v[1]*t;
    els[i].style.backgroundSize=(e.w*k)+'px '+(e.h*k)+'px';
    els[i].style.backgroundPosition=x+'px '+y+'px';});};
const D=S.n_frames/FPS, drv={t:0};
tl.fromTo(drv,{t:0},{t:D,duration:D,ease:'none',immediateRender:true,onUpdate:()=>up(drv.t)},0);
up(0);
window.__parallax={update:up};
})();
