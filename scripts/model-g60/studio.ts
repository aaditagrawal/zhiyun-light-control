import * as THREE from 'three';
import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { RectAreaLightUniformsLib } from 'three/addons/lights/RectAreaLightUniformsLib.js';
import { createG60Model, METRES_PER_UNIT } from './model';
import { createRubyRig, RUBY, type RubyOptions } from './ruby';

const width=960,height=720;
RectAreaLightUniformsLib.init();
const scene=new THREE.Scene();
const renderer=new THREE.WebGLRenderer({alpha:true,antialias:true,preserveDrawingBuffer:true});
renderer.setPixelRatio(2);
renderer.setSize(width,height);
renderer.setClearColor(0x000000,0);
renderer.outputColorSpace=THREE.SRGBColorSpace;
renderer.toneMapping=THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure=1.22;
renderer.shadowMap.enabled=true;
renderer.shadowMap.type=THREE.PCFSoftShadowMap;
document.querySelector('#stage')!.append(renderer.domElement);
const pmrem=new THREE.PMREMGenerator(renderer);
const environment=new RoomEnvironment();
scene.environment=pmrem.fromScene(environment,.04).texture;
scene.environmentIntensity=.55;
environment.dispose();

const model=createG60Model();
const stage=new THREE.Group();stage.add(model);stage.scale.setScalar(1/METRES_PER_UNIT);scene.add(stage);
stage.rotation.z=-.09;

const studioLights=new THREE.Group();studioLights.name='Studio lights';scene.add(studioLights);
function area(color: string,intensity: number,x: number,y: number,z: number,w: number,h: number) {
  const light=new THREE.RectAreaLight(color,intensity,w,h);light.position.set(x,y,z);light.lookAt(0,0,0);studioLights.add(light);
}
// Physically sized softboxes make the warm edge highlights and ruby rim.
area('#fff1d5',6,-3.6,5.2,5.5,4.5,4.5);
area('#f5e5cf',4,4.5,2,4,2,4);
area('#a50830',11,3.2,2.1,-3.2,3,4);
area('#d4173f',6,-3,.8,-1,1.2,3);
const key=new THREE.DirectionalLight('#ffe9cb',2.8);key.position.set(-3,6,5);key.castShadow=true;key.shadow.mapSize.set(2048,2048);key.shadow.camera.left=-3;key.shadow.camera.right=3;key.shadow.camera.top=3;key.shadow.camera.bottom=-3;key.shadow.bias=-.0003;studioLights.add(key);
const fill=new THREE.DirectionalLight('#e9d8cc',1.15);fill.position.set(4,2,3);studioLights.add(fill);
const rim=new THREE.DirectionalLight('#cf163c',1.8);rim.position.set(3,2,-4);studioLights.add(rim);

const studioCamera=new THREE.OrthographicCamera(-2.1*width/height,2.1*width/height,2.1,-2.1,.1,100);
let camera:THREE.Camera=studioCamera;
// `?preset=ruby` swaps in the optional ruby rig and perspective camera.
let ruby:ReturnType<typeof createRubyRig>|undefined;
function useRuby(options:RubyOptions|false=RUBY) {
  ruby?.dispose();ruby=undefined;camera=studioCamera;studioLights.visible=!options;
  if(options){ruby=createRubyRig(scene,renderer,stage,options);camera=ruby.camera;}
  view();
  return ruby?.options;
}
let currentView='front';
function view(side='front') {
  currentView=side;
  if(!ruby) {
    studioCamera.position.set(...(side==='rear'?[4.2,2.7,-6.3]:[4.25,2.85,6.6]) as [number,number,number]);
    studioCamera.lookAt(0,-.1,0);
  }
  renderer.render(scene,camera);
}
const preset=new URLSearchParams(location.search).get('preset');
if(preset==='ruby') useRuby(); else view();

async function save(name:string,body:BodyInit) {
  const response=await fetch(`/capture/${name}`,{method:'POST',body});
  if(!response.ok) throw new Error(`Cannot save ${name}: ${response.status}`);
  return response.json();
}
async function png(w:number,h:number) {
  // Render at 2x, then downsample to the exact native asset dimensions.
  renderer.setSize(w,h);renderer.render(scene,camera);
  const canvas=document.createElement('canvas');canvas.width=w;canvas.height=h;
  canvas.getContext('2d')!.drawImage(renderer.domElement,0,0,w,h);
  return await new Promise<Blob>(resolve=>canvas.toBlob(b=>resolve(b!),'image/png'));
}
function glowPng() {
  const canvas=document.createElement('canvas');canvas.width=canvas.height=256;
  const c=canvas.getContext('2d')!;
  const gradient=c.createRadialGradient(128,128,0,128,128,128);
  gradient.addColorStop(0,'rgba(200,24,67,.72)');
  gradient.addColorStop(.18,'rgba(181,17,56,.60)');
  gradient.addColorStop(.38,'rgba(147,10,43,.39)');
  gradient.addColorStop(.62,'rgba(106,6,32,.17)');
  gradient.addColorStop(.82,'rgba(83,4,25,.04)');
  gradient.addColorStop(1,'rgba(72,3,23,0)');
  c.fillStyle=gradient;c.fillRect(0,0,256,256);
  return new Promise<Blob>(resolve=>canvas.toBlob(b=>resolve(b!),'image/png'));
}
// The ruby preset writes only its own two images and metrics, never the
// default hero, compact, glow, rear or GLB outputs.
async function exportRuby(status:Element) {
  status.textContent='Rendering ruby hero…';
  view('front');
  await save('hero-ruby.png',await png(960,720));
  await save('compact-ruby.png',await png(480,360));
  renderer.setSize(width,height);renderer.render(scene,camera);
  const metrics={date:'2026-10-25',preset:'ruby',renderer:renderer.getContext().getParameter(renderer.getContext().VERSION),width,height,transparent:true,triangles:renderer.info.render.triangles,drawCalls:renderer.info.render.calls,source:['scripts/model-g60/model.ts','scripts/model-g60/ruby.ts'],options:ruby!.options};
  await save('ruby-render-metrics.json',JSON.stringify(metrics,null,2)+'\n');
  status.textContent='Ruby assets saved';
  (window as any).__g60Result=metrics;
  return metrics;
}
async function exportAssets() {
  const status=document.querySelector('#status')!;
  if(ruby) return exportRuby(status);
  status.textContent='Rendering transparent hero…';
  view('front');
  await save('hero.png',await png(960,720));
  await save('compact.png',await png(480,360));
  await save('glow.png',await glowPng());
  view('rear');await save('rear.png',await png(960,720));
  view('front');renderer.setSize(width,height);renderer.render(scene,camera);
  status.textContent='Exporting reusable GLB…';
  // Export the actual metre-scaled model, excluding render lights and stage.
  model.updateMatrixWorld(true);
  const result=await new GLTFExporter().parseAsync(model,{binary:true,onlyVisible:true,trs:false});
  if(!(result instanceof ArrayBuffer)) throw new Error('Expected binary GLB');
  await save('g60.glb',result);
  const loaded=await new GLTFLoader().parseAsync(result.slice(0),'');
  const bounds=new THREE.Box3().setFromObject(loaded.scene);
  const size=bounds.getSize(new THREE.Vector3());
  if(!Number.isFinite(size.length())||size.length()===0) throw new Error('Invalid GLB bounds');
  const metrics={date:'2026-10-25',renderer:renderer.getContext().getParameter(renderer.getContext().VERSION),width,height,transparent:true,glbBytes:result.byteLength,triangles:renderer.info.render.triangles,drawCalls:renderer.info.render.calls,textures:renderer.info.memory.textures,source:'scripts/model-g60/model.ts',metresPerConstructionUnit:METRES_PER_UNIT,roundTrip:{loaded:true,sizeMetres:size.toArray(),min:bounds.min.toArray(),max:bounds.max.toArray()}};
  await save('render-metrics.json',JSON.stringify(metrics,null,2)+'\n');
  status.textContent=`Assets saved · ${Math.round(result.byteLength/1024)} KiB GLB · ${metrics.triangles.toLocaleString()} triangles`;
  (window as any).__g60Result=metrics;
  return metrics;
}
document.querySelector('#front')!.addEventListener('click',()=>view('front'));
document.querySelector('#rear')!.addEventListener('click',()=>view('rear'));
document.querySelector('#export')!.addEventListener('click',()=>exportAssets().catch(e=>{document.querySelector('#status')!.textContent=e.message;console.error(e);}));
Object.assign(window,{g60:{scene,model,renderer,get camera(){return camera;},view,exportAssets,useRuby,RUBY}});
document.querySelector('#status')!.textContent=ruby?'Ready · ruby preset':'Ready · transparent studio render';
if(new URLSearchParams(location.search).has('capture')) exportAssets().catch(e=>{document.querySelector('#status')!.textContent=e.message;console.error(e);});
