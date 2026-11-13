// print block (profile [print], video_engine/printstyle.py): type roles, map grade, regions as printed ink, date
// headings, hand notes and the emboss of place names. Runs after the layout block; sets fonts, blend modes and the
// grade, and adds the heading and note elements (positions and times from S.layout).
(function(){
const S=window.SCENE, P=S.print, LY=S.layout, LG=S.legibility;
if(!P||!LY)return;
const MO=window.__motion;
const tl=window.__timelines.main, q=s=>document.querySelector(s), FPS=S.fps, C=S.camera, paper=P.surface==='paper';
const NS='http://www.w3.org/2000/svg', A=window.__adapt;
const rgba=(hex,a)=>'rgba('+[1,3,5].map(k=>parseInt(hex.slice(k,k+2),16)).join(',')+','+a+')';
const FAM={inter:'EngInter,sans-serif',serif:"'EngSerif',serif",garamond:"'EngGaramond',serif",
  garamond_italic:"'EngGaramondI',serif",cinzel:"'EngCinzel',serif",hand:"'EngHand',cursive"};
const font=(el,role,w,trk,caps)=>{el.style.fontFamily=FAM[role];el.style.fontWeight=w;if(role==='garamond_italic')el.style.fontStyle='normal';
  if(trk!==null&&trk!==undefined)el.style.letterSpacing=trk+'em';if(caps)el.style.textTransform='uppercase';};
const legs=window.__legs(C,FPS);
const fades=(el,spans)=>{gsap.set(el,{opacity:0});(spans||[]).forEach((s,j)=>{
  tl.fromTo(el,{opacity:0},{opacity:1,duration:Math.max(s[1],1e-3),ease:MO.entrance,immediateRender:j===0},s[0]);
  if(s[2]!==null)tl.fromTo(el,{opacity:1},{opacity:0,duration:Math.max(s[3],1e-3),ease:'power1.in',immediateRender:false},s[2]);});};
const base=q('#base'), map=q('#map'), shade=q('#shade'), cam=q('#cam'), ui=q('#ui');
const BLEND=[];   // [el, text el]: multiply on paper, normal while the knock-back vignette is up
// grade (paper): static saturation and contrast, the paper texture and grain multiplied over the map, a light vignette
if(paper){
  tl.getTweensOf(map).forEach(tw=>{if('filter' in (tw.vars||{}))tw.kill();});
  map.style.filter='saturate('+P.grade_saturate+') contrast('+P.grade_contrast+')';
  if(P.paper_texture&&P.paper_alpha>0){const t=document.createElement('div');t.setAttribute('data-grade','paper');
    t.style.cssText='position:absolute;left:0;top:0;width:'+S.map_size[0]+'px;height:'+S.map_size[1]+'px;background:url("assets/'+
      P.paper_texture+'") repeat;background-size:512px 512px;mix-blend-mode:multiply;opacity:'+P.paper_alpha+';pointer-events:none';
    map.after(t);}
  if(P.grain&&P.grain_alpha>0){const g=document.createElement('div');g.setAttribute('data-grade','grain');
    g.style.cssText='position:absolute;inset:0;background:url("assets/'+P.grain+'") repeat;mix-blend-mode:multiply;opacity:'+P.grain_alpha+';pointer-events:none';
    (q('#tilt')||cam).after(g);}
  shade.style.background='radial-gradient(ellipse at 50% 50%,'+rgba(P.ink,0)+' 55%,'+rgba(P.ink,P.vignette_alpha)+' 100%)';
}
const R=P.roles;
// pin labels: print role (the leader follows the ink)
S.pins.forEach((p,i)=>{const lab=q('[data-qa="pin:'+i+'"]');if(lab)font(lab,...R.label);});
// place names and the scene title: inside their region, entry tracking tightening, emboss under the ink on paper
const ghosts=[];
(LY.areas||[]).forEach(a=>{const lab=q('[data-qa="area:'+a.key+'"]');if(!lab)return;
  const k=a.title?'title':'area';font(lab,a.role||R[k][0],a.weight||R[k][1],a.tracking,true);
  const lw=lab.parentElement, pb=lw.parentElement, el=pb.parentElement;
  if(a.tracking_in!==undefined)(LY.els['area:'+a.key]||[]).forEach(s=>tl.fromTo(lab,{letterSpacing:a.tracking_in+'em'},
    {letterSpacing:a.tracking+'em',duration:Math.max(s[1],1e-3),ease:MO.entrance,immediateRender:false},s[0]));
  gsap.set(lab,{letterSpacing:(a.tracking_in!==undefined?a.tracking_in:a.tracking)+'em'});
  if(paper){BLEND.push([pb,lab]);
    if(P.emboss_alpha>0){const g=el.cloneNode(true);g.querySelectorAll('[data-qa],[data-ink],[data-line]').forEach(n=>{
        n.removeAttribute('data-qa');n.removeAttribute('data-ink');n.removeAttribute('data-line');n.classList.remove('adapt');});
      g.setAttribute('data-ink-ghost','emboss');el.before(g);
      ghosts.push({src:[pb,lw,lab],dst:[g.querySelector('.pb'),g.querySelector('.lw'),g.querySelector('.beside')]});}}
});
// year and time: print roles, centred by the layout
(S.figures||[]).forEach((F,i)=>{const el=q('[data-qa="figure:'+i+'"]'), Pf=LY.figures[String(i)];if(!el||!Pf)return;
  const time=Pf.tracking!==undefined;
  font(el,(time?R.time:R.year)[0],Pf.weight,time?Pf.tracking:0,time);
  if(paper)BLEND.push([el,el]);else if(!time&&A)A.shadow(el,[4,12,0.35]);});
// date headings: caps between two rules drawn from the centre outwards, a diamond at each inner end
(LY.headings||[]).forEach(h=>{
  const el=document.createElement('div');el.className='heading';el.setAttribute('data-qa','heading:'+h.key);el.textContent=h.text;
  el.style.cssText='position:absolute;white-space:nowrap;line-height:1;left:'+h.x+'px;top:'+h.y+'px;font-size:'+h.px+'px';
  font(el,...R.heading);ui.appendChild(el);
  if(A){A.el(el,'heading:'+h.key,LG.halo_width_em);if(!paper)A.shadow(el,[2,6,P.heading_shadow_alpha]);}
  const sp=LY.els['heading:'+h.key]||[], d=P.diamond_px, g=P.rule_gap;
  fades(el,sp);if(paper)BLEND.push([el,el]);
  if(h.ornaments===false||!(h.rules&&h.rules.length))return;
  h.rules.forEach(([x0,x1],j)=>{const ln=document.createElement('div');
    ln.style.cssText='position:absolute;left:'+x0+'px;top:'+(h.rule_y-P.rule_px/2)+'px;width:'+(x1-x0)+'px;height:'+P.rule_px+
      'px;transform-origin:'+(j===0?'100%':'0%')+' 50%';
    const dm=document.createElement('div'), cx=j===0?x1+g+d/2:x0-g-d/2;
    dm.style.cssText='position:absolute;left:'+(cx-d/2)+'px;top:'+(h.rule_y-d/2)+'px;width:'+d+'px;height:'+d+'px;transform:rotate(45deg)';
    [ln,dm].forEach((e,m)=>{ui.appendChild(e);e.setAttribute('data-line','rule:'+h.key+':'+j+':'+m);if(A)A.line(el,e);
      if(paper)BLEND.push([e,el]);fades(e,sp);});
    sp.forEach(s=>tl.fromTo(ln,{scaleX:0},{scaleX:1,duration:P.rule_draw_s,ease:MO.entrance,immediateRender:false},s[0]));
    gsap.set(ln,{scaleX:sp.length?0:1});
  });
});
// hand notes: written on left to right, then the arrow draws to its target; fixed on-screen size under the camera
const arrows=[];
(LY.notes||[]).forEach(n=>{
  const el=document.createElement('div');el.className='pin';el.style.left=n.anchor[0]+'px';el.style.top=n.anchor[1]+'px';el.style.visibility='visible';
  el.innerHTML='<div class="pb"><div class="lw" style="position:absolute;left:0;top:0"><div class="beside" data-qa="note:'+n.key+'"></div></div></div>';
  const lab=el.querySelector('.beside'), lw=el.querySelector('.lw'), pb=el.querySelector('.pb');
  lab.textContent=n.text;lab.style.cssText+=';left:0;top:0;font-size:'+n.px+'px;text-shadow:none';font(lab,...R.note);
  if(A)A.el(lab,'note:'+n.key,LG.halo_width_em);
  gsap.set(lab,{xPercent:-50,yPercent:-50,rotation:n.angle});cam.appendChild(el);
  legs.forEach(g=>tl.fromTo(pb,{scale:1/g.from.s},{scale:1/g.L.to.s,duration:g.d,ease:g.inv,immediateRender:g.k===0},g.t0));
  const sp=LY.els['note:'+n.key]||[];fades(lw,sp);if(paper)BLEND.push([pb,lab]);
  gsap.set(lab,{clipPath:'inset(-20% 100% -20% 0%)'});
  sp.forEach(s=>tl.fromTo(lab,{clipPath:'inset(-20% 100% -20% 0%)'},{clipPath:'inset(-20% 0% -20% 0%)',duration:P.note_write_s,ease:'none',immediateRender:false},s[0]));
  if(n.arrow){const [x1,y1,x2,y2]=n.arrow, sv=document.createElementNS(NS,'svg');
    sv.setAttribute('width','1');sv.setAttribute('height','1');sv.style.cssText='position:absolute;left:0;top:0;overflow:visible';
    const mx=(x1+x2)/2, my=(y1+y2)/2, L=Math.hypot(x2-x1,y2-y1), cx=mx-(y2-y1)*0.15, cy=my+(x2-x1)*0.15;
    const ang=Math.atan2(y2-cy,x2-cx), hl=Math.min(22,0.3*L), w=0.45;
    const path=document.createElementNS(NS,'path'), head=document.createElementNS(NS,'path');
    path.setAttribute('d','M'+x1+' '+y1+' Q'+cx+' '+cy+' '+x2+' '+y2);
    head.setAttribute('d','M'+(x2-hl*Math.cos(ang-w))+' '+(y2-hl*Math.sin(ang-w))+' L'+x2+' '+y2+' L'+(x2-hl*Math.cos(ang+w))+' '+(y2-hl*Math.sin(ang+w)));
    [path,head].forEach(e=>{e.setAttribute('fill','none');e.setAttribute('stroke-width','3.5');e.setAttribute('stroke-linecap','round');
      e.setAttribute('stroke-linejoin','round');sv.appendChild(e);});
    lw.appendChild(sv);sv.setAttribute('data-line','arrow:note:'+n.key);arrows.push([sv,lab]);
    const pl=path.getTotalLength(), hlen=head.getTotalLength();
    path.setAttribute('stroke-dasharray',pl+' '+pl);head.setAttribute('stroke-dasharray',hlen+' '+hlen);
    gsap.set(path,{attr:{'stroke-dashoffset':pl}});gsap.set(head,{attr:{'stroke-dashoffset':hlen}});
    sp.forEach(s=>{const a=s[0]+0.6*P.note_write_s;
      tl.fromTo(path,{attr:{'stroke-dashoffset':pl}},{attr:{'stroke-dashoffset':0},duration:0.3,ease:'power1.inOut',immediateRender:false},a);
      tl.fromTo(head,{attr:{'stroke-dashoffset':hlen}},{attr:{'stroke-dashoffset':0},duration:0.12,ease:'none',immediateRender:false},a+0.3);});}
});
// per frame (after the ink driver): blend modes from the ink state, emboss ghosts copied from their source, note
// arrows in the note's ink
const sync=()=>{
  BLEND.forEach(([e,t])=>{let va=0;try{va=+(JSON.parse(t.getAttribute('data-ink-state')||'{}').va||0);}catch(_){}
    e.style.mixBlendMode=va>0?'normal':'multiply';});
  ghosts.forEach(G=>{G.src.forEach((s,j)=>{G.dst[j].style.cssText=s.style.cssText;});
    const [gp,gw,gl]=G.dst;gw.style.opacity=(+getComputedStyle(G.src[1]).opacity)*P.emboss_alpha;gw.style.top=P.emboss_dy+'px';
    gl.style.color=P.emboss_colour;gl.style.textShadow='none';gl.style.setProperty('--vig','none');gl.style.setProperty('--halo-c','transparent');
    gp.style.mixBlendMode='normal';});
  arrows.forEach(([sv,lab])=>{const c=getComputedStyle(lab).color;sv.querySelectorAll('path').forEach(e=>e.setAttribute('stroke',c));
    const m=c.match(/\d+/g);if(m)sv.setAttribute('data-line-ink',JSON.stringify(['#'+m.slice(0,3).map(v=>(+v).toString(16).padStart(2,'0')).join('')]));});
};
const D=S.n_frames/FPS, drv={t:0};
tl.fromTo(drv,{t:0},{t:D,duration:D,ease:'none',immediateRender:true,onUpdate:sync},0);
if(A)A.up();sync();document.fonts.ready.then(()=>{if(A)A.up();sync();});
})();
