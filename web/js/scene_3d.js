/**
 * v7 ShotPreview3D — camera-viewpoint photo preview.
 *
 * The previous version (v6) put the camera at the "user's feet" and
 * rotated a 360° backdrop around them — a 360-environment-viewer. That
 * was the wrong mental model: users want to see *what the photo will
 * look like*, not *what's around the photographer*.
 *
 * v7 inverts the relationship:
 *   - The subject (avatar) sits at world origin.
 *   - The camera stands at the recommended (azimuth, distance, pitch)
 *     and looks back at the subject's chest.
 *   - FOV is computed from the LLM-recommended focal length (35mm
 *     equivalent), so the framing matches the parameters in the HUD.
 *   - DOF (BokehPass) blur strength is derived from `shot.camera.aperture`
 *     so f/1.4 looks creamy and f/8 looks sharp.
 *   - Background panorama is a large-radius sphere with a blur shader
 *     applied — it's context, not the focal point.
 *   - DirectionalLight orbits to the recommended `environment.sun`
 *     azimuth/altitude when present.
 *
 * Public API stays compatible:
 *   const view = createSceneView(container, { panoramaUrl, shot, picks, environment });
 *   view.dispose();
 *
 * Returns extras for the constructive overlay/HUD wiring:
 *   view.fov, view.cameraAzimuthDeg, view.aperture, view.focalMm
 */
import * as THREE from "https://cdn.jsdelivr.net/npm/three@0.165.0/build/three.module.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { BokehPass } from "three/addons/postprocessing/BokehPass.js";

import { buildAvatar } from "./avatar_builder.js";
import { getAvatarStyle, resolveAvatarPicks } from "./avatar_styles.js";
import {
  applyPosePreset,
  classifyExpression,
  pickPosePreset,
} from "./pose_presets.js";
import {
  loadAvatar,
  loadAnimationClip,
  playAnimation,
  loadAvatarManifest,
  resolveMixamoId,
} from "./avatar_loader.js";

const SUBJECT_HEIGHT = 1.65;        // average chest at 1.05m, eye at 1.55m
const SUBJECT_LOOK_AT_Y = 1.05;
const FOCAL_FRAME_HEIGHT_MM = 24;   // 24×36mm full frame; vertical FOV
const BG_RADIUS = 50;
const NEAR_PLANE = 0.05;
const FAR_PLANE = 200;

/**
 * @param {HTMLElement} container
 * @param {{
 *   panoramaUrl?: string,
 *   shot: any,
 *   picks?: string[],
 *   environment?: any,
 * }} opts
 */
export function createSceneView(container, opts) {
  const { panoramaUrl, shot, picks, environment } = opts;

  const W = () => container.clientWidth || 320;
  const H = () => container.clientHeight || 200;

  const scene = new THREE.Scene();
  // The horizon colour also drives the fog and the clear colour, so
  // wherever the camera looks there's never a pure-black void.
  const horizonHex = skyHorizonColor(environment);
  scene.background = new THREE.Color(horizonHex);
  // Subtle distance fog — pushes the backdrop sphere into a soft
  // wash so the avatar pops in the foreground.
  scene.fog = new THREE.Fog(horizonHex, 6, 35);

  // ── Camera at the recommended capture pose ──
  const focalMm = clampNumber(
    shot?.camera?.focal_length_mm,
    14, 200, 50,
  );
  const fovDeg = focalToFov(focalMm);
  const camera = new THREE.PerspectiveCamera(
    fovDeg, W() / H(), NEAR_PLANE, FAR_PLANE,
  );
  positionCameraForShot(camera, shot);

  // ── Renderer + post-processing chain ──
  const renderer = new THREE.WebGLRenderer({
    antialias: true,
    alpha: false,
    powerPreference: "high-performance",
  });
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
  renderer.setSize(W(), H(), false);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.0;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  container.innerHTML = "";
  container.appendChild(renderer.domElement);
  renderer.domElement.style.width = "100%";
  renderer.domElement.style.height = "100%";
  renderer.domElement.style.touchAction = "none";

  const composer = new EffectComposer(renderer);
  composer.setSize(W(), H());
  composer.addPass(new RenderPass(scene, camera));

  const apertureF = parseAperture(shot?.camera?.aperture);
  // Bokeh's `aperture` arg is *not* an f-number — it's a unitless
  // screen-space blur scale. We map f/1.4 → strong (0.0006) and
  // f/11 → mild (0.00005). Visual taste: f/2 was previously melting
  // the *subject* itself, not just the background, because maxblur
  // was 0.012 — far too aggressive for the small preview canvas.
  // Keep maxblur low so the subject reads sharply even when DOF is on.
  const bokehAperture = mapApertureToBokeh(apertureF);
  const bokehPass = new BokehPass(scene, camera, {
    focus: cameraDistance(shot),
    aperture: bokehAperture,
    maxblur: 0.004,
  });
  composer.addPass(bokehPass);

  // ── Lighting (sun-locked when environment.sun present) ──
  const lights = installLights(scene, environment);

  // ── 360 backdrop sphere (context only — the camera looks INWARD
  //    so we put it far away, with a softening blur via emissive
  //    intensity not screen-space). ──
  const bg = installBackdrop(scene, panoramaUrl, environment);

  // ── Subject avatar(s) at world origin ──
  const pose = (shot.poses && shot.poses[0]) || null;
  const personCount = pose?.persons?.length || 1;
  const legacyPicks = resolveAvatarPicks(picks || [], personCount);
  const avatarRegistry = [];
  const animationMixers = [];
  if (pose) {
    placeSubjects(scene, shot, pose, picks || [], legacyPicks, avatarRegistry, animationMixers);
  }

  // Subtle ground shadow disc — gives the avatar a footing reference.
  const ground = installGround(scene);

  // ── Lightweight orbit-style adjustment so users can nudge the
  //    preview by 10–15° if they don't like the AI default.
  //    Disabled by default — most users want the AI's exact framing. ──
  const controls = installSubjectOrbitControls(camera, renderer.domElement, shot);

  // ── Render loop ──
  let raf = null;
  let disposed = false;
  let lastT = performance.now();
  function tick() {
    if (disposed) return;
    raf = requestAnimationFrame(tick);
    const now = performance.now();
    const dt = Math.min(0.05, (now - lastT) / 1000);
    lastT = now;
    animationMixers.forEach((m) => m.update(dt));
    controls.update();
    composer.render();
  }
  tick();

  function onResize() {
    const w = W(), h = H();
    if (w === 0 || h === 0) return;
    renderer.setSize(w, h, false);
    composer.setSize(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  const ro = new ResizeObserver(onResize);
  ro.observe(container);

  function dispose() {
    disposed = true;
    if (raf) cancelAnimationFrame(raf);
    ro.disconnect();
    controls.dispose();
    avatarRegistry.forEach((a) => a.dispose && a.dispose());
    animationMixers.length = 0;
    lights.dispose();
    bg.dispose();
    ground.dispose();
    composer.dispose && composer.dispose();
    renderer.dispose();
    container.innerHTML = "";
  }

  return {
    dispose,
    fov: fovDeg,
    focalMm,
    aperture: apertureF,
    cameraAzimuthDeg: shot?.angle?.azimuth_deg ?? 0,
    cameraDistanceM: shot?.angle?.distance_m ?? cameraDistance(shot),
    cameraPitchDeg: shot?.angle?.pitch_deg ?? 0,
  };
}

// ---------------------------------------------------------------------------
// Camera positioning
// ---------------------------------------------------------------------------

function positionCameraForShot(camera, shot) {
  const az = ((shot?.angle?.azimuth_deg ?? 0) * Math.PI) / 180;
  const r = clampNumber(shot?.angle?.distance_m, 0.6, 8.0, 2.5);
  const pitchDeg = shot?.angle?.pitch_deg ?? 0;
  const eyeY = SUBJECT_LOOK_AT_Y + Math.tan((pitchDeg * Math.PI) / 180) * r;
  // Camera at distance r along azimuth. We use Three's convention:
  //   azimuth=0 → +Z (front of subject), 90° → +X (subject's left)
  camera.position.set(
    Math.sin(az) * r,
    eyeY,
    Math.cos(az) * r,
  );
  camera.lookAt(0, SUBJECT_LOOK_AT_Y, 0);
}

function cameraDistance(shot) {
  return clampNumber(shot?.angle?.distance_m, 0.6, 8.0, 2.5);
}

function focalToFov(focalMm) {
  // Vertical FOV from focal length on a 24mm-tall sensor (full frame).
  const rad = 2 * Math.atan(FOCAL_FRAME_HEIGHT_MM / 2 / focalMm);
  return Math.max(8, Math.min(120, (rad * 180) / Math.PI));
}

function parseAperture(input) {
  if (typeof input === "number" && Number.isFinite(input)) return input;
  if (typeof input !== "string") return 2.8;
  const m = input.match(/f\/?\s*([0-9]+\.?[0-9]*)/i);
  if (m) {
    const v = parseFloat(m[1]);
    if (Number.isFinite(v) && v > 0) return v;
  }
  return 2.8;
}

function mapApertureToBokeh(fNum) {
  // f/1.4 → noticeable but not insane; f/11+ → effectively pin-sharp.
  // Lower endpoints than v7-initial because the small preview canvas
  // amplifies blur visually — what looks gentle in a full-screen
  // viewport melts the subject in a 300×190 hero-stage card.
  const minF = 1.4;
  const maxF = 11.0;
  const clamped = Math.max(minF, Math.min(maxF, fNum));
  const t = (maxF - clamped) / (maxF - minF); // 1 at f/1.4, 0 at f/11
  return 0.00005 + 0.00055 * Math.pow(t, 1.4);
}

// ---------------------------------------------------------------------------
// Lighting — locks to environment.sun when present
// ---------------------------------------------------------------------------

function installLights(scene, environment) {
  const hemi = new THREE.HemisphereLight(0xb8d4ff, 0xffd9a8, 0.55);
  scene.add(hemi);

  const sun = new THREE.DirectionalLight(0xffe6b8, 1.45);
  sun.castShadow = true;
  sun.shadow.mapSize.set(1024, 1024);
  sun.shadow.camera.near = 0.5;
  sun.shadow.camera.far = 12;
  sun.shadow.camera.left = -4;
  sun.shadow.camera.right = 4;
  sun.shadow.camera.top = 4;
  sun.shadow.camera.bottom = -4;
  scene.add(sun);
  scene.add(sun.target);

  const fill = new THREE.DirectionalLight(0x9ec0ff, 0.32);
  scene.add(fill);

  applySunOrientation(sun, fill, environment);

  return {
    dispose() {
      scene.remove(hemi);
      scene.remove(sun);
      scene.remove(sun.target);
      scene.remove(fill);
      hemi.dispose && hemi.dispose();
    },
  };
}

function applySunOrientation(sun, fill, environment) {
  const sunInfo = environment?.sun;
  let az = 45, alt = 55;
  if (sunInfo && Number.isFinite(sunInfo.azimuth_deg) &&
      Number.isFinite(sunInfo.altitude_deg) && sunInfo.altitude_deg > -3) {
    az = sunInfo.azimuth_deg;
    alt = sunInfo.altitude_deg;
  } else if (environment?.visionLight?.directionDeg != null) {
    az = environment.visionLight.directionDeg;
    alt = environment.visionLight.elevationDeg ?? 35;
  }
  const r = 8;
  const azR = (az * Math.PI) / 180;
  const altR = Math.max(2, alt) * Math.PI / 180;
  sun.position.set(
    Math.sin(azR) * Math.cos(altR) * r,
    Math.sin(altR) * r,
    Math.cos(azR) * Math.cos(altR) * r,
  );
  sun.target.position.set(0, SUBJECT_LOOK_AT_Y, 0);
  // Fill light from the opposite side
  fill.position.set(-sun.position.x * 0.6, sun.position.y * 0.4, -sun.position.z * 0.6);
  // Warmer at low sun, cooler at high sun
  const warm = alt < 15 ? 0xffb872 : (alt < 40 ? 0xffd6a0 : 0xfff0d8);
  sun.color.setHex(warm);
}

// ---------------------------------------------------------------------------
// Backdrop sphere
// ---------------------------------------------------------------------------

function installBackdrop(scene, panoramaUrl, environment) {
  // Always mount an inside-out sky sphere with a procedural vertical
  // gradient so we get a proper sky → horizon → fog blend. If a real
  // panorama URL is supplied it overrides the gradient.
  const geo = new THREE.SphereGeometry(BG_RADIUS, 64, 32);
  geo.scale(-1, 1, 1);
  const tex = makeSkyTexture(environment);
  const mat = new THREE.MeshBasicMaterial({
    map: tex,
    fog: true,
    side: THREE.BackSide,
    depthWrite: false,
  });
  const sphere = new THREE.Mesh(geo, mat);
  scene.add(sphere);

  if (panoramaUrl) {
    const loader = new THREE.TextureLoader();
    loader.load(
      panoramaUrl,
      (panorama) => {
        panorama.colorSpace = THREE.SRGBColorSpace;
        panorama.minFilter = THREE.LinearFilter;
        panorama.magFilter = THREE.LinearFilter;
        if (mat.map) mat.map.dispose();
        mat.map = panorama;
        mat.color.set(0xd4d4d4);
        mat.needsUpdate = true;
      },
      undefined,
      (err) => console.debug("[scene_3d] panorama load failed:", err),
    );
  }

  return {
    dispose() {
      scene.remove(sphere);
      geo.dispose();
      mat.dispose();
      if (mat.map) mat.map.dispose();
    },
  };
}

/**
 * Procedural sky gradient. Returns a CanvasTexture with a vertical
 * gradient: zenith → mid-tone → horizon. Horizon hue is modulated by
 * environment.sun.altitude_deg so golden-hour shots get a warm fade.
 */
function makeSkyTexture(environment) {
  const c = document.createElement("canvas");
  c.width = 16; c.height = 256; // 1D-ish vertical gradient
  const ctx = c.getContext("2d");
  const horizon = skyHorizonColor(environment);
  const zenith = skyZenithColor(environment);
  const grad = ctx.createLinearGradient(0, 0, 0, 256);
  grad.addColorStop(0, hexCss(zenith));
  grad.addColorStop(0.55, hexCss(blendHex(zenith, horizon, 0.55)));
  grad.addColorStop(1, hexCss(horizon));
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, 16, 256);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.minFilter = THREE.LinearFilter;
  tex.magFilter = THREE.LinearFilter;
  return tex;
}

function skyHorizonColor(environment) {
  const alt = environment?.sun?.altitude_deg ?? 35;
  if (alt < 10) return 0x4a3520;       // dusk warm
  if (alt < 25) return 0x6a4a30;       // golden hour
  if (alt < 50) return 0x6e7a8c;       // mid-day haze
  return 0x4d6b88;                      // overhead sun
}

function skyZenithColor(environment) {
  const alt = environment?.sun?.altitude_deg ?? 35;
  if (alt < 10) return 0x141a26;
  if (alt < 25) return 0x1f2638;
  if (alt < 50) return 0x2c3a5a;
  return 0x365282;
}

function hexCss(hex) {
  return "#" + hex.toString(16).padStart(6, "0");
}
function blendHex(a, b, t) {
  const ar = (a >> 16) & 0xff, ag = (a >> 8) & 0xff, ab = a & 0xff;
  const br = (b >> 16) & 0xff, bg = (b >> 8) & 0xff, bb = b & 0xff;
  const r = Math.round(ar + (br - ar) * t);
  const g = Math.round(ag + (bg - ag) * t);
  const b2 = Math.round(ab + (bb - ab) * t);
  return (r << 16) | (g << 8) | b2;
}

// ---------------------------------------------------------------------------
// Ground plane (subtle, for footing context + shadow catching)
// ---------------------------------------------------------------------------

function installGround(scene) {
  // Layer 1: large ground disc — receives the sun's directional shadow.
  const geo = new THREE.CircleGeometry(8, 64);
  const mat = new THREE.MeshStandardMaterial({
    color: 0x2a2f3d,
    roughness: 0.92,
    metalness: 0.0,
  });
  const m = new THREE.Mesh(geo, mat);
  m.rotation.x = -Math.PI / 2;
  m.position.y = 0;
  m.receiveShadow = true;
  scene.add(m);

  // Layer 2: soft-edged contact shadow blob directly under the subject
  // — gives a clear visual anchor that the avatar isn't levitating.
  // Implemented as a radial-gradient canvas texture on a small disc;
  // works even if shadow mapping is disabled / weak on a given GPU.
  const blob = makeContactShadow();
  blob.position.y = 0.005; // avoid z-fighting with ground
  scene.add(blob);

  return {
    dispose() {
      scene.remove(m);
      scene.remove(blob);
      geo.dispose();
      mat.dispose();
      if (blob.material) blob.material.dispose();
      if (blob.geometry) blob.geometry.dispose();
      if (blob.material?.map) blob.material.map.dispose();
    },
  };
}

function makeContactShadow() {
  const c = document.createElement("canvas");
  c.width = 256; c.height = 256;
  const ctx = c.getContext("2d");
  const grad = ctx.createRadialGradient(128, 128, 8, 128, 128, 128);
  grad.addColorStop(0.0, "rgba(0,0,0,0.65)");
  grad.addColorStop(0.5, "rgba(0,0,0,0.32)");
  grad.addColorStop(1.0, "rgba(0,0,0,0.0)");
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, 256, 256);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  const geo = new THREE.PlaneGeometry(1.6, 1.6);
  const mat = new THREE.MeshBasicMaterial({
    map: tex, transparent: true, depthWrite: false, fog: false,
  });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.rotation.x = -Math.PI / 2;
  return mesh;
}

// ---------------------------------------------------------------------------
// Subjects — placed at origin (camera looks at them)
// ---------------------------------------------------------------------------

function placeSubjects(scene, shot, pose, picksRPM, picksLegacy, registry, mixers) {
  const persons = pose.persons || [];
  const n = Math.max(1, persons.length || picksLegacy.length);

  // Layout offsets are in *meters around the origin* (subject-local):
  // person 0 sits at (0,0,0) (or close), the rest spread sideways.
  const offsets = layoutOffsets(pose.layout || "single", n);

  const pc = personCountFromPose(pose);

  for (let i = 0; i < n; i++) {
    const p = persons[i] || persons[0] || {};
    const ofx = offsets[i].x;
    const ofz = offsets[i].z;

    // Try the high-quality glb pipeline first; fall back to procedural
    // mesh if assets aren't bundled yet.
    const placeholder = buildPlaceholder(picksLegacy[i] || picksLegacy[picksLegacy.length - 1]);
    placeholder.root.position.set(ofx, 0, ofz);
    placeholder.root.lookAt(getCameraTargetFromShot(shot, ofx, ofz));
    placeholder.root.castShadow = true;
    placeholder.root.traverse?.((c) => { c.castShadow = true; });
    scene.add(placeholder.root);
    registry.push(placeholder);

    // Pose + expression on placeholder
    applyPosePreset(pickPosePreset(p), placeholder.joints, {
      mirror: shouldMirror(pose.layout, i, n),
    });
    placeholder.setExpression?.(classifyExpression(p));
    placeholder.joints.head.rotation.y += (i - (n - 1) / 2) * 0.06;

    // Async upgrade — when the glb arrives, swap it in.
    upgradeToRPM({
      scene,
      registry,
      mixers,
      placeholder,
      personIndex: i,
      pose,
      personCount: pc,
      preferredPresetId: picksRPM[i],
    });
  }
}

function personCountFromPose(pose) {
  if (pose?.persons?.length) return pose.persons.length;
  return 1;
}

function buildPlaceholder(styleId) {
  const style = getAvatarStyle(styleId);
  return buildAvatar(style);
}

async function upgradeToRPM({
  scene, registry, mixers, placeholder,
  personIndex, pose, personCount, preferredPresetId,
}) {
  try {
    const manifest = await loadAvatarManifest();
    const presetId = preferredPresetId || pickPresetForPerson(manifest.presets, personIndex);
    if (!presetId) {
      console.info("[scene_3d] no preset available, keeping procedural placeholder");
      return;
    }
    const avatar = await loadAvatar(presetId);
    if (!avatar) {
      console.info(`[scene_3d] glb not bundled for ${presetId}, keeping procedural placeholder`);
      return;
    }
    avatar.position.copy(placeholder.root.position);
    avatar.rotation.copy(placeholder.root.rotation);
    avatar.traverse((c) => { c.castShadow = true; });
    scene.add(avatar);
    scene.remove(placeholder.root);
    placeholder.dispose && placeholder.dispose();
    // Replace registry entry so dispose() finds the upgraded mesh.
    const idx = registry.indexOf(placeholder);
    if (idx >= 0) registry[idx] = { dispose: () => scene.remove(avatar) };
    console.info(`[scene_3d] upgraded to RPM avatar: ${presetId}`);

    // Bind Mixamo animation
    const poseId = pose?.id || pose?.reference_thumbnail_id;
    const mixamoId = resolveMixamoId(poseId, personCount, manifest);
    const clip = await loadAnimationClip(mixamoId);
    const ctrl = playAnimation(avatar, clip);
    if (ctrl) {
      mixers.push(ctrl.mixer);
      console.info(`[scene_3d] bound mixamo animation: ${mixamoId}`);
    }
  } catch (err) {
    console.warn("[scene_3d] RPM upgrade failed:", err?.message || err, err);
  }
}

function pickPresetForPerson(presets, idx) {
  if (!presets || !presets.length) return null;
  const order = ["female_youth_18", "male_casual_25",
                 "female_casual_22",
                 "female_elegant_30", "child_girl_8"];
  const found = order.filter((id) => presets.some((p) => p.id === id));
  return found[idx % found.length] || presets[0].id;
}

function getCameraTargetFromShot(shot, fromX, fromZ) {
  // Approximate the camera location for "subject faces camera"
  const az = ((shot?.angle?.azimuth_deg ?? 0) * Math.PI) / 180;
  const r = clampNumber(shot?.angle?.distance_m, 0.6, 8.0, 2.5);
  return new THREE.Vector3(
    Math.sin(az) * r - fromX * 0.0,  // keep relative aim simple
    SUBJECT_LOOK_AT_Y,
    Math.cos(az) * r - fromZ * 0.0,
  );
}

// ---------------------------------------------------------------------------
// Layout offsets — same heuristics as v6, kept for consistency
// ---------------------------------------------------------------------------

function layoutOffsets(layout, n) {
  const out = [];
  switch (layout) {
    case "side_by_side":
      for (let i = 0; i < n; i++) out.push({ x: (i - (n - 1) / 2) * 0.7, z: 0 });
      break;
    case "high_low_offset":
      for (let i = 0; i < n; i++) {
        const sgn = i % 2 === 0 ? -1 : 1;
        out.push({ x: sgn * 0.45, z: i % 2 === 0 ? 0 : 0.25 });
      }
      break;
    case "triangle":
      out.push({ x: 0, z: -0.25 });
      if (n >= 2) out.push({ x: -0.7, z: 0.25 });
      if (n >= 3) out.push({ x: 0.7, z: 0.25 });
      if (n >= 4) out.push({ x: 0, z: 0.55 });
      break;
    case "diagonal":
      for (let i = 0; i < n; i++) {
        const t = (i - (n - 1) / 2);
        out.push({ x: t * 0.55, z: t * 0.3 });
      }
      break;
    case "v_formation":
      out.push({ x: 0, z: -0.3 });
      for (let i = 1; i < n; i++) {
        const sgn = i % 2 === 0 ? -1 : 1;
        out.push({ x: sgn * (0.4 + Math.floor(i / 2) * 0.4), z: i * 0.18 });
      }
      break;
    case "circle":
      for (let i = 0; i < n; i++) {
        const a = (i / n) * Math.PI * 2;
        out.push({ x: Math.sin(a) * 0.7, z: Math.cos(a) * 0.7 });
      }
      break;
    case "line":
      for (let i = 0; i < n; i++) {
        out.push({ x: (i - (n - 1) / 2) * 0.55, z: (i % 2) * 0.12 });
      }
      break;
    case "cluster":
      out.push({ x: 0, z: 0 });
      if (n >= 2) out.push({ x: -0.55, z: 0.18 });
      if (n >= 3) out.push({ x: 0.55, z: 0.22 });
      if (n >= 4) out.push({ x: 0, z: 0.5 });
      break;
    case "single":
    default:
      out.push({ x: 0, z: 0 });
      for (let i = 1; i < n; i++) out.push({ x: i * 0.55, z: 0.1 });
  }
  return out.slice(0, n);
}

function shouldMirror(layout, i, n) {
  if (layout === "side_by_side" || layout === "line") return i >= n / 2;
  if (layout === "high_low_offset") return i % 2 === 1;
  if (layout === "v_formation") return i % 2 === 0;
  return false;
}

// ---------------------------------------------------------------------------
// Subject-locked orbit controls — small drag delta around the AI default
// ---------------------------------------------------------------------------

function installSubjectOrbitControls(camera, dom, shot) {
  // Stash the AI-default camera position so we can restore it.
  const target = new THREE.Vector3(0, SUBJECT_LOOK_AT_Y, 0);
  let azimuth = (shot?.angle?.azimuth_deg ?? 0) * Math.PI / 180;
  let elevation = (shot?.angle?.pitch_deg ?? 0) * Math.PI / 180;
  let radius = clampNumber(shot?.angle?.distance_m, 0.6, 8.0, 2.5);

  const state = { dragging: false, lastX: 0, lastY: 0, pinchD: 0 };

  function applyCamera() {
    const eyeY = target.y + Math.sin(elevation) * radius;
    const horizontal = Math.cos(elevation) * radius;
    camera.position.set(
      target.x + Math.sin(azimuth) * horizontal,
      eyeY,
      target.z + Math.cos(azimuth) * horizontal,
    );
    camera.lookAt(target);
  }
  applyCamera();

  function onPointerDown(e) {
    state.dragging = true;
    state.lastX = e.clientX; state.lastY = e.clientY;
    dom.setPointerCapture?.(e.pointerId);
  }
  function onPointerMove(e) {
    if (!state.dragging) return;
    const dx = e.clientX - state.lastX;
    const dy = e.clientY - state.lastY;
    state.lastX = e.clientX; state.lastY = e.clientY;
    azimuth -= dx * 0.005;
    elevation = Math.max(-Math.PI / 2 + 0.05, Math.min(Math.PI / 2 - 0.05, elevation + dy * 0.005));
    applyCamera();
  }
  function onPointerUp(e) {
    state.dragging = false;
    dom.releasePointerCapture?.(e.pointerId);
  }
  function onWheel(e) {
    radius = Math.max(0.5, Math.min(8, radius + e.deltaY * 0.0015));
    applyCamera();
    e.preventDefault();
  }

  dom.addEventListener("pointerdown", onPointerDown);
  dom.addEventListener("pointermove", onPointerMove);
  dom.addEventListener("pointerup", onPointerUp);
  dom.addEventListener("pointercancel", onPointerUp);
  dom.addEventListener("wheel", onWheel, { passive: false });

  function getTouchDist(e) {
    const a = e.touches[0]; const b = e.touches[1];
    return Math.hypot(b.clientX - a.clientX, b.clientY - a.clientY);
  }
  function onTouchStart(e) {
    if (e.touches.length === 2) state.pinchD = getTouchDist(e);
  }
  function onTouchMove(e) {
    if (e.touches.length === 2 && state.pinchD > 0) {
      const d = getTouchDist(e);
      radius = Math.max(0.5, Math.min(8, radius - (d - state.pinchD) * 0.005));
      state.pinchD = d;
      applyCamera();
      e.preventDefault();
    }
  }
  dom.addEventListener("touchstart", onTouchStart, { passive: true });
  dom.addEventListener("touchmove", onTouchMove, { passive: false });

  return {
    update() { /* no per-frame work */ },
    dispose() {
      dom.removeEventListener("pointerdown", onPointerDown);
      dom.removeEventListener("pointermove", onPointerMove);
      dom.removeEventListener("pointerup", onPointerUp);
      dom.removeEventListener("pointercancel", onPointerUp);
      dom.removeEventListener("wheel", onWheel);
      dom.removeEventListener("touchstart", onTouchStart);
      dom.removeEventListener("touchmove", onTouchMove);
    },
  };
}

// ---------------------------------------------------------------------------
// Numeric helpers
// ---------------------------------------------------------------------------

function clampNumber(v, lo, hi, fallback) {
  if (!Number.isFinite(v)) return fallback;
  return Math.max(lo, Math.min(hi, v));
}
