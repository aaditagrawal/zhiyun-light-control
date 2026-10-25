import { inflateSync } from 'node:zlib';
import { resolve } from 'node:path';

const root=resolve(import.meta.dir,'../..');
const assets=resolve(root,'apps/android/assets/g60');
function assert(condition:unknown,message:string): asserts condition {if(!condition) throw new Error(message);}

// Decode the generated RGBA PNGs to verify actual alpha pixels, not a metadata
// declaration or a black-background screenshot. No extra image dependency.
async function inspectPng(name:string,expectedWidth:number,expectedHeight:number) {
  const data=Buffer.from(await Bun.file(resolve(assets,name)).arrayBuffer());
  assert(data.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10])),`${name}: PNG signature`);
  let width=0,height=0;const compressed:Buffer[]=[];
  for(let p=8;p<data.length;) {
    const length=data.readUInt32BE(p),type=data.toString('ascii',p+4,p+8),body=data.subarray(p+8,p+8+length);
    if(type==='IHDR'){width=body.readUInt32BE(0);height=body.readUInt32BE(4);assert(body[8]===8&&body[9]===6&&body[12]===0,`${name}: expected non-interlaced 8-bit RGBA`);}
    if(type==='IDAT')compressed.push(body);p+=length+12;
  }
  assert(width===expectedWidth&&height===expectedHeight,`${name}: dimensions`);
  const raw=inflateSync(Buffer.concat(compressed));const stride=width*4,pixels=Buffer.alloc(stride*height);
  function paeth(a:number,b:number,c:number){const p=a+b-c,pa=Math.abs(p-a),pb=Math.abs(p-b),pc=Math.abs(p-c);return pa<=pb&&pa<=pc?a:pb<=pc?b:c;}
  for(let y=0;y<height;y++) {
    const filter=raw[y*(stride+1)];assert(filter<=4,`${name}: PNG filter`);
    for(let x=0;x<stride;x++) {
      const index=y*stride+x,a=x>=4?pixels[index-4]:0,b=y>0?pixels[index-stride]:0,c=y>0&&x>=4?pixels[index-stride-4]:0;
      const prediction=[0,a,b,Math.floor((a+b)/2),paeth(a,b,c)][filter];
      pixels[index]=(raw[y*(stride+1)+1+x]+prediction)&255;
    }
  }
  const alpha=(x:number,y:number)=>pixels[(y*width+x)*4+3];
  assert([alpha(0,0),alpha(width-1,0),alpha(0,height-1),alpha(width-1,height-1)].every(x=>x===0),`${name}: transparent corners`);
  let minX=width,minY=height,maxX=-1,maxY=-1,opaque=0,partial=0;
  for(let y=0;y<height;y++)for(let x=0;x<width;x++){const a=alpha(x,y);if(a>0){minX=Math.min(minX,x);maxX=Math.max(maxX,x);minY=Math.min(minY,y);maxY=Math.max(maxY,y);}if(a===255)opaque++;else if(a>0)partial++;}
  assert(partial>0,`${name}: antialiased / graduated alpha`);
  if(name!=='glow.png')assert(minX>0&&minY>0&&maxX<width-1&&maxY<height-1&&opaque>1000,`${name}: uncropped visible model`);
  else {assert(alpha(width/2,height/2)>150,'glow: luminous centre');for(let x=width/2+1;x<width;x++)assert(alpha(x,height/2)<=alpha(x-1,height/2)+1,'glow: smooth radial falloff');}
  return {name,width,height,bytes:data.length,bounds:[minX,minY,maxX,maxY],opaquePixels:opaque,partialAlphaPixels:partial,transparentCorners:true};
}

const pngs=await Promise.all([inspectPng('hero.png',960,720),inspectPng('compact.png',480,360),inspectPng('glow.png',256,256)]);
// Optional `?preset=ruby` renders are checked the same way when present.
for(const [name,w,h] of [['hero-ruby.png',960,720],['compact-ruby.png',480,360]] as const) if(await Bun.file(resolve(assets,name)).exists()) pngs.push(await inspectPng(name,w,h));
const glb=Buffer.from(await Bun.file(resolve(assets,'g60.glb')).arrayBuffer());
assert(glb.toString('ascii',0,4)==='glTF'&&glb.readUInt32LE(4)===2,'GLB 2.0 header');
assert(glb.readUInt32LE(8)===glb.length,'GLB declared byte length');
const jsonLength=glb.readUInt32LE(12);assert(glb.toString('ascii',16,20)==='JSON','GLB JSON chunk');
const gltf=JSON.parse(glb.toString('utf8',20,20+jsonLength));
assert(gltf.buffers.length===1&&!gltf.buffers[0].uri,'Self-contained binary geometry');
assert(gltf.images.every((i:any)=>i.bufferView!==undefined&&!i.uri),'Embedded procedural textures');
assert(gltf.nodes.some((n:any)=>n.name==='MOLUS_G60_original_procedural_approximation'),'Named model root');
assert(gltf.accessors.every((a:any)=>!a.min||[...a.min,...a.max].every(Number.isFinite)),'Finite geometry bounds');
const modelRoot=gltf.nodes.find((n:any)=>n.name==='MOLUS_G60_original_procedural_approximation');
assert(modelRoot.matrix?.[0]===.04||modelRoot.scale?.[0]===.04,'Model export in metres');
const triangles=gltf.meshes.reduce((total:number,m:any)=>total+m.primitives.reduce((n:number,p:any)=>n+(p.indices!==undefined?gltf.accessors[p.indices].count:gltf.accessors[p.attributes.POSITION].count)/3,0),0);
const report={checkedAt:new Date().toISOString(),pngs,glb:{bytes:glb.length,meshes:gltf.meshes.length,materials:gltf.materials.length,embeddedTextures:gltf.images.length,triangles,externalResources:0,units:'metres'},status:'passed'};
await Bun.write(resolve(root,'artifacts/redesign-20261025/model/verification.json'),JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify(report,null,2));
