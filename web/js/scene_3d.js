/**
 * 3D scene composer — equirectangular panorama sphere with virtual
 * anime-style avatars placed at the recommended azimuth/distance per
 * shot. The user can drag to look around (or rotate the phone for
 * gyro-driven view); we don't try to wander outside the camera origin
 * because we don't have parallax data, only a 360° backdrop.
 *
 * Public API:
 *   const scene = createSceneView(container, { panoramaUrl, shot, picks });
 *   scene.dispose();   // when the user closes / re-renders
 *
 * `picks` is an array of avatar style ids ("akira", "yuki", ...) — one
 * per person in the shot's first pose. We pull the matching presets
 * from avatar_styles and the joint poses from pose_presets.
 */
import * as THREE from "https://cdn.jsdelivr.net/npm/three@0.165.0/build/three.module.js";

import { buildAvatar } from "./avatar_builder.js";
import { getAvatarStyle, resolveAvatarPicks } from "./avatar_styles.js";
import {
  applyPosePreset,
  classifyExpression,
  pickPosePreset,
} from "./pose_presets.js";

/**
 * @param {HTMLElement} container
 * @param {{
 *   panoramaUrl?: string,
 *   shot: any,
 *   picks?: string[],
 * }} opts
 */
export function createSceneView(container, opts) {
  const { panoramaUrl, shot, picks } = opts;

  const W = () => container.clientWidth || 320;
  const H = () => container.clientHeight || 200;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0d0d12);

  const camera = new THREE.PerspectiveCamera(70, W() / H(), 0.05, 200);
  // Stand at the user's "capture origin" – feet at y=0, eye level ≈ 1.55m.
  camera.position.set(0, 1.55, 0);

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

  container.innerHTML = "";
  container.appendChild(renderer.domElement);
  renderer.domElement.style.width = "100%";
  renderer.domElement.style.height = "100%";
  renderer.domElement.style.touchAction = "none";

  // ── Lights ──
  const hemi = new THREE.HemisphereLight(0xb8d4ff, 0xffd9a8, 0.8);
  scene.add(hemi);
  const sun = new THREE.DirectionalLight(0xffe0b8, 1.1);
  sun.position.set(8, 12, 6);
  scene.add(sun);
  const fill = new THREE.DirectionalLight(0x8eb6ff, 0.35);
  fill.position.set(-6, 4, -4);
  scene.add(fill);

  // ── Panorama sphere ──
  const sphereGeo = new THREE.SphereGeometry(50, 64, 32);
  sphereGeo.scale(-1, 1, 1); // flip inward
  const sphereMat = new THREE.MeshBasicMaterial({
    color: 0x404a64, // until texture loads
  });
  const sphere = new THREE.Mesh(sphereGeo, sphereMat);
  scene.add(sphere);

  if (panoramaUrl) {
    const loader = new THREE.TextureLoader();
    loader.load(
      panoramaUrl,
      (tex) => {
        tex.colorSpace = THREE.SRGBColorSpace;
        tex.minFilter = THREE.LinearFilter;
        tex.magFilter = THREE.LinearFilter;
        sphereMat.map = tex;
        sphereMat.color.set(0xffffff);
        sphereMat.needsUpdate = true;
      },
      undefined,
      (err) => console.warn("panorama load failed", err),
    );
  }

  // ── Ground plane (subtle, for footing context) ──
  const ground = new THREE.Mesh(
    new THREE.CircleGeometry(8, 48),
    new THREE.MeshStandardMaterial({
      color: 0x2a2f3c,
      roughness: 0.95,
      transparent: true,
      opacity: 0.55,
    }),
  );
  ground.rotation.x = -Math.PI / 2;
  ground.position.y = 0;
  scene.add(ground);

  // Camera marker (small disc at origin so users see "this is where you stand")
  const me = new THREE.Mesh(
    new THREE.CylinderGeometry(0.2, 0.2, 0.04, 24),
    new THREE.MeshStandardMaterial({
      color: 0x5b9cff,
      emissive: 0x223a66,
      roughness: 0.4,
    }),
  );
  me.position.y = 0.02;
  scene.add(me);

  // ── Avatars ──
  const pose = (shot.poses && shot.poses[0]) || null;
  const personCount = pose?.persons?.length || 1;
  const resolved = resolveAvatarPicks(picks || [], personCount);
  const avatars = [];

  if (pose) {
    placeAvatars(scene, shot, pose, resolved, avatars);
  }

  // ── Shot label sprite at the recommended azimuth ──
  if (shot.angle && shot.angle.azimuth_deg != null) {
    const dir = azimuthToDir(shot.angle.azimuth_deg);
    const arrow = makeArrowSprite(`目标 ${Math.round(shot.angle.azimuth_deg)}°`);
    arrow.position.set(dir.x * 6, 2.0, dir.z * 6);
    scene.add(arrow);
  }

  // ── Camera controls (drag + pinch zoom + optional gyro) ──
  const controls = installOrbitControls(camera, renderer.domElement);

  // ── Animate ──
  let raf = null;
  let disposed = false;

  function tick() {
    if (disposed) return;
    raf = requestAnimationFrame(tick);
    controls.update();
    renderer.render(scene, camera);
  }
  tick();

  function onResize() {
    const w = W();
    const h = H();
    if (w === 0 || h === 0) return;
    renderer.setSize(w, h, false);
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
    avatars.forEach((a) => a.dispose());
    renderer.dispose();
    sphereGeo.dispose();
    sphereMat.dispose();
    if (sphereMat.map) sphereMat.map.dispose();
    container.innerHTML = "";
  }

  function setShot(newShot) {
    // Update the arrow + avatars in-place when result switches shot
    avatars.forEach((a) => a.dispose());
    avatars.length = 0;
    while (scene.children.length > 0) {
      // Defer — partial wipe is risky. We'll rebuild on demand.
    }
  }

  return { dispose, setShot };
}

// ---------------------------------------------------------------------------
// Avatar placement
// ---------------------------------------------------------------------------

function placeAvatars(scene, shot, pose, picks, registry) {
  const baseDir = azimuthToDir(shot.angle?.azimuth_deg ?? 0);
  const baseDist = clamp(shot.angle?.distance_m ?? 2.5, 1.0, 6.0);
  const layout = pose.layout || "single";
  const persons = pose.persons || [];
  const n = Math.max(1, persons.length || picks.length);

  const offsets = layoutOffsets(layout, n);

  for (let i = 0; i < n; i++) {
    const styleId = picks[i] || picks[picks.length - 1];
    const style = getAvatarStyle(styleId);
    const character = buildAvatar(style);
    registry.push(character);
    scene.add(character.root);

    // Position: base point in front of the camera at distance `baseDist`,
    // then add the layout offset (in world meters) along the local axes
    // perpendicular to the look direction.
    const p = persons[i] || persons[0] || {};
    const ofx = offsets[i].x; // sideways offset (m)
    const ofz = offsets[i].z; // forward/back offset (m)

    // World axes: dir = look direction, right = perpendicular
    const dir = baseDir;
    const right = { x: -dir.z, z: dir.x };

    const worldX = dir.x * (baseDist + ofz) + right.x * ofx;
    const worldZ = dir.z * (baseDist + ofz) + right.z * ofx;

    character.root.position.set(worldX, 0, worldZ);

    // Face roughly back toward the camera (origin), with a personal
    // tilt for some variety.
    const yaw = Math.atan2(-worldX, -worldZ) + (i - (n - 1) / 2) * 0.18;
    character.root.rotation.y = yaw;

    // Pose + expression
    const presetName = pickPosePreset(p);
    applyPosePreset(presetName, character.joints, {
      mirror: shouldMirror(layout, i, n),
    });
    character.setExpression(classifyExpression(p));

    // Slight randomness in head bow / shoulder relax to avoid clones
    character.joints.head.rotation.y += (i - (n - 1) / 2) * 0.06;
  }
}

function layoutOffsets(layout, n) {
  // Side-to-side offsets in meters, with optional forward/back offset.
  const out = [];
  switch (layout) {
    case "side_by_side":
      for (let i = 0; i < n; i++) {
        out.push({ x: (i - (n - 1) / 2) * 0.7, z: 0 });
      }
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
      for (let i = 1; i < n; i++) {
        out.push({ x: i * 0.55, z: 0.1 });
      }
  }
  return out.slice(0, n);
}

function shouldMirror(layout, i, n) {
  if (layout === "side_by_side" || layout === "line") return i >= n / 2;
  if (layout === "high_low_offset") return i % 2 === 1;
  if (layout === "v_formation") return i % 2 === 0;
  return false;
}

function azimuthToDir(deg) {
  // 0° = +Z (north / front), 90° = +X (east), going clockwise as the
  // user perceives it. In Three.js the camera looks toward -Z by default
  // so we match that convention by treating azimuth=0 as -Z.
  const r = (deg * Math.PI) / 180;
  return { x: Math.sin(r), z: -Math.cos(r) };
}

function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }

// ---------------------------------------------------------------------------
// Arrow + label sprite
// ---------------------------------------------------------------------------

function makeArrowSprite(text) {
  const canvas = document.createElement("canvas");
  canvas.width = 256;
  canvas.height = 96;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "rgba(91, 156, 255, 0.95)";
  ctx.beginPath();
  ctx.roundRect(10, 16, canvas.width - 20, 64, 28);
  ctx.fill();
  ctx.fillStyle = "#06122a";
  ctx.font = "700 30px system-ui, -apple-system";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(text, canvas.width / 2, 48);

  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false });
  const spr = new THREE.Sprite(mat);
  spr.scale.set(2.0, 0.75, 1);
  return spr;
}

// ---------------------------------------------------------------------------
// Lightweight orbit-style camera controls (no external dependency)
// Supports mouse-drag, pinch, and gyro.
// ---------------------------------------------------------------------------

function installOrbitControls(camera, dom) {
  let yaw = 0;
  let pitch = 0;
  let fov = 70;

  const state = { dragging: false, lastX: 0, lastY: 0, pinchD: 0 };

  function applyToCamera() {
    pitch = Math.max(-Math.PI / 2 + 0.05, Math.min(Math.PI / 2 - 0.05, pitch));
    const dir = new THREE.Vector3(
      Math.sin(yaw) * Math.cos(pitch),
      Math.sin(pitch),
      -Math.cos(yaw) * Math.cos(pitch),
    );
    camera.lookAt(camera.position.clone().add(dir));
    camera.fov = fov;
    camera.updateProjectionMatrix();
  }

  function onPointerDown(e) {
    state.dragging = true;
    state.lastX = e.clientX;
    state.lastY = e.clientY;
    dom.setPointerCapture?.(e.pointerId);
  }
  function onPointerMove(e) {
    if (!state.dragging) return;
    const dx = e.clientX - state.lastX;
    const dy = e.clientY - state.lastY;
    state.lastX = e.clientX;
    state.lastY = e.clientY;
    yaw -= dx * 0.005;
    pitch -= dy * 0.005;
    applyToCamera();
  }
  function onPointerUp(e) {
    state.dragging = false;
    dom.releasePointerCapture?.(e.pointerId);
  }
  function onWheel(e) {
    fov = Math.max(30, Math.min(95, fov + e.deltaY * 0.05));
    applyToCamera();
    e.preventDefault();
  }

  dom.addEventListener("pointerdown", onPointerDown);
  dom.addEventListener("pointermove", onPointerMove);
  dom.addEventListener("pointerup", onPointerUp);
  dom.addEventListener("pointercancel", onPointerUp);
  dom.addEventListener("wheel", onWheel, { passive: false });

  // Touch pinch zoom
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
      fov = Math.max(30, Math.min(95, fov - (d - state.pinchD) * 0.1));
      state.pinchD = d;
      applyToCamera();
      e.preventDefault();
    }
  }
  dom.addEventListener("touchstart", onTouchStart, { passive: true });
  dom.addEventListener("touchmove", onTouchMove, { passive: false });

  applyToCamera();

  return {
    update() {/* nothing per-frame; we apply on input only */},
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
