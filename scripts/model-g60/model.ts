import * as THREE from 'three';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';

// An original visual approximation. Construction units are 40 mm; the exported
// root scales them to metres. Front +Z, up +Y, origin at the chassis centre.
export const METRES_PER_UNIT = 0.04;

function texture(size: number, paint: (ctx: CanvasRenderingContext2D, size: number) => void) {
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext('2d')!;
  paint(ctx, size);
  const map = new THREE.CanvasTexture(canvas);
  map.colorSpace = THREE.SRGBColorSpace;
  map.anisotropy = 8;
  return map;
}

export function createG60Model() {
  const root = new THREE.Group();
  root.name = 'MOLUS_G60_original_procedural_approximation';
  root.scale.setScalar(METRES_PER_UNIT);
  root.userData = {
    author: 'Lighttime procedural design study',
    provenance: 'Original geometry; official Zhiyun G60 imagery and user guide consulted, 2026-10-25.',
    disclaimer: 'Visual approximation, not manufacturer CAD. Rear display is illustrative.',
    units: 'metres', front: '+Z', up: '+Y',
  };
  const black = new THREE.MeshStandardMaterial({ name: 'Matte black anodised metal', color: '#17181a', metalness: .68, roughness: .37 });
  const graphite = new THREE.MeshStandardMaterial({ name: 'Graphite edge / grille', color: '#29292c', metalness: .75, roughness: .31 });
  const inset = new THREE.MeshStandardMaterial({ name: 'Recess shadow', color: '#050507', metalness: .1, roughness: .75 });
  const silver = new THREE.MeshStandardMaterial({ name: 'Brushed aluminium', color: '#c1c0ba', metalness: .93, roughness: .28 });
  const darkSilver = new THREE.MeshStandardMaterial({ name: 'Dark machined aluminium', color: '#686a6a', metalness: .9, roughness: .3 });
  const gold = new THREE.MeshStandardMaterial({ name: 'Warm emitter surround', color: '#d5a354', metalness: .65, roughness: .32 });
  const orange = new THREE.MeshStandardMaterial({ name: 'Control index amber', color: '#bc592a', metalness: .45, roughness: .4 });

  function mesh(name: string, geometry: THREE.BufferGeometry, material: THREE.Material, p: number[] = [0,0,0], parent: THREE.Object3D = root) {
    const m = new THREE.Mesh(geometry, material);
    m.name = name;
    m.position.set(p[0], p[1], p[2]);
    m.castShadow = true;
    m.receiveShadow = true;
    parent.add(m);
    return m;
  }
  function box(name: string, d: number[], p: number[], mat = black, radius = .035, parent: THREE.Object3D = root) {
    const geometry=radius<=.02?new THREE.BoxGeometry(d[0],d[1],d[2]):new RoundedBoxGeometry(d[0],d[1],d[2],1,radius);
    return mesh(name, geometry, mat, p,parent);
  }
  function cyl(name: string, radius: number, depth: number, p: number[], mat = silver, axis = 'z', parent: THREE.Object3D = root, segments = 64) {
    const m = mesh(name,new THREE.CylinderGeometry(radius,radius,depth,segments,1),mat,p,parent);
    if(axis === 'z') m.rotation.x = Math.PI/2;
    if(axis === 'x') m.rotation.z = Math.PI/2;
    return m;
  }
  function torus(name: string, r: number, tube: number, z: number, mat: THREE.Material = silver, parent: THREE.Object3D = root) {
    return mesh(name,new THREE.TorusGeometry(r,tube,8,96),mat,[0,0,z],parent);
  }
  function annulus(name: string, profile: number[][], mat: THREE.Material, parent = root) {
    const geo = new THREE.LatheGeometry(profile.map(([r,z])=>new THREE.Vector2(r,z)).reverse(),96);
    const m=mesh(name,geo,mat,[0,0,0],parent);
    m.rotation.x=-Math.PI/2;
    return m;
  }
  function panelMap(paint: (ctx: CanvasRenderingContext2D,s: number)=>void, name: string) {
    return new THREE.MeshStandardMaterial({ name, map:texture(1024,paint), metalness:.45, roughness:.46 });
  }

  // Bevelled, almost-cubic chassis, separate outer rails and recessed seams.
  box('Chassis', [2.08,1.94,1.94], [0,-.025,0],black,.075);
  box('Front face', [2.13,2.04,.11], [0,.015,1.005],graphite,.045);
  box('Rear frame', [2.14,2.04,.10], [0,.015,-1.003],graphite,.045);
  for(const x of [-1.055,1.055]) {
    box('Fine outer edge rail',[.035,2.035,2.04],[x,.015,0],darkSilver,.012);
    box('Lower machined seam',[.045,.028,1.84],[x,-.82,0],graphite,.008);
  }

  // Front COB assembly: dark retaining collar, bevels, textured silver annulus,
  // recessed gold board, pale warm phosphor. No reflector accessory is fitted.
  cyl('Front mount shadow seat',.944,.11,[0,0,1.075],inset);
  annulus('Machined front circular mount',[[.49,-1.11],[.88,-1.11],[.925,-1.16],[.925,-1.22],[.88,-1.265],[.49,-1.265],[.46,-1.235],[.46,-1.17],[.49,-1.11]],silver);
  // Lathe's negative Y becomes +Z after rotation, putting this ring at the front.
  torus('Outer polished bevel',.90,.018,1.245,silver);
  torus('Inner emitter bevel',.474,.023,1.252,darkSilver);
  torus('Front mount groove',.82,.008,1.268,darkSilver);
  torus('Front mount inner groove',.565,.007,1.268,darkSilver);
  for(let i=0;i<96;i++) {
    const a=i*Math.PI*2/96;
    const r=.859;
    const rib=box(`Front knurl ${i+1}`,[.014,.077,.012],[Math.cos(a)*r,Math.sin(a)*r,1.27],i%3===0?darkSilver:silver,.003);
    rib.rotation.z=a-Math.PI/2+.28;
  }
  // Three bayonet catches and four black screw heads make the face mechanical.
  for(let i=0;i<3;i++) {
    const a=(i/3)*Math.PI*2+.38;
    const catchMesh=box(`Accessory bayonet ${i+1}`,[.13,.18,.055],[Math.cos(a)*.899,Math.sin(a)*.899,1.278],darkSilver,.012);
    catchMesh.rotation.z=a;
  }
  for(let i=0;i<4;i++) {
    const a=Math.PI*.25+i*Math.PI*.5;
    const x=Math.cos(a)*.659,y=Math.sin(a)*.659;
    cyl(`COB screw ${i+1}`,.042,.013,[x,y,1.279],inset,'z',root,16);
    const slot=box('Screw driver slot',[.045,.008,.005],[x,y,1.288],darkSilver,.002);
    slot.rotation.z=a;
  }
  cyl('Gold emitter carrier',.454,.033,[0,0,1.218],gold);
  torus('Phosphor retaining lip',.414,.014,1.245,gold);
  const emitterMap=texture(512,(ctx,s)=>{
    ctx.fillStyle='#ffe3a1';ctx.fillRect(0,0,s,s);
    const glow=ctx.createRadialGradient(s*.4,s*.36,0,s*.5,s*.5,s*.65);
    glow.addColorStop(0,'#fff2d1');glow.addColorStop(1,'#e5b95f');ctx.fillStyle=glow;ctx.fillRect(0,0,s,s);
    for(let y=18;y<s;y+=22) for(let x=18;x<s;x+=22) {
      ctx.fillStyle=((x+y)%44===14)?'rgba(255,251,221,.38)':'rgba(162,113,37,.19)';
      ctx.fillRect(x,y,13,13);
    }
  });
  const emitterMat=new THREE.MeshStandardMaterial({name:'Warm COB phosphor array',map:emitterMap,color:'#fff4d4',emissive:'#ffd398',emissiveIntensity:.42,metalness:.12,roughness:.46});
  cyl('COB light emitting surface',.397,.012,[0,0,1.248],emitterMat);

  // Top grille with real capsule-shaped perforations. The circular fan and
  // blades sit below the holes, separated by an air cavity.
  const topGroup=new THREE.Group();topGroup.name='Top cooling grille';root.add(topGroup);
  const shape=new THREE.Shape();
  shape.moveTo(-.97,-.88);shape.lineTo(.97,-.88);shape.lineTo(.97,.88);shape.lineTo(-.97,.88);shape.closePath();
  const angle=-Math.PI/4;
  for(let i=-6;i<=6;i++) {
    const offset=i*.14;
    const len=1.73-Math.abs(i)*.14;
    const hole=new THREE.Path();const half=len/2,rad=.032;
    const points:THREE.Vector2[]=[];
    for(let j=0;j<=10;j++) {const a=-Math.PI/2+j/10*Math.PI;points.push(new THREE.Vector2(half+Math.cos(a)*rad,Math.sin(a)*rad));}
    for(let j=0;j<=10;j++) {const a=Math.PI/2+j/10*Math.PI;points.push(new THREE.Vector2(-half+Math.cos(a)*rad,Math.sin(a)*rad));}
    const rotated=points.map(p=>new THREE.Vector2(p.x*Math.cos(angle)-p.y*Math.sin(angle)+offset*.707,p.x*Math.sin(angle)+p.y*Math.cos(angle)+offset*.707));
    hole.setFromPoints(rotated);hole.closePath();shape.holes.push(hole);
  }
  const grille=mesh('Perforated top aluminium',new THREE.ExtrudeGeometry(shape,{depth:.035,bevelEnabled:true,bevelSize:.009,bevelThickness:.009,bevelSegments:1,curveSegments:4}),graphite,[0,1.012,0],topGroup);
  grille.rotation.x=-Math.PI/2;
  cyl('Top fan shadow',.83,.014,[0,.967,0],inset,'y');
  cyl('Fan centre',.19,.02,[0,.987,0],darkSilver,'y',root,32);
  for(let i=0;i<7;i++) {
    const a=i*Math.PI*2/7;
    const blade=box('Fan blade',[.44,.018,.23],[Math.cos(a)*.44,.983,Math.sin(a)*.44],black,.06);
    blade.rotation.y=-a+.5;
  }
  // Front top lip brand plate, power button, countersunk frame screws.
  box('Power button',[.21,.035,.095],[-.76,1.067,-.76],orange,.035);
  const topMark=panelMap((c,s)=>{c.fillStyle='#222326';c.fillRect(0,0,s,s);c.fillStyle='#aaa8a3';c.font='600 130px sans-serif';c.fillText('ZHIYUN',80,570);},'Top manufacturer mark');
  const topLabel=mesh('Top ZHIYUN mark',new THREE.PlaneGeometry(.51,.19),topMark,[.53,1.059,-.77]);topLabel.rotation.x=-Math.PI/2;

  // Right-side graphic plate. Entirely new artwork, no photograph or logo file.
  const brand=panelMap((c,s)=>{
    c.fillStyle='#151619';c.fillRect(0,0,s,s);
    c.lineWidth=27;c.strokeStyle='#232428';
    for(let i=-s;i<s*2;i+=140){c.beginPath();c.moveTo(i,s);c.lineTo(i+s,0);c.stroke();}
    c.fillStyle='#ece7dc';c.font='300 490px sans-serif';c.fillText('60',63,560);
    c.font='600 158px sans-serif';c.fillText('W',80,742);
    c.fillStyle='#a4a19a';c.font='500 37px sans-serif';c.fillText('POCKET COB LIGHT',510,640);c.fillText('DESIGNED BY ZHIYUN',510,695);
    c.fillStyle='#e1ddd4';c.font='600 52px sans-serif';c.fillText('MOLUS G60',510,860);
    c.fillStyle='#b96c41';c.fillRect(79,840,88,5);
  },'Original G60 side typography');
  box('Side brand plate backing',[.055,1.51,1.91],[1.089,.19,-.01],inset,.025);
  const brandPlate=mesh('MOLUS 60W side graphic',new THREE.PlaneGeometry(1.89,1.48),brand,[1.12,.19,-.01]);brandPlate.rotation.y=Math.PI/2;
  // Left side fan grille uses deep slots and a separate dark fan housing.
  box('Side fan recess',[.035,1.72,1.72],[-1.058,.04,0],inset,.04);
  for(let i=-6;i<=6;i++) {
    const bar=box('Side diagonal cooling louvre',[.045,.065,1.61-Math.abs(i)*.095],[-1.086,i*.126,0],graphite,.018);
    bar.rotation.x=-.45;
  }
  // Rear display and separate CCT / DIM knobs, with physical radial grip ribs.
  box('Rear control panel',[1.91,1.54,.045],[0,.19,-1.065],black,.025);
  box('Display bezel',[.89,.52,.05],[0,.55,-1.106],inset,.035);
  const display=panelMap((c,s)=>{
    c.fillStyle='#0b141a';c.fillRect(0,0,s,s);c.fillStyle='#89a9b7';c.font='42px monospace';c.fillText('CCT                PD',70,135);
    c.fillStyle='#d5e8e6';c.font='600 146px monospace';c.fillText('4300 K',65,420);c.fillText('65 %',65,735);c.fillStyle='#6a888d';c.fillRect(70,875,880,8);
  },'Illustrative display (not live readback)');
  const screen=mesh('Illustrative rear display',new THREE.PlaneGeometry(.77,.41),display,[0,.55,-1.136]);screen.rotation.y=Math.PI;
  for(const [x,label] of [[-.49,'DIM'],[.49,'CCT']] as const) {
    const knob=new THREE.Group();knob.name=`${label} knob`;knob.position.set(x,-.15,-1.19);root.add(knob);
    cyl('Knob seat',.337,.065,[0,0,0],darkSilver,'z',knob,48);
    const ring=torus('Amber control ring',.306,.014,-.044,orange,knob);
    cyl('Knob grip',.267,.17,[0,0,-.105],black,'z',knob,48);
    cyl('Knob cap',.245,.018,[0,0,-.196],graphite,'z',knob,48);
    for(let i=0;i<36;i++) {
      const a=i*Math.PI*2/36;
      const rib=box('Knurled control grip',[.018,.023,.132],[Math.cos(a)*.264,Math.sin(a)*.264,-.105],graphite,.005,knob);rib.rotation.z=a;
    }
    if(label==='CCT') for(const rotation of [Math.PI/4,-Math.PI/4]) {const wing=box('CCT crossed grip',[.53,.105,.058],[0,0,-.228],graphite,.035,knob);wing.rotation.z=rotation;}
    else box('DIM white index',[.015,.058,.008],[0,.17,-.212],silver,.004,knob);
  }
  cyl('DC input rim',.09,.028,[-.61,-.72,-1.073],darkSilver,'z',root,24);
  cyl('DC input socket',.055,.033,[-.61,-.72,-1.09],inset,'z',root,24);
  box('USB-C socket',[.026,.25,.095],[-1.095,-.33,-.57],inset,.018);
  // Folding U bracket is kept close to the body to preserve the cube silhouette.
  const bracket=new THREE.Group();bracket.name='Folding stand bracket';root.add(bracket);
  for(const x of [-1.15,1.15]) {
    box('Bracket arm',[.11,.69,.25],[x,-1.005,-.15],black,.025,bracket);
    cyl('Bracket pivot',.139,.07,[x+(x>0?.07:-.07),-.7,-.15],silver,'x',bracket,32);
    cyl('Pivot inset',.075,.075,[x+(x>0?.075:-.075),-.7,-.15],darkSilver,'x',bracket,32);
  }
  box('Bracket base',[2.38,.105,.37],[0,-1.352,-.15],graphite,.035,bracket);
  cyl('Quarter-inch mount shadow',.074,.012,[0,-1.414,-.15],inset,'y',bracket,24);
  return root;
}
