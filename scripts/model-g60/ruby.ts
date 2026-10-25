import * as THREE from 'three';

// Optional `?preset=ruby` product render: pale key, scarlet/coral rim lights,
// a dark reflective studio environment and a soft contact shadow on an
// otherwise transparent floor. Geometry and materials come from model.ts; only
// the COB emission is raised while this preset is active.
export type Lamp = { name: string; color: string; intensity: number; env: number; az: number; el: number; w: number; h: number; shadow?: boolean; distance?: number };
export type RubyOptions = {
  exposure: number; environment: number; toneMapping: 'aces' | 'agx' | 'neutral';
  camera: { fov: number; az: number; el: number; distance: number; target: [number, number, number] };
  horizon: { color: string; intensity: number };
  key: { color: string; intensity: number; az: number; el: number };
  lamps: Lamp[];
  shadow: { opacity: number; contact: number };
  // Emissive intensity of the warm COB face for this render (model default .42).
  cob: number;
};
// Azimuth is degrees from the front (+Z) toward the brand side (+X); elevation
// is degrees above the horizon. `intensity` drives a direct light (an area
// light, or a shadowed directional light when `shadow` is set) and `env` the
// matching emissive panel in the reflection environment. The env panels are
// placed where each visible bevel mirrors the camera: pale key on the top
// front edge, a pale strip on the front corner, coral on the top/brand edge
// and a low scarlet backlight on every silhouette.
export const RUBY: RubyOptions = {
  exposure: 1.1, environment: 1, toneMapping: 'aces',
  camera: { fov: 24, az: 33, el: 18, distance: 10, target: [0, -.3, 0] },
  horizon: { color: '#5c0716', intensity: .5 },
  key: { color: '#fff0e2', intensity: 1.6, az: -45, el: 50 },
  lamps: [
    { name: 'Pale key softbox', color: '#fff1e6', intensity: 3, env: 24, az: -58, el: 52, w: 4, h: 3 },
    { name: 'Pale edge strip', color: '#ffe8de', intensity: 0, env: 20, az: 57, el: -18, w: .5, h: 3 },
    { name: 'Coral top edge', color: '#ff5536', intensity: 0, env: 24, az: 159, el: 31, w: 3, h: .8 },
    { name: 'Scarlet backlight', color: '#ff1636', intensity: 0, env: 16, az: 212, el: -16, w: 9, h: 4.5 },
    { name: 'Scarlet top glow', color: '#ff1636', intensity: 0, env: .3, az: -147, el: 10, w: 6, h: 1.2 },
    { name: 'Silver sweep', color: '#ffdcd2', intensity: 0, env: 3, az: -40, el: -32, w: 5, h: 1.6 },
    { name: 'Front sheen', color: '#ffd8cc', intensity: 0, env: 4, az: -62, el: -16, w: 3, h: 3 },
    { name: 'Pale side fill', color: '#ffe9e2', intensity: 1, env: 0, az: 75, el: 12, w: 2, h: 2 },
    { name: 'Scarlet left rim', color: '#ff1a3a', intensity: 8, env: 0, az: -105, el: -5, w: 1.2, h: 4, shadow: true },
    // Close washes fall off across the brand side and top: bright at the back.
    { name: 'Scarlet side wash', color: '#ff1a3a', intensity: 12, env: 0, az: 140, el: 25, w: 1.5, h: 2.5, distance: 3 },
    { name: 'Coral top wash', color: '#ff4a32', intensity: 10, env: 0, az: -160, el: 30, w: 2.5, h: 1, distance: 3 },
  ],
  shadow: { opacity: .62, contact: .82 },
  cob: 1.1,
};
// Lowest point of the model (quarter-inch mount) in construction units.
export const FLOOR_Y = -1.42;

function direction(az: number, el: number, distance: number) {
  const a = THREE.MathUtils.degToRad(az), e = THREE.MathUtils.degToRad(el);
  return new THREE.Vector3(Math.cos(e) * Math.sin(a), Math.sin(e), Math.cos(e) * Math.cos(a)).multiplyScalar(distance);
}

function environment(renderer: THREE.WebGLRenderer, o: RubyOptions) {
  const env = new THREE.Scene();
  env.add(new THREE.Mesh(new THREE.SphereGeometry(30, 32, 16), new THREE.MeshBasicMaterial({ color: '#060203', side: THREE.BackSide })));
  // A low ruby band gives grazing black metal a faint red falloff.
  const band = new THREE.MeshBasicMaterial({ color: o.horizon.color, side: THREE.BackSide });
  band.color.multiplyScalar(o.horizon.intensity);
  const horizon = new THREE.Mesh(new THREE.CylinderGeometry(24, 24, 7, 48, 1, true), band);
  horizon.position.y = 1.5; env.add(horizon);
  for (const lamp of o.lamps) if (lamp.env > 0) {
    const material = new THREE.MeshBasicMaterial({ color: lamp.color, side: THREE.DoubleSide });
    material.color.multiplyScalar(lamp.env);
    const panel = new THREE.Mesh(new THREE.PlaneGeometry(lamp.w * 2, lamp.h * 2), material);
    panel.position.copy(direction(lamp.az, lamp.el, 16)); panel.lookAt(0, 0, 0); env.add(panel);
  }
  const pmrem = new THREE.PMREMGenerator(renderer);
  const texture = pmrem.fromScene(env, .02).texture;
  pmrem.dispose();
  env.traverse(object => { if (object instanceof THREE.Mesh) { object.geometry.dispose(); object.material.dispose(); } });
  return texture;
}

// Soft occlusion under the raised body plus a tight contact line under the
// bracket base, drawn in floor coordinates (x, z) and blurred on a canvas.
function contactShadow(o: RubyOptions) {
  const size = 1024, span = 6, px = size / span;
  const canvas = document.createElement('canvas'); canvas.width = canvas.height = size;
  const c = canvas.getContext('2d')!;
  const rect = (x0: number, z0: number, x1: number, z1: number, radius: number, alpha: number, blur: number) => {
    c.filter = `blur(${Math.round(blur * px)}px)`; c.fillStyle = `rgba(0,0,0,${alpha})`;
    c.beginPath(); c.roundRect((x0 + span / 2) * px, (z0 + span / 2) * px, (x1 - x0) * px, (z1 - z0) * px, radius * px); c.fill();
  };
  rect(-1.15, -1.25, 1.15, 1.05, .35, o.shadow.opacity, .22);
  rect(-1.1, -1, 1.1, .95, .3, o.shadow.opacity * .55, .1);
  rect(-1.21, -.35, 1.21, .05, .05, o.shadow.contact, .035);
  const map = new THREE.CanvasTexture(canvas);
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(span, span), new THREE.MeshBasicMaterial({ color: '#000000', map, transparent: true, depthWrite: false }));
  floor.name = 'Contact shadow'; floor.rotation.x = -Math.PI / 2; floor.position.y = FLOOR_Y - .002; floor.renderOrder = -1;
  return floor;
}

export function createRubyRig(scene: THREE.Scene, renderer: THREE.WebGLRenderer, stage: THREE.Object3D, o: RubyOptions = RUBY) {
  const rig = new THREE.Group(); rig.name = 'Ruby render rig'; scene.add(rig);
  const previous = { environment: scene.environment, environmentIntensity: scene.environmentIntensity, exposure: renderer.toneMappingExposure, toneMapping: renderer.toneMapping, roll: stage.rotation.z };
  // The product stands level on the shadow floor.
  stage.rotation.z = 0;
  const emitters: THREE.MeshStandardMaterial[] = [];
  stage.traverse(object => { if (object instanceof THREE.Mesh && object.material.name === 'Warm COB phosphor array') emitters.push(object.material); });
  const emission = emitters.map(material => material.emissiveIntensity);
  for (const material of emitters) material.emissiveIntensity = o.cob;
  const texture = environment(renderer, o);
  scene.environment = texture; scene.environmentIntensity = o.environment;
  renderer.toneMappingExposure = o.exposure;
  renderer.toneMapping = { aces: THREE.ACESFilmicToneMapping, agx: THREE.AgXToneMapping, neutral: THREE.NeutralToneMapping }[o.toneMapping];
  const target = new THREE.Vector3(...o.camera.target);
  // Area lights cannot cast shadows, so a lamp marked `shadow` becomes a
  // shadowed directional light; it cannot leak under the raised body.
  function sun(color: string, intensity: number, az: number, el: number) {
    const light = new THREE.DirectionalLight(color, intensity);
    light.position.copy(direction(az, el, 8)); light.castShadow = true;
    light.shadow.mapSize.set(2048, 2048); Object.assign(light.shadow.camera, { left: -3, right: 3, top: 3, bottom: -3 });
    light.shadow.bias = -.0003; light.shadow.normalBias = .015;
    rig.add(light); return light;
  }
  for (const lamp of o.lamps) if (lamp.intensity > 0) {
    if (lamp.shadow) { sun(lamp.color, lamp.intensity, lamp.az, lamp.el).name = lamp.name; continue; }
    const light = new THREE.RectAreaLight(lamp.color, lamp.intensity, lamp.w, lamp.h);
    light.name = lamp.name; light.position.copy(direction(lamp.az, lamp.el, lamp.distance ?? 7)).add(target); light.lookAt(target); rig.add(light);
  }
  sun(o.key.color, o.key.intensity, o.key.az, o.key.el).name = 'Pale key';
  rig.add(contactShadow(o));
  const camera = new THREE.PerspectiveCamera(o.camera.fov, 4 / 3, .1, 100);
  camera.position.copy(direction(o.camera.az, o.camera.el, o.camera.distance)).add(target); camera.lookAt(target);
  function dispose() {
    rig.traverse(object => { if (object instanceof THREE.Mesh) { object.geometry.dispose(); (object.material as THREE.MeshBasicMaterial).map?.dispose(); object.material.dispose(); } });
    rig.traverse(object => { if (object instanceof THREE.DirectionalLight) object.shadow.dispose(); });
    scene.remove(rig); texture.dispose();
    Object.assign(scene, { environment: previous.environment, environmentIntensity: previous.environmentIntensity });
    Object.assign(renderer, { toneMappingExposure: previous.exposure, toneMapping: previous.toneMapping });
    stage.rotation.z = previous.roll;
    emitters.forEach((material, index) => { material.emissiveIntensity = emission[index]; });
  }
  return { camera, dispose, options: o };
}
