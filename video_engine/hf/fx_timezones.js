// Stage 4a P17: vertical UTC bands on a flat map. Band geometry is in map pixels from video_engine/fx.py, so the
// bands follow the camera; labels are projected and clamped to the safe area at a constant on-screen size.
(function(){
const S=window.SCENE, T=S.timezones;
if(!T||!T.bands||S.globe)return;
const W=S.width, H=S.height, FPS=S.fps, C=S.camera, tl=window.__timelines.main;
const ui=document.getElementById('ui'), sx=60*W/1080, st=100*H/1920, sb=H-100*H/1920;
const legs=window.__legs(C,FPS);
const camAt=t=>{let x=C.start.x,y=C.start.y,s=C.start.s;
  for(const g of legs){if(t<g.t0)break;
    if(t>=g.t0+g.d){x=g.L.to.x;y=g.L.to.y;s=g.L.to.s;}
    else{const p=g.ease((t-g.t0)/g.d);x=g.from.x+(g.L.to.x-g.from.x)*p;y=g.from.y+(g.L.to.y-g.from.y)*p;s=g.from.s+(g.L.to.s-g.from.s)*p;}}
  return {x,y,s};};
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
const NS='http://www.w3.org/2000/svg', mk=(tag,at)=>{const e=document.createElementNS(NS,tag);for(const k in at)e.setAttribute(k,at[k]);return e;};
const map=document.getElementById('map'), svg=mk('svg',{width:S.map_size[0],height:S.map_size[1]});
svg.setAttribute('data-fx-timezones','');svg.style.cssText='position:absolute;left:0;top:0;overflow:visible;pointer-events:none';
const t0=T.at_frame/FPS, labs=[];
T.bands.forEach((b,i)=>{
  const e=mk('rect',{x:b.x0,y:0,width:Math.max(0,b.x1-b.x0),height:S.map_size[1],fill:b.colour,'fill-opacity':T.alpha});
  e.setAttribute('data-tz-band',i);svg.appendChild(e);
  tl.fromTo(e,{opacity:0},{opacity:1,duration:0.4,ease:window.__motion.entrance,immediateRender:true},t0);
  const lab=document.createElement('div');lab.className='tzl';
  lab.innerHTML='<div class="pb" data-qa="tz:'+i+'"></div>';
  lab.firstChild.textContent=b.label;lab.firstChild.style.fontSize='var(--lab-size)';   // same size as pin labels
  lab.firstChild.style.transform='translate(-50%,-50%)';ui.appendChild(lab);
  labs.push({el:lab,x:b.lx,y:b.ly});
  tl.set(lab,{visibility:'visible'},t0);
});
if(map)map.after(svg);
const mark=document.createElement('div');mark.setAttribute('data-qa','fx:timezones');
mark.style.cssText='position:absolute;left:0;top:0;width:0;height:0';ui.appendChild(mark);
const place=()=>{const c=camAt(tl.time());
  labs.forEach(o=>{const w=o.el.firstChild.offsetWidth/2, h=o.el.firstChild.offsetHeight/2;
    const x=clamp(c.x+c.s*o.x,sx+w,W-sx-w), y=clamp(c.y+c.s*o.y,st+h,sb-h);
    o.el.style.left=x+'px';o.el.style.top=y+'px';});};
const D=S.n_frames/FPS, drv={t:0};
tl.fromTo(drv,{t:0},{t:D,duration:D,ease:'none',immediateRender:true,onUpdate:place},0);
place();
})();
