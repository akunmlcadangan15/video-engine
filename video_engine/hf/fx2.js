// Stage 4b visual components (gsap_scene_config.flag_characters | cutaway | insets | particles):
// P15 flag-icon characters, P16 cross-section + framed insets, P19 deterministic particles. Geoid (P18) lives in
// globe.js because it needs the three.js scene. Everything here is analytic in the frame: no wall clock, no
// Math.random, so a seek to any frame is identical. Loaded after the template scripts.
(function(){
const S=window.SCENE;
if(!S||(!S.flag_characters&&!S.cutaway&&!S.insets&&!S.particles))return;
const W=S.width,H=S.height,FPS=S.fps,tl=window.__timelines.main,root=document.getElementById('root');
const css=document.createElement('style');
css.textContent='#fx2{position:absolute;left:0;top:0;width:'+W+'px;height:'+H+'px;pointer-events:none;overflow:hidden}'+
  '.fxflag{position:absolute;left:0;top:0;visibility:hidden}'+
  '.fxcut{position:absolute;left:0;top:0;visibility:hidden}'+
  '.fxband{position:absolute;transform-origin:0 50%;will-change:transform}'+
  '.fxlab{position:absolute;white-space:nowrap;font-family:EngInter,sans-serif;font-weight:700;line-height:1;'+
  'text-shadow:0 2px 6px rgba(0,0,0,.55)}'+
  '.fxinset{position:absolute;left:0;top:0;background:#fff;visibility:hidden;box-sizing:border-box;'+
  'box-shadow:0 8px 24px rgba(0,0,0,.38)}'+
  '.fxinset img{display:block;width:100%;height:100%;object-fit:cover}';
document.head.appendChild(css);
const layer=document.createElement('div');layer.id='fx2';
const ui=document.getElementById('ui');root.insertBefore(layer,ui||null);
const clamp=(v,a,b)=>Math.min(b,Math.max(a,v));
const easeOut=p=>1-Math.pow(1-clamp(p,0,1),3);
const lcg=seed=>{let a=seed>>>0;return()=>{a=(1664525*a+1013904223)%4294967296;return a/4294967296;};};
const updaters=[];
// screen point of pin i at frame f: flat maps from the camera legs (the #cam transform translate(x,y) scale(s), as
// in fx_timezones.js; the DOM transform may lag one tween behind inside a seek), a globe from the pin element that
// globe.js has just placed (globe.js calls __fx2.update after each draw). null = hidden (far side of the globe).
const CAM=S.camera, LEGS=CAM&&window.__legs?window.__legs(CAM,FPS):[];
const camAt=t=>{let x=CAM.start.x,y=CAM.start.y,s=CAM.start.s;
  for(const g of LEGS){if(t<g.t0)break;
    if(t>=g.t0+g.d){x=g.L.to.x;y=g.L.to.y;s=g.L.to.s;}
    else{const p=g.ease((t-g.t0)/g.d);x=g.from.x+(g.L.to.x-g.from.x)*p;y=g.from.y+(g.L.to.y-g.from.y)*p;s=g.from.s+(g.L.to.s-g.from.s)*p;}}
  return {x,y,s};};
const pinAt=(i,f)=>{const p=(S.pins||[])[i];if(!p)return null;
  if(S.globe){const el=document.querySelector('[data-qa="anchor:pin:'+i+'"]');
    if(!el||el.style.display==='none')return null;const r=el.getBoundingClientRect();return {x:r.left,y:r.top};}
  if(!CAM||!CAM.start)return {x:p.px,y:p.py};
  const c=camAt(f/FPS);return {x:c.x+c.s*p.px,y:c.y+c.s*p.py};};
window.__fx2pinAt=pinAt;
// inset box beside its anchor (fx2.inset_blocks rule): away from the nearer frame edge, above, clamped to the frame
// and clear of the pin's own label (lab = its screen rect): the box starts past the label's far edge
const placeInset=(ins,a,lab)=>{const w=ins.w,h=ins.h,g=ins.gap,m=ins.margin,side=a.x>W/2?-1:1;
  const near=lab?(side>0?Math.max(a.x,lab.right):Math.min(a.x,lab.left)):a.x;
  const cx=clamp(near+side*(w/2+g),m+w/2,W-m-w/2), cy=clamp(a.y-h/2-g,m+h/2,H-m-h/2);
  let lx=clamp(a.x,cx-w/2,cx+w/2), ly=clamp(a.y,cy-h/2,cy+h/2);
  if(lx===a.x&&ly===a.y){const pts=[[cx-w/2,a.y],[cx+w/2,a.y],[a.x,cy-h/2],[a.x,cy+h/2]];
    [lx,ly]=pts.reduce((b,q)=>Math.hypot(q[0]-a.x,q[1]-a.y)<Math.hypot(b[0]-a.x,b[1]-a.y)?q:b);}
  return {x:cx,y:cy,lx,ly,ax:a.x,ay:a.y};};

// ---------------------------------------------------------------- P15 flag characters
const chars=(S.flag_characters||[]).map(c=>{
  const el=document.createElement('div');el.className='fxflag';el.setAttribute('data-qa',c.id);
  el.style.width=c.w+'px';el.style.height=c.h+'px';
  el.style.filter='drop-shadow(0 '+c.shadow_px+'px '+(c.shadow_px*1.4)+'px rgba(0,0,0,.35))';
  const cloth=document.createElement('div');
  cloth.style.cssText='position:absolute;inset:0;overflow:hidden;border-radius:'+(c.h*0.035)+'px';
  const strips=[],n=Math.max(4,c.slices),sw=c.w/n;
  for(let j=0;j<n;j++){
    const s=document.createElement('div');
    s.style.cssText='position:absolute;left:'+(j*sw)+'px;top:0;width:'+(sw+1.5)+'px;height:100%;'+
      'background-image:url("'+c.href+'");background-size:'+c.w+'px '+c.h+'px;background-position:'+(-j*sw)+'px 0;'+
      'background-repeat:no-repeat;transform-origin:50% 50%;will-change:transform';
    cloth.appendChild(s);strips.push(s);
  }
  el.appendChild(cloth);
  const eyes=document.createElement('div');
  eyes.style.cssText='position:absolute;left:50%;top:38%;transform:translate(-50%,-50%);display:flex;'+
    'gap:'+(c.eye_px*0.45)+'px;transform-origin:50% 50%;pointer-events:none';
  const pupils=[];
  for(let k=0;k<2;k++){
    const eye=document.createElement('div');
    eye.style.cssText='width:'+c.eye_px+'px;height:'+c.eye_px+'px;border-radius:50%;background:#fff;'+
      'border:'+Math.max(1,c.eye_px*0.08)+'px solid rgba(0,0,0,.2);position:relative;'+
      'box-shadow:inset 0 0 '+c.eye_px*0.18+'px rgba(0,0,0,.16)';
    const p=document.createElement('div');
    p.style.cssText='position:absolute;width:'+c.eye_px*0.42+'px;height:'+c.eye_px*0.42+'px;border-radius:50%;'+
      'background:#101418;left:50%;top:50%;transform:translate(-50%,-50%)';
    eye.appendChild(p);eyes.appendChild(eye);pupils.push(p);
  }
  el.appendChild(eyes);layer.appendChild(el);
  return {c:c,el:el,strips:strips,pupils:pupils,eyes:eyes};
});
if(chars.length)updaters.push(f=>{
  chars.forEach(o=>{
    const c=o.c;
    if(!(f>=c.at_frame&&f<c.out_frame)){o.el.style.visibility='hidden';return;}
    const op=clamp(Math.min((f-c.at_frame)/5,(c.out_frame-f)/5),0,1);
    o.el.style.opacity=String(op);
    o.el.style.visibility=op>0?'visible':'hidden';
    o.el.style.transform='translate('+(c.x-c.w/2)+'px,'+(c.y-c.h/2)+'px)';
    if(c.wave){
      const u=(f-c.at_frame)/FPS;
      o.strips.forEach((s,j)=>{
        const ph=u*2*Math.PI*c.wave_hz-j/(Math.max(1,o.strips.length-1))*1.9*Math.PI;
        const dy=Math.sin(ph)*c.wave_amp,sy=1.12+Math.cos(ph)*0.03;
        s.style.transform='translateY('+dy+'px) scaleY('+sy+') skewY('+(Math.cos(ph)*1.1)+'deg)';
      });
    }
    const so=c.eye_px*0.18;let lk=c.look;
    if(c.look_pin!==undefined){const a=pinAt(c.look_pin,f);
      if(a){const dx=a.x-c.x,dy=a.y-c.y,d=Math.hypot(dx,dy)||1;lk=[dx/d,dy/d];}}
    o.pupils.forEach(p=>{p.style.transform='translate(calc(-50% + '+(lk[0]*so)+'px),calc(-50% + '+(lk[1]*so)+'px))';});
    const per=Math.max(1,c.blink_period_f),off=c.seed%per,idx=Math.floor((f+off)/per),start=idx*per-off;
    const bp=(f-start)/Math.max(1,c.blink_frames);
    const open=(bp>=0&&bp<=1)?Math.abs(bp*2-1):1;
    o.eyes.style.transform='translate(-50%,-50%) scaleY('+open.toFixed(4)+')';
  });
});

// ---------------------------------------------------------------- P16 cutaway
const cut=S.cutaway;
if(cut){
  const el=document.createElement('div');el.className='fxcut';el.setAttribute('data-qa','cutaway');
  el.style.left=cut.x+'px';el.style.top=cut.y+'px';el.style.width=cut.w+'px';el.style.height=cut.h+'px';
  const dx=cut.diagram[0]-cut.x,dy=cut.diagram[1]-cut.y,dw=cut.diagram[2],dh=cut.diagram[3],bands=[],labs=[];
  cut.layers.forEach((L,i)=>{
    const b=document.createElement('div');b.className='fxband';
    b.style.left=dx+'px';b.style.top=(dy+L.y)+'px';b.style.width=dw+'px';b.style.height=L.h+'px';
    b.style.background=L.colour;b.style.transform='scaleX(0)';
    if(i) b.style.boxShadow='inset 0 1px 0 rgba(255,255,255,.18)';
    el.appendChild(b);bands.push(b);
    const l=document.createElement('div');l.className='fxlab';l.textContent=L.label;
    l.style.top=(dy+L.ly)+'px';l.style.fontSize=cut.label_px+'px';l.style.color=L.ink;l.style.opacity='0';
    if(cut.side==='right'){l.style.left=(dx+dw+cut.gap)+'px';l.style.width=(cut.w-(dx+dw)-cut.gap)+'px';l.style.textAlign='left';}
    else{l.style.left='0px';l.style.width=(dx-cut.gap)+'px';l.style.textAlign='right';}
    l.style.transform='translateY(-50%)';
    const ln=document.createElement('div');
    ln.style.cssText='position:absolute;height:'+cut.line_px+'px;background:currentColor;opacity:.0;'+
      'top:'+(dy+L.ly-cut.line_px/2)+'px;'+(cut.side==='right'?'left:'+(dx+dw)+'px;width:'+cut.gap+'px;':
      'left:'+(dx-cut.gap)+'px;width:'+cut.gap+'px;');
    ln.style.color=L.ink;
    el.appendChild(ln);el.appendChild(l);labs.push([l,ln]);
  });
  layer.appendChild(el);
  updaters.push(f=>{
    if(f<cut.at_frame){el.style.visibility='hidden';return;}
    const rel=f-cut.at_frame,rf=Math.max(1,cut.reveal_f);
    el.style.visibility='visible';el.style.opacity='1';
    bands.forEach((b,i)=>{
      const p=easeOut((rel-i*rf*0.6)/rf);
      b.style.transform='scaleX('+p.toFixed(4)+')';
      const [l,ln]=labs[i];l.style.opacity=String(p);ln.style.opacity=String(0.65*p);
    });
  });
}

// ---------------------------------------------------------------- P16 photo insets
const insets=S.insets||[];
if(insets.length){
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('width',W);svg.setAttribute('height',H);
  svg.style.cssText='position:absolute;left:0;top:0;overflow:visible;pointer-events:none';
  layer.appendChild(svg);
  const leaderPx=()=>Math.max(1,Math.round(cut?cut.line_px:2));
  const els=insets.map(ins=>{
    const g=document.createElementNS('http://www.w3.org/2000/svg','g');
    const under=document.createElementNS('http://www.w3.org/2000/svg','line');
    const line=document.createElementNS('http://www.w3.org/2000/svg','line');
    [under,line].forEach((ln,k)=>{
      ln.setAttribute('x1',ins.lx);ln.setAttribute('y1',ins.ly);ln.setAttribute('x2',ins.ax);ln.setAttribute('y2',ins.ay);
      ln.setAttribute('stroke-linecap','round');
      ln.setAttribute('stroke',k?'#ffffff':'rgba(0,0,0,.35)');ln.setAttribute('stroke-width',k?leaderPx():1);
      g.appendChild(ln);});
    const dot=document.createElementNS('http://www.w3.org/2000/svg','circle');
    dot.setAttribute('cx',ins.ax);dot.setAttribute('cy',ins.ay);dot.setAttribute('r',7);
    dot.setAttribute('fill','#ffffff');dot.setAttribute('stroke','rgba(0,0,0,.35)');dot.setAttribute('stroke-width',2);
    g.appendChild(dot);svg.appendChild(g);g.style.opacity='0';
    const el=document.createElement('div');el.className='fxinset';el.setAttribute('data-qa',ins.id);
    el.style.width=ins.w+'px';el.style.height=ins.h+'px';el.style.padding=ins.border_px+'px';
    const im=document.createElement('img');im.src=ins.img;el.appendChild(im);layer.appendChild(el);
    return {ins:ins,el:el,g:g,lines:[under,line],dot:dot};
  });
  updaters.push(f=>{
    els.forEach(o=>{
      let ins=o.ins;
      if(!(f>=ins.at_frame&&f<ins.out_frame)){o.el.style.visibility='hidden';o.g.style.opacity='0';return;}
      if(ins.pin!==undefined){const a=pinAt(ins.pin,f);
        if(!a){o.el.style.visibility='hidden';o.g.style.opacity='0';return;}
        const le=document.querySelector('[data-qa="pin:'+ins.pin+'"]'), lr=le&&le.offsetWidth?le.getBoundingClientRect():null;
        ins={...ins,...placeInset(ins,a,lr)};
        o.lines.forEach(ln=>{ln.setAttribute('x1',ins.lx);ln.setAttribute('y1',ins.ly);ln.setAttribute('x2',ins.ax);ln.setAttribute('y2',ins.ay);});
        o.dot.setAttribute('cx',ins.ax);o.dot.setAttribute('cy',ins.ay);}
      const op=clamp(Math.min((f-ins.at_frame)/ins.fade_f,(ins.out_frame-f)/ins.fade_f),0,1);
      const sc=0.96+0.04*easeOut((f-ins.at_frame)/ins.fade_f);
      o.el.style.opacity=String(op);o.el.style.visibility=op>0?'visible':'hidden';
      o.el.style.transform='translate('+(ins.x-ins.w/2)+'px,'+(ins.y-ins.h/2)+'px) scale('+sc.toFixed(4)+')';
      o.g.style.opacity=String(op);
    });
  });
}

// ---------------------------------------------------------------- P19 particles
const parts=S.particles||[];
if(parts.length){
  const canvas=document.createElement('canvas');canvas.width=W;canvas.height=H;
  canvas.style.cssText='position:absolute;left:0;top:0;width:'+W+'px;height:'+H+'px';
  layer.insertBefore(canvas,layer.firstChild);
  const ctx=canvas.getContext('2d');
  const arcs=(x,y,r,a,c)=>{ctx.globalAlpha=a;ctx.fillStyle=c;ctx.beginPath();ctx.arc(x,y,Math.max(0.4,r),0,2*Math.PI);ctx.fill();};
  updaters.push(f=>{
    ctx.clearRect(0,0,W,H);
    parts.forEach(e=>{
      if(!(f>=e.f_in&&f<e.f_out))return;
      const dur=(e.f_out-e.f_in)/FPS,t=(f-e.f_in)/FPS,rnd=lcg(e.seed),dt=0.045;
      ctx.globalCompositeOperation='lighter';
      if(e.kind==='fireworks'){
        const g=e.gravity*H*e.scale;
        for(let i=0;i<e.count;i++){
          const ang=rnd()*2*Math.PI,sp=H*(0.16+0.34*rnd())*e.scale;
          const col=Math.floor(rnd()*e.colours.length),size=H*(0.0025+0.004*rnd())*e.scale,life=0.55+0.45*rnd();
          const x=e.x+Math.cos(ang)*sp*t,y=e.y+Math.sin(ang)*sp*t+0.5*g*t*t;
          const a=Math.max(0,1-t/dur)*life,r=size*Math.max(0,1-0.6*t/dur);
          if(t>dt){
            const x0=e.x+Math.cos(ang)*sp*(t-dt),y0=e.y+Math.sin(ang)*sp*(t-dt)+0.5*g*(t-dt)*(t-dt);
            ctx.globalAlpha=a*0.35;ctx.strokeStyle=e.colours[col];ctx.lineWidth=Math.max(1,r*0.9);
            ctx.beginPath();ctx.moveTo(x0,y0);ctx.lineTo(x,y);ctx.stroke();
          }
          arcs(x,y,r,a,e.colours[col]);
        }
      }else{
        for(let i=0;i<e.count;i++){
          const base=(rnd()-0.5)*0.16*H*e.scale,rise=H*(0.16+0.28*rnd())*e.scale;
          const sway=H*(0.01+0.035*rnd())*e.scale,freq=1.5+3.5*rnd(),phase=rnd()*2*Math.PI;
          const period=0.7+1.1*rnd(),off=rnd(),u=(t+off*period)%period,age=u/period;
          const x=e.x+base+Math.sin(age*2*Math.PI*freq+phase)*sway*(0.3+0.7*age);
          const y=e.y-rise*u,r=H*0.012*e.scale*(1-0.65*age);
          const a=Math.pow(1-age,0.7)*Math.min(1,t*3);
          arcs(x,y,r,a,e.colours[Math.min(e.colours.length-1,Math.floor(age*e.colours.length))]);
        }
      }
      ctx.globalAlpha=1;ctx.globalCompositeOperation='source-over';
    });
  });
}

// ---------------------------------------------------------------- driver
const D=S.n_frames/FPS,drv={t:0};
const update=t=>updaters.forEach(fn=>fn(t*FPS));
tl.fromTo(drv,{t:0},{t:D,duration:D,ease:'none',immediateRender:true,onUpdate:()=>update(drv.t)},0);
update(0);
window.__fx2={update:update};
})();
