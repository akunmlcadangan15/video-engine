// Full 3D camera (gsap_scene_config.camera3d; video_engine/camera_full3d.py): the map (#map, hidden) is a textured
// plane z = 0 in a three.js scene (map point (u, v) at world (u*mw, -v*mh, 0), north +y) and a PerspectiveCamera flies
// over it. The camera state of every frame (position, quaternion, vertical fov) comes precomputed from Python
// (S.cam3d.frames), so the browser draws exactly what the gates checked; nothing here samples splines. Pins (.pin in
// #cam, which stays at identity) are placed at their screen projection and hidden behind the camera. Loaded as a
// module after the template scripts (hyperframes.build_dir).
import * as THREE from './assets/three/three.module.js';
const S=window.SCENE, C=S.cam3d, W=S.width, H=S.height, FPS=S.fps, tl=window.__timelines.main;
const [MW,MH]=S.map_size, N=C.frames.length;
const map=document.getElementById('map');
map.style.visibility='hidden';
const canvas=document.createElement('canvas');canvas.width=W;canvas.height=H;canvas.setAttribute('data-scene3d','');
canvas.style.cssText='position:absolute;left:0;top:0;width:'+W+'px;height:'+H+'px';
{const root=document.getElementById('root'), bd=root.querySelector(':scope>[data-fx-backdrop]');bd?bd.after(canvas):root.prepend(canvas);}
const renderer=new THREE.WebGLRenderer({canvas,antialias:true,preserveDrawingBuffer:true,alpha:true});
renderer.setPixelRatio(1);renderer.setSize(W,H,false);renderer.setClearColor(0x000000,0);
const scene=new THREE.Scene(), cam=new THREE.PerspectiveCamera(50,W/H,1,1e7);
const tex=new THREE.Texture(map);tex.colorSpace=THREE.SRGBColorSpace;tex.anisotropy=8;
tex.minFilter=THREE.LinearMipmapLinearFilter;
if(map.complete&&map.naturalWidth)tex.needsUpdate=true;else map.addEventListener('load',()=>{tex.needsUpdate=true;draw(drv.t);});
const plane=new THREE.Mesh(new THREE.PlaneGeometry(MW,MH),new THREE.MeshBasicMaterial({map:tex}));
plane.position.set(MW/2,-MH/2,0);scene.add(plane);
const pins=[...document.querySelectorAll('#cam .pin')];
const pinW=S.pins.map(p=>new THREE.Vector3(p.mx,-p.my,0));
const state=f=>C.frames[Math.max(0,Math.min(N-1,Math.round(f)))];
const draw=t=>{const v=state(t*FPS);
  cam.position.set(v[0],v[1],v[2]);cam.quaternion.set(v[3],v[4],v[5],v[6]);cam.fov=v[7];
  cam.near=Math.max(1e-3,v[2]*C.near_frac);cam.updateProjectionMatrix();cam.updateMatrixWorld();
  renderer.render(scene,cam);
  pins.forEach((el,i)=>{const X=pinW[i];if(!X)return;
    const d=X.clone().applyMatrix4(cam.matrixWorldInverse), q=X.clone().project(cam);
    el.style.display=d.z<0&&q.z<1?'':'none';
    el.style.left=((q.x+1)/2*W)+'px';el.style.top=((1-q.y)/2*H)+'px';});
  canvas.style.filter=map.style.filter;
  if(window.__fxPlace)window.__fxPlace();};
const D=S.n_frames/FPS, drv={t:0};
tl.fromTo(drv,{t:0},{t:D,duration:D,ease:'none',immediateRender:true,onUpdate:()=>draw(drv.t)},0);
draw(0);
window.__scene3d={draw,camera:cam,state};
