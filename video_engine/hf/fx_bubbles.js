// Stage 4a P13: graphic dialogue bubbles tied to a pin, a region, a normalised point or a declared overlay. They live
// in #ui above the map and are placed every frame; text is short (<= 60 chars) and bubbles stay out of the caption band.
(function(){
const S=window.SCENE, B=S.bubbles;
if(!B||!B.length)return;
const W=S.width, H=S.height, FPS=S.fps, tl=window.__timelines.main, ui=document.getElementById('ui');
const MO=window.__motion||{};
const U=S.units, sx=U?U.safe.x:60*W/1080, st=U?U.safe.top:100*H/1920, sb=U?U.safe.bottom:H-100*H/1920;
const HR=U?U.ref[1]:1920;   // reference canvas height (units.py): font_px and gap_px are px at it
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
const items=B.map((b,i)=>{
  const el=document.createElement('div');el.className='bubble'+(b.style==='dark'?' dark':'')+' s-'+(b.side==='auto'?'top':b.side);
  el.setAttribute('data-qa','bubble:'+i);el.textContent=b.text;
  el.style.fontSize=(b.font_px*H/HR)+'px';
  el.style.maxWidth=(b.max_width_pct*W)+'px';
  el.style.setProperty('--tail-colour',b.style==='dark'?'#15181d':'#fff');
  ui.appendChild(el);
  const t0=b.at_frame/FPS, pop=b.pop_frames/FPS;
  tl.set(el,{opacity:0,scale:0.72},t0);
  tl.fromTo(el,{opacity:0,scale:0.72},{opacity:1,scale:1,duration:pop,ease:MO.entrance||'power2.out',immediateRender:true},t0);
  tl.set(el,{visibility:'visible'},t0);
  if(b.out_frame!==null&&b.out_frame!==undefined){
    const t1=b.out_frame/FPS;
    tl.to(el,{opacity:0,scale:0.9,duration:pop,ease:MO.exit||'power2.in'},t1);
    tl.set(el,{visibility:'hidden'},t1+pop);
  }
  gsap.set(el,{xPercent:0,yPercent:0});
  return {b,el};
});
const anchorAt=a=>{
  if(a.kind==='xy'){
    if(a.px!==undefined)return [a.px,a.py];
    return [a.x*W,a.y*H];
  }
  if(a.kind==='pin'){
    const p=document.querySelectorAll('#cam .pin')[a.index];
    if(!p)return null;
    const r=p.getBoundingClientRect(), lab=p.querySelector('.lab,.beside'), lr=lab?lab.getBoundingClientRect():null;
    return [r.left+r.width/2,r.top+r.height/2,lr];
  }
  if(a.kind==='region'){
    const p=document.querySelector('[data-region="'+a.index+'"]')||document.querySelector('[data-geo="'+a.index+'"]');
    if(!p)return null;
    const r=p.getBoundingClientRect();return [r.left+r.width/2,r.top+r.height/2];
  }
  return null;
};
const place=()=>{
  items.forEach(({b,el})=>{
    const p=anchorAt(b.anchor);if(!p)return;
    const ax=p[0], ay=p[1], lr=p[2]||null, bw=el.offsetWidth, bh=el.offsetHeight, gap=b.gap_px*H/HR;
    let side=b.side, left=0, top=0;
    if(side==='auto'){
      if(ax+gap+bw<=W-sx)side='right';
      else if(ax-gap-bw>=sx)side='left';
      else side='top';
    }
    if(side==='top'){left=ax-bw/2;top=Math.min(ay-gap,lr?lr.top-gap:ay-gap)-bh;}
    else if(side==='left'){left=Math.min(ax-gap,lr?lr.left-gap:ax-gap)-bw;top=ay-bh/2;}
    else{left=Math.max(ax+gap,lr?lr.right+gap:ax+gap);top=ay-bh/2;}
    const maxTop=H*b.max_bottom_pct-bh;
    left=clamp(left,sx,W-sx-bw);top=clamp(top,st,Math.max(st,maxTop));
    el.style.left=left+'px';el.style.top=top+'px';
    el.className='bubble'+(b.style==='dark'?' dark':'')+' s-'+side;
    if(side==='top')el.style.setProperty('--tail',clamp(ax-left,24,bw-24)+'px');
    else el.style.setProperty('--tail',clamp(ay-top,20,bh-20)+'px');
  });
};
place();
const D=S.n_frames/FPS, drv={t:0};
window.__fxPlace=place;
if(!S.globe){
  tl.fromTo(drv,{t:0},{t:D,duration:D,ease:'none',immediateRender:true,onUpdate:place},0);
}
})();
