// props layer (gsap_scene_config.props and beats[].props): free-positioned images over the map. A turntable model
// prop (e.seq) is a clipping box over its sprite sheet, shifted to the cell of the frame (props.py `cell`).
// Loaded after the template scripts of a scene with props (hyperframes.build_dir). The per-frame math is
// video_engine/props.py `state` (same formula, same eases); the camera of each frame comes from the shared legs
// (window.__legs), the same transform the #cam tweens apply on that frame.
(function(){
const S=window.SCENE, P=S.props;
if(!P||!P.length)return;
const tl=window.__timelines.main, FPS=S.fps, W=S.width, H=S.height, C=S.camera, LG=window.__legs(C,FPS);
const css=document.createElement('style');
css.textContent='.props{position:absolute;left:0;top:0;z-index:0;width:'+W+'px;height:'+H+'px;overflow:hidden;pointer-events:none}'+
  '.prop{position:absolute;left:0;top:0;transform-origin:50% 50%;visibility:hidden;max-width:none}'+
  '.prop.seq{overflow:hidden}.prop.seq>img{position:absolute;left:0;top:0;max-width:none;display:block}';
document.head.appendChild(css);
const mapL=document.createElement('div'), scrL=document.createElement('div');
mapL.className=scrL.className='props';
mapL.id='props-map';scrL.id='props-screen';
document.getElementById('cam').after(mapL);          // over the map, under the shade and the ui
document.getElementById('ui').before(scrL);          // over the shade, under the ui texts
const EASE={glide:t=>{t=Math.min(Math.max(t,0),1);return (1-Math.cos(Math.PI*t))/2;}, drift:t=>t,
  whip:t=>t<0.5?8*t*t*t*t:1-Math.pow(-2*t+2,4)/2};
const cl=v=>Math.min(1,Math.max(0,v));
// camera {x,y,s} at time t (s): the leg tween value of that frame
const camAt=t=>{let prev=C.start;
  for(const g of LG){if(t<g.t0+g.d){if(t<=g.t0)return prev;const e=g.ease((t-g.t0)/g.d),a=g.from,b=g.L.to;
    return {x:a.x+(b.x-a.x)*e,y:a.y+(b.y-a.y)*e,s:a.s+(b.s-a.s)*e};}prev=g.L.to;}
  return prev;};
// path props: map point and direction (deg) u frames in, arc length share glide(u / f) (props.py `path_at`)
const pathAt=(pa,u)=>{const q=pa.pts, L=q.slice(1).map((b,j)=>Math.hypot(b[0]-q[j][0],b[1]-q[j][1]));
  let d=EASE.glide(cl(u/pa.f))*L.reduce((a,b)=>a+b,0);
  for(let j=0;j<L.length;j++){const a=q[j],b=q[j+1];
    if(L[j]>0&&d<=L[j])return [a[0]+(b[0]-a[0])*d/L[j],a[1]+(b[1]-a[1])*d/L[j],Math.atan2(b[1]-a[1],b[0]-a[0])*180/Math.PI];
    d-=L[j];}
  let j=L.length-1;while(j>0&&L[j]===0)j--;
  return [q[q.length-1][0],q[q.length-1][1],Math.atan2(q[j+1][1]-q[j][1],q[j+1][0]-q[j][0])*180/Math.PI];};
const sheets=[];
const els=P.map((e,i)=>{let im;
  if(e.seq){im=document.createElement('div');const sh=document.createElement('img');sh.src=e.img;
    const rows=Math.ceil(e.seq.n/e.seq.cols);sh.style.width=(e.seq.cols*100)+'%';sh.style.height=(rows*100)+'%';
    im.appendChild(sh);sheets[i]=sh;im.className='prop seq';}
  else{im=document.createElement('img');im.className='prop';im.src=e.img;}
  im.setAttribute('data-prop',e.id);im.style.zIndex=String(e.z);
  if(e.shadow){const s=e.shadow;im.style.filter='drop-shadow('+s.dx+'px '+s.dy+'px '+s.blur+'px rgba(0,0,0,'+s.alpha+'))';}
  (e.anchor==='map'?mapL:scrL).appendChild(im);return im;});
const up=t=>{const f=t*FPS, c=camAt(t);
  P.forEach((e,i)=>{const el=els[i];
    if(!(f>=e.f_in-1e-6&&f<e.f_out-1e-6)){el.style.visibility='hidden';return;}
    const u=f-e.f_in;let op=e.op,size=1,ox=0,oy=0,rot=e.rot;
    for(const k of ['enter','exit']){const m=e[k];if(!m)continue;
      const p=k==='enter'?cl(u/m.f):cl((f-e.f_out+m.f)/m.f), v=EASE[m.ease](p), a=k==='enter'?v:1-v;
      if(m.kind==='pop')size*=a;else op*=a;
      if(m.kind==='slide'){const q=k==='enter'?-(1-v):v;ox+=q*m.dx;oy+=q*m.dy;}}
    let x,y,w;
    if(e.anchor==='map'){const r=camAt(e.f_in/FPS), d=e.d, kk=Math.pow(c.s/r.s,1+d);
      const cx=(W/2-c.x)/c.s, cy=(H/2-c.y)/c.s, rx=(W/2-r.x)/r.s, ry=(H/2-r.y)/r.s, sg=r.s*kk;
      let mx=e.m[0],my=e.m[1];
      if(e.path){const pa=pathAt(e.path,u);mx=pa[0];my=pa[1];if(e.path.heading)rot+=pa[2];}
      x=W/2+sg*((mx-rx)-(1+d)*(cx-rx));y=H/2+sg*((my-ry)-(1+d)*(cy-ry));w=e.w*kk;}
    else{x=e.p[0];y=e.p[1];w=e.w;}
    if(e.idle){const sn=Math.sin(2*Math.PI*u/e.idle.period);if(e.idle.kind==='bob')oy+=e.idle.a*sn;else rot+=e.idle.a*sn;}
    w*=size;const h=w*e.size[1]/e.size[0];
    if(e.seq){const q=e.seq,k=((Math.floor(u*q.n/q.period+1e-4)%q.n)+q.n)%q.n,rows=Math.ceil(q.n/q.cols);
      sheets[i].style.transform='translate('+(-(k%q.cols)*100/q.cols)+'%,'+(-Math.floor(k/q.cols)*100/rows)+'%)';}
    el.style.width=w+'px';el.style.height=h+'px';el.style.opacity=String(op);
    el.style.visibility=w>0&&op>0?'visible':'hidden';
    el.style.transform='translate('+(x+ox-w/2)+'px,'+(y+oy-h/2)+'px) rotate('+rot+'deg)';});};
const D=S.n_frames/FPS, drv={t:0};
tl.fromTo(drv,{t:0},{t:D,duration:D,ease:'none',immediateRender:true,onUpdate:()=>up(drv.t)},0);
up(0);
window.__props={camAt:camAt,update:up};
})();
