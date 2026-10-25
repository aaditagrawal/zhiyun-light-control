import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { METRES_PER_UNIT } from './model';
import { createRubyRig, RUBY, type RubyOptions } from './ruby';

// Touch-rotatable G60 showcase for the Android WebView, bundled by
// showcase-build.ts into one offline HTML document with the embedded g60.glb.
// The model is an illustration: it never reads, writes or mirrors a real light.
//
// The model turns on a turntable (yaw) while the camera keeps the ruby hero
// azimuth and only changes elevation, so the pale key and scarlet rims stay
// where they flatter the product from every side.

type Action = 'front' | 'back' | 'reset' | 'spin' | 'pause' | 'resume' | 'dispose';
type Outgoing = { type: 'ready' } | { type: 'error'; message: string } | { type: 'state'; spinning: boolean } | { type: 'interaction' } | { type: 'progress'; phase: string };

const ACTIONS: readonly Action[] = ['front', 'back', 'reset', 'spin', 'pause', 'resume', 'dispose'];
const HERO = { yaw: 0, elevation: RUBY.camera.el, zoom: 1 };
// `#inline` (or an injected `window.__LIGHTTIME_INLINE__ = true`) renders the same
// rig on a transparent page inside the app's own artwork, with a tighter fit and
// a narrower zoom range because that view has no Reset control.
const INLINE = location.hash === '#inline' || location.hash === '#inline-reduce-motion'
  || (window as any).__LIGHTTIME_INLINE__ === true;
const LIMITS = { elevation: [-50, 80], zoom: INLINE ? [.8, 1.5] : [.55, 1.7] } as const;
const SPIN_DEG_PER_S = 8;
const RESUME_AFTER_MS = 2600;
const MAX_DPR = 1.5;
const PADDING = INLINE ? 1.03 : 1.1;
// A confirmed software WebGL rasteriser (SwiftShader, llvmpipe, emulators)
// keeps the same model, materials, lights, environment and contact shadow but
// renders at 1x without MSAA or realtime shadow-map passes, at a capped rate.
// ANGLE alone is not a signal: it fronts real GPUs on Android and Windows.
const SOFTWARE_RENDERER = /swiftshader|llvmpipe|softpipe|lavapipe|software|basic render driver/i;
const SOFTWARE_FPS = 30;
const FAILURE = 'The 3D preview could not load.';

const reducedQuery = window.matchMedia?.('(prefers-reduced-motion: reduce)');
const canvas = document.getElementById('g60') as HTMLCanvasElement;

// Interaction and lifecycle state exists before the model loads, so commands
// sent early (for example `pause` while the app is backgrounded) still apply.
const view = { yaw: HERO.yaw, elevation: HERO.elevation, zoom: HERO.zoom };
const target = { ...view };
const motion = { yaw: 0, elevation: 0 };
// Native may inject `window.__LIGHTTIME_REDUCED_MOTION__ = true` before load.
const prefersReduced = () => location.hash === '#reduce-motion' || location.hash === '#inline-reduce-motion'
  || (window as any).__LIGHTTIME_REDUCED_MOTION__ === true || !!reducedQuery?.matches;
let reducedMotion = prefersReduced();
let spinPreference = !reducedMotion;
let lifecyclePaused = false;
let hidden = document.visibilityState === 'hidden';
let dragging = false;
let resumeAt = 0;
let ready = false;
let failed = false;
let disposed = false;
let contextLost = false;
let frame = 0;
let lastTime = 0;
let frames = 0;
let lastInteraction = 0;
let resumeTimer = 0;

let renderer: THREE.WebGLRenderer | undefined;
let scene: THREE.Scene | undefined;
let camera: THREE.PerspectiveCamera | undefined;
let turntable: THREE.Group | undefined;
let stage: THREE.Group | undefined;
let contact: THREE.Mesh | undefined;
let rig: ReturnType<typeof createRubyRig> | undefined;
const fit = { radius: 1, centerY: 0, distance: 10 };
let meshCount = 0;
let quality = { software: false, renderer: '', forced: false };

// Read the unmasked renderer from a throwaway context before the real one is
// created, so antialiasing can still be chosen. Native may inject
// `window.__LIGHTTIME_SHOWCASE_QUALITY__ = 'software' | 'hardware'` to force a
// path for testing.
function detectQuality() {
  let name = '';
  try {
    const probe = document.createElement('canvas');
    const gl = (probe.getContext('webgl2') ?? probe.getContext('webgl')) as WebGLRenderingContext | null;
    if (gl) {
      const debug = gl.getExtension('WEBGL_debug_renderer_info');
      name = String(gl.getParameter(debug ? debug.UNMASKED_RENDERER_WEBGL : gl.RENDERER) ?? '');
      gl.getExtension('WEBGL_lose_context')?.loseContext();
    }
  } catch { /* The real renderer reports any WebGL failure. */ }
  const forced = (window as any).__LIGHTTIME_SHOWCASE_QUALITY__;
  const software = forced === 'software' || (forced !== 'hardware' && SOFTWARE_RENDERER.test(name));
  return { software, renderer: name, forced: forced === 'software' || forced === 'hardware' };
}

function post(message: Outgoing) {
  const text = JSON.stringify(message);
  try {
    const native = (window as any).ReactNativeWebView;
    if (native?.postMessage) native.postMessage(text);
    else if (window.parent !== window) window.parent.postMessage(text, '*');
  } catch { /* A missing or closed parent is not a renderer failure. */ }
}

// Report the first failure once and stop drawing; never rethrow from here, so
// a failing frame cannot loop through window error handlers.
function fail(error: unknown) {
  if (failed || disposed) return;
  failed = true;
  stopLoop();
  console.error('G60 showcase', error);
  document.body.classList.add('failed');
  post({ type: 'error', message: FAILURE });
}

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));
const ease = (dt: number, rate: number) => 1 - Math.exp(-dt * rate);
const shortest = (from: number, to: number) => from + ((((to - from) % 360) + 540) % 360) - 180;
const smoothstep = (a: number, b: number, x: number) => { const t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); };

function spinActive(now = performance.now()) {
  return spinPreference && !dragging && now >= resumeAt;
}
function running() {
  return ready && !failed && !disposed && !lifecyclePaused && !hidden && !contextLost;
}
function settled() {
  return Math.abs(target.yaw - view.yaw) < .01 && Math.abs(target.elevation - view.elevation) < .01
    && Math.abs(target.zoom - view.zoom) < .0005 && Math.abs(motion.yaw) < .01 && Math.abs(motion.elevation) < .01;
}

function requestRender() {
  if (!running() || frame) return;
  lastTime = 0;
  frame = requestAnimationFrame(tick);
}
function stopLoop() {
  if (frame) cancelAnimationFrame(frame);
  clearTimeout(resumeTimer);
  frame = resumeTimer = 0;
}

function tick(time: number) {
  frame = 0;
  if (!running()) return;
  try {
    // The software path skips vsyncs to hold a steady capped rate; dt still
    // spans the real elapsed time, so spin speed and inertia are unchanged.
    if (quality.software && lastTime && time - lastTime < 1000 / SOFTWARE_FPS - 4) { frame = requestAnimationFrame(tick); return; }
    const dt = lastTime ? Math.min((time - lastTime) / 1000, quality.software ? 1 / 8 : 1 / 20) : 1 / 60;
    lastTime = time;
    step(dt, performance.now());
    draw();
    // Keep the loop only while something moves; a static view renders once
    // and wakes when the idle spin is due to resume.
    const now = performance.now();
    if (dragging || spinActive(now) || !settled()) frame = requestAnimationFrame(tick);
    else if (spinPreference && now < resumeAt) { clearTimeout(resumeTimer); resumeTimer = setTimeout(requestRender, resumeAt - now) as unknown as number; }
  } catch (error) { fail(error); }
}

function step(dt: number, now: number) {
  if (!dragging) {
    // A fling decays into the gentle idle spin (or to rest when spin is off).
    const goal = spinActive(now) ? SPIN_DEG_PER_S : 0;
    const rate = Math.abs(motion.yaw) > SPIN_DEG_PER_S ? 2.2 : goal ? 1.6 : 2.6;
    motion.yaw += (goal - motion.yaw) * ease(dt, rate);
    if (!goal && Math.abs(motion.yaw) < .01) motion.yaw = 0;
    motion.elevation *= 1 - ease(dt, 5);
    if (Math.abs(motion.elevation) < .01) motion.elevation = 0;
    target.yaw += motion.yaw * dt;
    target.elevation = clamp(target.elevation + motion.elevation * dt, ...LIMITS.elevation);
  }
  if (reducedMotion) Object.assign(view, target);
  else {
    const k = ease(dt, dragging ? 22 : 9);
    view.yaw += (target.yaw - view.yaw) * k;
    view.elevation += (target.elevation - view.elevation) * k;
    view.zoom += (target.zoom - view.zoom) * ease(dt, 10);
  }
  // Keep angles bounded without a visible jump.
  if (Math.abs(view.yaw) > 3600) { const wrap = Math.round(view.yaw / 360) * 360; view.yaw -= wrap; target.yaw -= wrap; }
}

function draw() {
  if (!renderer || !scene || !camera || !turntable) return;
  turntable.rotation.y = THREE.MathUtils.degToRad(view.yaw);
  const az = THREE.MathUtils.degToRad(RUBY.camera.az), el = THREE.MathUtils.degToRad(view.elevation);
  const distance = fit.distance * view.zoom;
  camera.position.set(Math.cos(el) * Math.sin(az), Math.sin(el), Math.cos(el) * Math.cos(az)).multiplyScalar(distance);
  camera.position.y += fit.centerY;
  camera.near = Math.max(.05, distance - fit.radius * 3);
  camera.far = distance + fit.radius * 4;
  camera.updateProjectionMatrix();
  camera.lookAt(0, fit.centerY, 0);
  // Fade the floor shadow as the camera drops below the floor.
  if (contact) (contact.material as THREE.MeshBasicMaterial).opacity = smoothstep(-14, 4, view.elevation);
  renderer.render(scene, camera);
  frames++;
}

function resize() {
  if (!renderer || !camera || disposed) return;
  const width = Math.max(1, window.innerWidth), height = Math.max(1, window.innerHeight);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, quality.software ? 1 : MAX_DPR));
  renderer.setSize(width, height, false);
  camera.aspect = width / height;
  // Fit the turntable's bounding sphere to the narrower field of view, so the
  // product is never cropped at any yaw, elevation, viewport or orientation.
  const vertical = THREE.MathUtils.degToRad(camera.fov) / 2;
  const horizontal = Math.atan(Math.tan(vertical) * camera.aspect);
  fit.distance = fit.radius * PADDING / Math.sin(Math.min(vertical, horizontal));
  if (ready && !frame && running()) { draw(); }
}

function setSpin(on: boolean) {
  if (spinPreference === on) return;
  spinPreference = on;
  resumeAt = 0;
  post({ type: 'state', spinning: on });
  requestRender();
}

function face(yaw: number) {
  setSpin(false);
  motion.yaw = motion.elevation = 0;
  target.yaw = shortest(view.yaw, yaw);
  target.elevation = HERO.elevation;
  requestRender();
}

function command(action: unknown): boolean {
  if (typeof action !== 'string' || !(ACTIONS as readonly string[]).includes(action) || disposed) return false;
  switch (action as Action) {
    case 'front': face(RUBY.camera.az); break;
    case 'back': face(RUBY.camera.az + 180); break;
    case 'reset':
      motion.yaw = motion.elevation = 0;
      Object.assign(target, { yaw: shortest(view.yaw, HERO.yaw), elevation: HERO.elevation, zoom: HERO.zoom });
      setSpin(!reducedMotion);
      requestRender();
      break;
    case 'spin': setSpin(!spinPreference); break;
    // Lifecycle only: the user's spin preference is kept and restored.
    case 'pause': lifecyclePaused = true; stopLoop(); break;
    case 'resume': lifecyclePaused = false; resumeAt = 0; requestRender(); break;
    case 'dispose': dispose(); break;
  }
  return true;
}

function getState() {
  const info = renderer?.info;
  return {
    ready, failed, disposed, contextLost, inline: INLINE,
    paused: lifecyclePaused, hidden, reducedMotion,
    spinning: spinPreference, meshes: meshCount, spinActive: ready && running() && spinActive(), animating: frame !== 0, dragging,
    yaw: +view.yaw.toFixed(3), elevation: +view.elevation.toFixed(3), zoom: +view.zoom.toFixed(4),
    target: { yaw: +target.yaw.toFixed(3), elevation: +target.elevation.toFixed(3), zoom: +target.zoom.toFixed(4) },
    camera: camera ? { fov: camera.fov, aspect: +camera.aspect.toFixed(4), distance: +(fit.distance * view.zoom).toFixed(3), fitRadius: +fit.radius.toFixed(4) } : null,
    renderer: renderer && info ? {
      pixelRatio: renderer.getPixelRatio(), width: renderer.domElement.width, height: renderer.domElement.height,
      calls: info.render.calls, triangles: info.render.triangles, geometries: info.memory.geometries, textures: info.memory.textures,
    } : null,
    quality: {
      tier: quality.software ? 'software' : 'hardware', renderer: quality.renderer, forced: quality.forced,
      maxPixelRatio: quality.software ? 1 : MAX_DPR, antialias: !quality.software,
      shadowMaps: renderer?.shadowMap.enabled ?? !quality.software, maxFps: quality.software ? SOFTWARE_FPS : null,
    },
    frames,
  };
}

// Pointer gestures: one finger orbits (yaw + elevation), two fingers pinch to
// zoom, the wheel zooms. There is no pan, so the product stays centred.
const pointers = new Map<number, { x: number; y: number }>();
let pinch = 0;
let lastMove = 0;

function interaction() {
  const now = performance.now();
  if (now - lastInteraction > 400) post({ type: 'interaction' });
  lastInteraction = now;
}
function spread() {
  const [a, b] = [...pointers.values()];
  return a && b ? Math.hypot(a.x - b.x, a.y - b.y) : 0;
}
function onPointerDown(event: PointerEvent) {
  if (disposed || failed) return;
  try { canvas.setPointerCapture(event.pointerId); } catch { /* Synthetic or already released pointer. */ }
  pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
  if (pointers.size === 1) interaction();
  dragging = true;
  motion.yaw = motion.elevation = 0;
  pinch = pointers.size >= 2 ? spread() : 0;
  lastMove = performance.now();
  requestRender();
}
function onPointerMove(event: PointerEvent) {
  const previous = pointers.get(event.pointerId);
  if (!previous || disposed) return;
  const point = { x: event.clientX, y: event.clientY };
  pointers.set(event.pointerId, point);
  const now = performance.now();
  if (pointers.size >= 2) {
    const next = spread();
    if (pinch > 0 && next > 0) target.zoom = clamp(target.zoom * pinch / next, ...LIMITS.zoom);
    pinch = next;
  } else {
    const degPerPx = 270 / Math.max(320, Math.min(window.innerWidth, 900));
    const dYaw = (point.x - previous.x) * degPerPx, dEl = (point.y - previous.y) * degPerPx * .7;
    target.yaw += dYaw;
    target.elevation = clamp(target.elevation + dEl, ...LIMITS.elevation);
    // Smoothed release velocity in degrees per second.
    const dt = Math.max(1, now - lastMove) / 1000;
    motion.yaw = motion.yaw * .4 + dYaw / dt * .6;
    motion.elevation = motion.elevation * .4 + dEl / dt * .6;
  }
  lastMove = now;
  requestRender();
}
function onPointerUp(event: PointerEvent) {
  if (!pointers.delete(event.pointerId)) return;
  pinch = pointers.size >= 2 ? spread() : 0;
  if (pointers.size) return;
  dragging = false;
  const stale = performance.now() - lastMove > 90;
  if (stale || reducedMotion) motion.yaw = motion.elevation = 0;
  motion.yaw = clamp(motion.yaw, -720, 720);
  motion.elevation = clamp(motion.elevation, -240, 240);
  resumeAt = performance.now() + RESUME_AFTER_MS;
  requestRender();
}
function onWheel(event: WheelEvent) {
  if (disposed || failed) return;
  event.preventDefault();
  interaction();
  const delta = event.deltaMode === 1 ? event.deltaY * 16 : event.deltaY;
  target.zoom = clamp(target.zoom * Math.exp(clamp(delta, -200, 200) * .0015), ...LIMITS.zoom);
  requestRender();
}

function onMessage(event: MessageEvent) {
  let data = event.data;
  if (typeof data === 'string') { try { data = JSON.parse(data); } catch { return; } }
  if (data && typeof data === 'object' && data.type === 'lighttime-showcase-command') command(data.action);
}
function onVisibility() {
  hidden = document.visibilityState === 'hidden';
  if (hidden) stopLoop(); else { resumeAt = 0; requestRender(); }
}
function onReducedMotion() {
  const next = prefersReduced();
  if (next === reducedMotion) return;
  reducedMotion = next;
  if (next) { motion.yaw = motion.elevation = 0; setSpin(false); }
  requestRender();
}
function onContextLost(event: Event) { event.preventDefault(); contextLost = true; stopLoop(); }
function onContextRestored() { contextLost = false; resize(); requestRender(); }
function onResize() { resize(); requestRender(); }
// Uncaught errors only count while loading; frames report their own failures.
function onError(event: ErrorEvent | PromiseRejectionEvent) {
  if (!ready) fail('reason' in event ? event.reason : event.error ?? event.message);
}

const listeners: [EventTarget, string, EventListener, AddEventListenerOptions?][] = [
  [canvas, 'pointerdown', onPointerDown as EventListener],
  [canvas, 'pointermove', onPointerMove as EventListener],
  [canvas, 'pointerup', onPointerUp as EventListener],
  [canvas, 'pointercancel', onPointerUp as EventListener],
  [canvas, 'lostpointercapture', onPointerUp as EventListener],
  [canvas, 'wheel', onWheel as EventListener, { passive: false }],
  [canvas, 'webglcontextlost', onContextLost],
  [canvas, 'webglcontextrestored', onContextRestored],
  [canvas, 'contextmenu', (event: Event) => event.preventDefault()],
  [window, 'resize', onResize],
  [window, 'message', onMessage as EventListener],
  // Android react-native-webview delivers injected messages on document.
  [document, 'message', onMessage as EventListener],
  [document, 'visibilitychange', onVisibility],
  [window, 'pagehide', () => dispose()],
  [window, 'error', onError as EventListener],
  [window, 'unhandledrejection', onError as EventListener],
];
for (const [node, type, listener, options] of listeners) node.addEventListener(type, listener, options);
reducedQuery?.addEventListener?.('change', onReducedMotion);

function dispose() {
  if (disposed) return;
  disposed = true;
  stopLoop();
  for (const [node, type, listener, options] of listeners) node.removeEventListener(type, listener, options);
  reducedQuery?.removeEventListener?.('change', onReducedMotion);
  pointers.clear();
  try {
    rig?.dispose();
    const textures = new Set<THREE.Texture>();
    turntable?.traverse(object => {
      if (!(object instanceof THREE.Mesh)) return;
      object.geometry.dispose();
      for (const material of ([] as THREE.Material[]).concat(object.material)) {
        for (const value of Object.values(material)) if (value instanceof THREE.Texture) textures.add(value);
        material.dispose();
      }
    });
    textures.forEach(texture => texture.dispose());
    renderer?.dispose();
    renderer?.forceContextLoss();
  } catch (error) { console.error('G60 showcase dispose', error); }
  renderer = scene = camera = turntable = stage = contact = rig = undefined;
}

function decodeModel() {
  const source = document.getElementById('g60-glb');
  const text = source?.textContent?.trim();
  if (!text) throw new Error('Embedded model missing');
  source!.remove();
  const binary = atob(text);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

// The product is static, so bake every mesh's transform and merge meshes that
// share a material and attribute layout: one draw call per material instead of
// one per part, with identical shading. Mirrored parts keep their own mesh.
function batch(source: THREE.Object3D) {
  source.updateMatrixWorld(true);
  const groups = new Map<string, { material: THREE.Material; geometries: THREE.BufferGeometry[] }>();
  const merged = new THREE.Group();
  merged.name = 'G60 batched';
  const keep: THREE.Mesh[] = [];
  source.traverse(object => {
    if (!(object instanceof THREE.Mesh)) return;
    const geometry = (object.geometry as THREE.BufferGeometry).clone().applyMatrix4(object.matrixWorld);
    if (Array.isArray(object.material) || object.matrixWorld.determinant() < 0 || Object.keys(geometry.morphAttributes).length) {
      const mesh = new THREE.Mesh(geometry, object.material); mesh.name = object.name; keep.push(mesh); return;
    }
    const layout = Object.entries(geometry.attributes).sort(([a], [b]) => a.localeCompare(b))
      .map(([name, attribute]) => `${name}:${attribute.itemSize}:${attribute.normalized}:${(attribute as THREE.BufferAttribute).array.constructor.name}`);
    const key = `${object.material.uuid}|${geometry.index ? 'i' : 'n'}|${layout.join(',')}`;
    const group = groups.get(key) ?? { material: object.material, geometries: [] };
    group.geometries.push(geometry); groups.set(key, group);
  });
  for (const { material, geometries } of groups.values()) {
    const geometry = geometries.length > 1 ? mergeGeometries(geometries) : geometries[0];
    if (!geometry) { for (const part of geometries) keep.push(new THREE.Mesh(part, material)); continue; }
    if (geometries.length > 1) geometries.forEach(part => part.dispose());
    const mesh = new THREE.Mesh(geometry, material); mesh.name = material.name; merged.add(mesh);
  }
  keep.forEach(mesh => merged.add(mesh));
  source.traverse(object => { if (object instanceof THREE.Mesh) object.geometry.dispose(); });
  merged.traverse(object => { if (object instanceof THREE.Mesh) { object.castShadow = true; object.receiveShadow = true; meshCount++; } });
  return merged;
}

// The ruby rig's area lights need large LTC lookup tables, so the showcase keeps
// its reflection panels, shadowed rims and contact shadow, and stands in close
// point lights with the same colour, position and approximate power.
function pointLights(group: THREE.Group, o: RubyOptions) {
  const aim = new THREE.Vector3(...o.camera.target);
  for (const lamp of o.lamps) {
    if (lamp.shadow || lamp.intensity <= 0) continue;
    const light = new THREE.PointLight(lamp.color, lamp.intensity * lamp.w * lamp.h * 1.15, 0, 2);
    const a = THREE.MathUtils.degToRad(lamp.az), e = THREE.MathUtils.degToRad(lamp.el), d = lamp.distance ?? 7;
    light.position.set(Math.cos(e) * Math.sin(a), Math.sin(e), Math.cos(e) * Math.cos(a)).multiplyScalar(d).add(aim);
    light.name = lamp.name;
    group.add(light);
  }
}

async function start() {
  const started = performance.now();
  post({ type: 'progress', phase: 'renderer' });
  quality = detectQuality();
  renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: !quality.software, powerPreference: 'default' });
  post({ type: 'progress', phase: 'model' });
  renderer.setClearColor(0x000000, 0);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  // Decided before the first compile so no shader variant is built twice.
  renderer.shadowMap.enabled = !quality.software;
  renderer.shadowMap.type = THREE.PCFShadowMap;
  scene = new THREE.Scene();

  const gltf = await new GLTFLoader().parseAsync(decodeModel(), '');
  post({ type: 'progress', phase: 'materials' });
  if (disposed) return;
  // The exported study has an illustrative LCD. Blank its sample numbers in
  // the interactive demo so they can never be mistaken for device readback.
  gltf.scene.traverse(object => {
    if (!(object instanceof THREE.Mesh)) return;
    for (const material of ([] as THREE.Material[]).concat(object.material)) {
      if (material.name !== 'Illustrative display (not live readback)') continue;
      const display = material as THREE.MeshBasicMaterial;
      display.map?.dispose(); display.map = null; display.color.set('#140B0B'); display.needsUpdate = true;
    }
  });
  stage = new THREE.Group();
  stage.name = 'G60';
  stage.scale.setScalar(1 / METRES_PER_UNIT);
  stage.add(batch(gltf.scene));
  turntable = new THREE.Group();
  turntable.add(stage);
  scene.add(turntable);

  const options: RubyOptions = { ...RUBY, lamps: RUBY.lamps.map(lamp => lamp.shadow ? lamp : { ...lamp, intensity: 0 }) };
  rig = createRubyRig(scene, renderer, stage, options);
  post({ type: 'progress', phase: 'rig' });
  camera = rig.camera;
  pointLights(scene.getObjectByName('Ruby render rig') as THREE.Group, RUBY);
  scene.traverse(object => { if (object instanceof THREE.DirectionalLight) object.shadow.mapSize.set(1024, 1024); });
  // The contact shadow belongs to the product, so it turns with the turntable.
  contact = scene.getObjectByName('Contact shadow') as THREE.Mesh | undefined;
  if (contact) turntable.attach(contact);

  // Bounding sphere centred on the turntable axis, from the actual vertices.
  scene.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(stage, true);
  fit.centerY = (box.min.y + box.max.y) / 2;
  let radius = 0;
  const point = new THREE.Vector3();
  stage.traverse(object => {
    if (!(object instanceof THREE.Mesh)) return;
    const position = object.geometry.getAttribute('position');
    for (let i = 0; i < position.count; i++) {
      point.fromBufferAttribute(position, i).applyMatrix4(object.matrixWorld);
      radius = Math.max(radius, Math.hypot(point.x, point.y - fit.centerY, point.z));
    }
  });
  fit.radius = radius || 2;

  resize();
  renderer.compile(scene, camera);
  post({ type: 'progress', phase: 'compiled' });
  ready = true;
  draw();
  document.body.classList.add('ready');
  (window as any).G60Showcase.loadMs = Math.round(performance.now() - started);
  post({ type: 'ready' });
  post({ type: 'state', spinning: spinPreference });
  requestRender();
}

(window as any).G60Showcase = { command, getState, loadMs: 0 };
if (!canvas) fail(new Error('Canvas missing'));
else start().catch(fail);
