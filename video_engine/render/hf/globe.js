// Blue Marble globe (gsap_scene_config.globe): a three.js unit sphere textured with the whole-world map (#map, hidden)
// and the detail patches (coarse to fine), drawn into a canvas under the ui every time the timeline updates. Camera views
// come from video_engine/globe.py `camera_at` (scene globe.views); pins (.pin in #cam, which stays at identity) are placed
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
// behind everything else, but in front of a P12/P14 backdrop canvas (grid/stars sit behind the globe, not over it)
{const root=document.getElementById('root'), bd=root.querySelector(':scope>[data-fx-backdrop]');bd?bd.after(canvas):root.prepend(canvas);}
const renderer=new THREE.WebGLRenderer({canvas,antialias:true,preserveDrawingBuffer:true,alpha:true});
renderer.setPixelRatio(1);renderer.setSize(W,H,false);renderer.setClearColor(0x000000,0);
const scene=new THREE.Scene(), cam=new THREE.PerspectiveCamera(G.fov,W/H,1e-4,50);
const tex=img=>{const t=new THREE.Texture(img);t.colorSpace=THREE.SRGBColorSpace;t.anisotropy=8;
  t.minFilter=THREE.LinearMipmapLinearFilter;const up=()=>{t.needsUpdate=true;draw(drv.t);};
  if(img.complete&&img.naturalWidth)t.needsUpdate=true;else img.addEventListener('load',up);return t;};
const base=new THREE.Mesh(new THREE.SphereGeometry(1,192,96),new THREE.MeshBasicMaterial({map:tex(map)}));
scene.add(base);
// P14 atmospheric rim: a slightly larger back-side shell, additive; the fresnel term keeps the glow on the silhouette
// and the depth test hides it behind the globe, so the halo follows the sphere's screen radius on every frame.
if(G.halo){const hm=new THREE.ShaderMaterial({
  uniforms:{uColour:{value:new THREE.Color(G.halo.colour)},uAlpha:{value:G.halo.alpha}},
  vertexShader:'varying vec3 vN;varying vec3 vV;void main(){vN=normalize(normalMatrix*normal);vec4 mv=modelViewMatrix*'+
    'vec4(position,1.0);vV=normalize(-mv.xyz);gl_Position=projectionMatrix*mv;}',
  fragmentShader:'uniform vec3 uColour;uniform float uAlpha;varying vec3 vN;varying vec3 vV;void main(){float rim='+
    'pow(1.0-abs(dot(normalize(vN),normalize(vV))),2.2);gl_FragColor=vec4(uColour,rim*uAlpha);}',
  transparent:true,blending:THREE.AdditiveBlending,depthWrite:false,side:THREE.BackSide});
  const shell=new THREE.Mesh(new THREE.SphereGeometry(1+G.halo.width_pct/100,96,64),hm);shell.renderOrder=-1;scene.add(shell);
  const hmk=document.createElement('div');hmk.setAttribute('data-qa','fx:halo');hmk.style.cssText='position:absolute;left:0;top:0;width:0;height:0';document.getElementById('root').appendChild(hmk);}
if(!G.geoid)(G.patches||[]).forEach((P,i)=>{const im=document.createElement('img');im.src=P.img;im.style.display='none';
  document.body.appendChild(im);const R=Math.PI/180;
  const m=new THREE.Mesh(new THREE.SphereGeometry(1,128,128,(P.west+180)*R,(P.east-P.west)*R,(90-P.north)*R,(P.north-P.south)*R),
    new THREE.MeshBasicMaterial({map:tex(im),depthTest:false,depthWrite:false}));
  m.renderOrder=1+i;scene.add(m);});   // coarse to fine; a convex sphere's front faces never hide each other
// P18 geoid: a displaced sphere coloured by the scene ramp. The displacement is a seeded sum of smooth waves (the same
// harmonic data the Python mirror uses), so the mesh is identical on every run. Fades in at geoid.at_frame over fade_f.
let geoid=null, gmat=null, gd=null;
if(G.geoid){gd=G.geoid;
  const gh=(x,y,z)=>{const L=Math.sqrt(x*x+y*y+z*z)||1,dx=x/L,dy=y/L,dz=z/L;let s=0;
    for(const h of gd.harmonics){const dot=Math.max(-1,Math.min(1,dx*h.v[0]+dy*h.v[1]+dz*h.v[2]));
      s+=h.a*Math.sin(h.n*Math.acos(dot)+h.p);}return Math.max(-1,Math.min(1,s));};
  const ramp=gd.colour_ramp.map(c=>new THREE.Color(c));
  const colAt=t=>{t=Math.max(0,Math.min(1,t));const u=t*(ramp.length-1),i=Math.min(ramp.length-2,Math.floor(u));
    return ramp[i].clone().lerp(ramp[i+1],u-i);};
  const geo=new THREE.SphereGeometry(1,192,96),pos=geo.attributes.position,cols=[],hs=[];
  for(let i=0;i<pos.count;i++)hs.push(gh(pos.getX(i),pos.getY(i),pos.getZ(i)));
  // the ramp spans the mesh's own height range: summed waves rarely reach +-1, and (h+1)/2 left every vertex on the
  // ramp's middle colours (a flat murky green). Displacement still uses the raw seeded height.
  const h0=Math.min(...hs), h1=Math.max(...hs), hr=h1-h0>1e-6?h1-h0:1;
  for(let i=0;i<pos.count;i++){const x=pos.getX(i),y=pos.getY(i),z=pos.getZ(i),L=Math.sqrt(x*x+y*y+z*z)||1;
    const h=hs[i],r=1.002*(1+gd.exaggeration*h);pos.setXYZ(i,x/L*r,y/L*r,z/L*r);
    const c=colAt((h-h0)/hr);cols.push(c.r,c.g,c.b);}
  geo.setAttribute('color',new THREE.Float32BufferAttribute(cols,3));geo.computeVertexNormals();
  gmat=new THREE.MeshLambertMaterial({vertexColors:true,transparent:true,opacity:gd.at_frame?0:1,depthWrite:false});
  geoid=new THREE.Mesh(geo,gmat);scene.add(geoid);
  scene.add(new THREE.AmbientLight(0xffffff,0.62));
  const dl=new THREE.DirectionalLight(0xffffff,0.85);dl.position.set(5,3,6);scene.add(dl);}
const V=(lat,lng)=>{const a=lat*Math.PI/180,b=lng*Math.PI/180;return new THREE.Vector3(Math.cos(a)*Math.cos(b),Math.sin(a),-Math.cos(a)*Math.sin(b));};
const view=f=>{f=Math.min(Math.max(f,0),G.views.length-1);const i=Math.floor(f),t=f-i,a=G.views[i],b=G.views[Math.min(i+1,G.views.length-1)];
  return {lat:a[0]+(b[0]-a[0])*t,lng:((a[1]+((b[1]-a[1]+540)%360-180)*t+180)%360+360)%360-180,
    alt:a[2]+(b[2]-a[2])*t,tilt:a[3]+(b[3]-a[3])*t};};
const pins=[...document.querySelectorAll('#cam .pin')], geo=[...document.querySelectorAll('[data-geo]')],
  lls=[...document.querySelectorAll('[data-ll]')];
const scr=(lat,lng)=>{const X=V(lat,lng), q=X.clone().project(cam);
  return {front:X.dot(X.clone().sub(cam.position))<0&&q.z<1,x:(q.x+1)/2*W,y:(1-q.y)/2*H};};
const draw=t=>{const v=view(t*FPS), P=V(v.lat,v.lng), rawE=new THREE.Vector3(0,1,0).cross(P), east=(rawE.lengthSq()>1e-12?rawE:new THREE.Vector3(1,0,0)).normalize(),
    north=P.clone().cross(east), a=v.tilt*Math.PI/180;
  cam.position.copy(P.clone().add(P.clone().multiplyScalar(Math.cos(a)).add(north.clone().multiplyScalar(-Math.sin(a))).multiplyScalar(v.alt)));
  cam.up.copy(north);cam.lookAt(P);cam.near=Math.max(1e-5,v.alt*0.02);cam.updateProjectionMatrix();cam.updateMatrixWorld();
  if(geoid){const f=t*FPS,op=gd.at_frame>0?Math.max(0,Math.min(1,(f-gd.at_frame)/Math.max(1,gd.fade_f))):1;
    gmat.opacity=op;geoid.visible=op>0.001;geoid.rotation.y=gd.rotate_deg_s*Math.PI/180*t;base.visible=op<0.999;}
  renderer.render(scene,cam);
  S.pins.forEach((p,i)=>{const el=pins[i];if(!el)return;const X=V(p.lat,p.lng), d=X.clone().sub(cam.position);
    const front=X.dot(d)<0, q=X.clone().project(cam);
    el.style.display=front&&q.z<1?'':'none';
    el.style.left=((q.x+1)/2*W)+'px';el.style.top=((1-q.y)/2*H)+'px';});
  const rs=(S.regions||[]).map(r=>{if(!r.ll)return null;const q=r.ll.map(a=>scr(a[0],a[1]));
    const xs=q.map(o=>o.x),ys=q.map(o=>o.y);
    return {front:q.every(o=>o.front),pts:q.map(o=>o.x+','+o.y).join(' '),
      x:Math.min(...xs),y:Math.min(...ys),w:Math.max(...xs)-Math.min(...xs),h:Math.max(...ys)-Math.min(...ys)};});
  geo.forEach(el=>{const r=rs[+el.getAttribute('data-geo')];el.setAttribute('points',r.pts);el.style.display=r.front?'':'none';});
  document.querySelectorAll('[data-flag-geo]').forEach(el=>{const r=rs[+el.getAttribute('data-flag-geo')];
    el.style.display=r.front?'':'none';
    const fr=(window.__flagRect||((x,y,width,height)=>({x,y,width,height})))(r.x,r.y,r.w,r.h,{fit:el.getAttribute('data-fit'),aspect:el.getAttribute('data-aspect')});
    for(const [k,v] of Object.entries(fr))el.setAttribute(k,v);});
  lls.forEach(el=>{const a=el.getAttribute('data-ll').split(',').map(Number), o=scr(a[0],a[1]);
    el.style.display=o.front?'':'none';el.style.left=o.x+'px';el.style.top=o.y+'px';});
  canvas.style.filter=map.style.filter;
  if(window.__fxPlace)window.__fxPlace();
  if(window.__fx2)window.__fx2.update(t);};   // fx2 anchors read the pins placed above
const D=S.n_frames/FPS, drv={t:0};
tl.fromTo(drv,{t:0},{t:D,duration:D,ease:'none',immediateRender:true,onUpdate:()=>draw(drv.t)},0);
draw(0);
window.__globe={view,draw,camera:cam};
