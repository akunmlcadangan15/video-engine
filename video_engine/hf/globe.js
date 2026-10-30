// Blue Marble globe (gsap_scene_config.globe): a three.js unit sphere textured with the whole-world map (#map, hidden)
// and the detail patches (coarse to fine), drawn into a canvas under the ui every time the timeline updates. The camera math is
// video_engine/globe.py `camera_at` / `pose` (same formulas); pins (.pin in #cam, which stays at identity) are placed
// at their three.js screen projection and hidden while their point faces away; region shapes ([data-geo] = region
// index, vertices `ll`) and region labels ([data-ll]) likewise, a region hidden while any vertex faces away; the
// map's filter (legibility basemap saturation) is copied onto the canvas. Loaded as a module after the template
// scripts (hyperframes.build_dir).
import * as THREE from './assets/three/three.module.js';
const S=window.SCENE, G=S.globe, W=S.width, H=S.height, FPS=S.fps, tl=window.__timelines.main;
const map=document.getElementById('map');
map.style.visibility='hidden';
const canvas=document.createElement('canvas');canvas.width=W;canvas.height=H;canvas.setAttribute('data-globe','');
canvas.style.cssText='position:absolute;left:0;top:0;width:'+W+'px;height:'+H+'px';
document.getElementById('root').prepend(canvas);
const renderer=new THREE.WebGLRenderer({canvas,antialias:true,preserveDrawingBuffer:true,alpha:true});
renderer.setPixelRatio(1);renderer.setSize(W,H,false);renderer.setClearColor(0x000000,0);
const scene=new THREE.Scene(), cam=new THREE.PerspectiveCamera(G.fov,W/H,1e-4,50);
const tex=img=>{const t=new THREE.Texture(img);t.colorSpace=THREE.SRGBColorSpace;t.anisotropy=8;
  t.minFilter=THREE.LinearMipmapLinearFilter;const up=()=>{t.needsUpdate=true;draw(drv.t);};
  if(img.complete&&img.naturalWidth)t.needsUpdate=true;else img.addEventListener('load',up);return t;};
scene.add(new THREE.Mesh(new THREE.SphereGeometry(1,192,96),new THREE.MeshBasicMaterial({map:tex(map)})));
(G.patches||[]).forEach((P,i)=>{const im=document.createElement('img');im.src=P.img;im.style.display='none';
  document.body.appendChild(im);const R=Math.PI/180;
  const m=new THREE.Mesh(new THREE.SphereGeometry(1,128,128,(P.west+180)*R,(P.east-P.west)*R,(90-P.north)*R,(P.north-P.south)*R),
    new THREE.MeshBasicMaterial({map:tex(im),depthTest:false,depthWrite:false}));
  m.renderOrder=1+i;scene.add(m);});   // coarse to fine; a convex sphere's front faces never hide each other
const V=(lat,lng)=>{const a=lat*Math.PI/180,b=lng*Math.PI/180;return new THREE.Vector3(Math.cos(a)*Math.cos(b),Math.sin(a),-Math.cos(a)*Math.sin(b));};
const glide=t=>{t=Math.min(Math.max(t,0),1);return 0.35*t+0.65*(1-Math.cos(Math.PI*t))/2;};
const slerp=(a,b,t)=>{const c=Math.min(1,Math.max(-1,a.dot(b))),w=Math.acos(c);if(w<1e-9)return a.clone();const s=Math.sin(w);
  return a.clone().multiplyScalar(Math.sin((1-t)*w)/s).add(b.clone().multiplyScalar(Math.sin(t*w)/s));};
const view=f=>{const K=G.keys;
  for(let i=0;i+1<K.length;i++){const a=K[i],b=K[i+1];if(f<b.f){if(f<=a.f)break;const e=glide((f-a.f)/(b.f-a.f));
    const v=slerp(V(a.lat,a.lng),V(b.lat,b.lng),e);
    return {lat:Math.asin(Math.min(1,Math.max(-1,v.y)))*180/Math.PI,lng:Math.atan2(-v.z,v.x)*180/Math.PI,
      alt:Math.exp(Math.log(a.alt)+(Math.log(b.alt)-Math.log(a.alt))*e),tilt:a.tilt+(b.tilt-a.tilt)*e};}}
  const k=f<=K[0].f?K[0]:K[K.length-1];
  return {lat:k.lat,lng:k.lng,tilt:k.tilt,alt:Math.max(G.alt_min,k.alt*Math.exp(-G.drift*Math.max(0,f-k.f)/FPS))};};
const pins=[...document.querySelectorAll('#cam .pin')], geo=[...document.querySelectorAll('[data-geo]')],
  lls=[...document.querySelectorAll('[data-ll]')];
const scr=(lat,lng)=>{const X=V(lat,lng), q=X.clone().project(cam);
  return {front:X.dot(X.clone().sub(cam.position))<0&&q.z<1,x:(q.x+1)/2*W,y:(1-q.y)/2*H};};
const draw=t=>{const v=view(t*FPS), P=V(v.lat,v.lng), east=new THREE.Vector3(0,1,0).cross(P).normalize(),
    north=P.clone().cross(east), a=v.tilt*Math.PI/180;
  cam.position.copy(P.clone().add(P.clone().multiplyScalar(Math.cos(a)).add(north.clone().multiplyScalar(-Math.sin(a))).multiplyScalar(v.alt)));
  cam.up.copy(north);cam.lookAt(P);cam.near=Math.max(1e-5,v.alt*0.02);cam.updateProjectionMatrix();cam.updateMatrixWorld();
  renderer.render(scene,cam);
  S.pins.forEach((p,i)=>{const el=pins[i];if(!el)return;const X=V(p.lat,p.lng), d=X.clone().sub(cam.position);
    const front=X.dot(d)<0, q=X.clone().project(cam);
    el.style.display=front&&q.z<1?'':'none';
    el.style.left=((q.x+1)/2*W)+'px';el.style.top=((1-q.y)/2*H)+'px';});
  const rs=(S.regions||[]).map(r=>{if(!r.ll)return null;const q=r.ll.map(a=>scr(a[0],a[1]));
    return {front:q.every(o=>o.front),pts:q.map(o=>o.x+','+o.y).join(' ')};});
  geo.forEach(el=>{const r=rs[+el.getAttribute('data-geo')];el.setAttribute('points',r.pts);el.style.display=r.front?'':'none';});
  lls.forEach(el=>{const a=el.getAttribute('data-ll').split(',').map(Number), o=scr(a[0],a[1]);
    el.style.display=o.front?'':'none';el.style.left=o.x+'px';el.style.top=o.y+'px';});
  canvas.style.filter=map.style.filter;};
const D=S.n_frames/FPS, drv={t:0};
tl.fromTo(drv,{t:0},{t:D,duration:D,ease:'none',immediateRender:true,onUpdate:()=>draw(drv.t)},0);
draw(0);
window.__globe={view,draw,camera:cam};
