/**
 * Compact 3D avatar pose preview — drops a Three.js stage into a small
 * container and shows the user's selected avatars doing the AI's
 * recommended pose. Used by the guide page (in the "姿势示意" card) and
 * potentially anywhere else we want a "see exactly how I should stand"
 * mini view without committing to a full panorama hero.
 *
 * Reuses avatar_builder + pose_presets so it's consistent with the
 * full scene_3d view.
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
 * @param {HTMLElement} container — must have a width; height auto-fills via aspect ratio
 * @param {{
 *   pose: any,
 *   picks?: string[],
 * }} opts
 */
export function createAvatarPosePreview(container, opts) {
  const pose = opts.pose;
  if (!pose) {
    container.innerHTML = "<div class='avatar-preview-empty'>暂无姿势数据</div>";
    return { dispose() {} };
  }

  const persons = pose.persons || [];
  const n = Math.max(1, persons.length);
  const picks = resolveAvatarPicks(opts.picks || [], n);

  const W = () => container.clientWidth || 320;
  const H = () => Math.round(W() * 0.6);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x141822);

  // Subtle gradient sky-ish background (gradient texture)
  scene.background = makeGradientTexture(0x141822, 0x252b3c);

  const camera = new THREE.PerspectiveCamera(34, W() / H(), 0.05, 30);
  camera.position.set(0, 1.45, 3.5);
  camera.lookAt(0, 1.0, 0);

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
  renderer.setSize(W(), H(), false);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  container.innerHTML = "";
  container.appendChild(renderer.domElement);
  renderer.domElement.style.width = "100%";
  renderer.domElement.style.height = "100%";
  renderer.domElement.style.display = "block";

  // Lighting
  scene.add(new THREE.HemisphereLight(0xddeeff, 0xfff0d0, 1.0));
  const key = new THREE.DirectionalLight(0xfff1d6, 0.9);
  key.position.set(2, 4, 3);
  scene.add(key);

  // Floor
  const floor = new THREE.Mesh(
    new THREE.CircleGeometry(2.6, 32),
    new THREE.MeshStandardMaterial({
      color: 0x1c2030, roughness: 0.95,
    }),
  );
  floor.rotation.x = -Math.PI / 2;
  scene.add(floor);
  const ring = new THREE.Mesh(
    new THREE.RingGeometry(2.5, 2.55, 64),
    new THREE.MeshBasicMaterial({ color: 0x5b9cff, transparent: true, opacity: 0.6 }),
  );
  ring.rotation.x = -Math.PI / 2;
  ring.position.y = 0.001;
  scene.add(ring);

  // Avatars
  const avatars = [];
  const layout = pose.layout || "single";
  const offsets = compactLayout(layout, n);
  for (let i = 0; i < n; i++) {
    const style = getAvatarStyle(picks[i]);
    const av = buildAvatar(style);
    const p = persons[i] || persons[0] || {};
    av.root.position.set(offsets[i].x, 0, offsets[i].z);
    av.root.rotation.y = -offsets[i].x * 0.4 + (i - (n - 1) / 2) * 0.1;
    applyPosePreset(pickPosePreset(p), av.joints, {
      mirror: i % 2 === 1 && layout !== "single",
    });
    av.setExpression(classifyExpression(p));
    scene.add(av.root);
    avatars.push(av);
  }

  // Slow auto-rotation — gives the user a 360 sense without controls.
  let yaw = 0;
  let raf = null;
  let disposed = false;

  function tick() {
    if (disposed) return;
    raf = requestAnimationFrame(tick);
    yaw += 0.0035;
    const r = 3.5;
    camera.position.x = Math.sin(yaw) * r;
    camera.position.z = Math.cos(yaw) * r;
    camera.lookAt(0, 1.0, 0);
    renderer.render(scene, camera);
  }
  tick();

  // Re-size on container changes
  function onResize() {
    const w = W(), h = H();
    if (w === 0) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  const ro = new ResizeObserver(onResize);
  ro.observe(container);

  // Optional: pause on tab hidden
  const onVis = () => {
    if (document.hidden) {
      if (raf) cancelAnimationFrame(raf);
      raf = null;
    } else if (!disposed && !raf) {
      tick();
    }
  };
  document.addEventListener("visibilitychange", onVis);

  function dispose() {
    disposed = true;
    if (raf) cancelAnimationFrame(raf);
    document.removeEventListener("visibilitychange", onVis);
    ro.disconnect();
    avatars.forEach((a) => a.dispose());
    renderer.dispose();
    container.innerHTML = "";
  }

  return { dispose };
}

function compactLayout(layout, n) {
  const out = [];
  switch (layout) {
    case "side_by_side":
    case "line":
      for (let i = 0; i < n; i++) out.push({ x: (i - (n - 1) / 2) * 0.55, z: 0 });
      break;
    case "high_low_offset":
      out.push({ x: -0.32, z: 0 });
      if (n >= 2) out.push({ x: 0.32, z: 0.18 });
      for (let i = 2; i < n; i++)
        out.push({ x: (i - 2) * 0.5 - 0.5, z: 0.36 });
      break;
    case "triangle":
      out.push({ x: 0, z: -0.1 });
      if (n >= 2) out.push({ x: -0.5, z: 0.3 });
      if (n >= 3) out.push({ x: 0.5, z: 0.3 });
      if (n >= 4) out.push({ x: 0, z: 0.55 });
      break;
    case "diagonal":
      for (let i = 0; i < n; i++) {
        const t = i - (n - 1) / 2;
        out.push({ x: t * 0.45, z: t * 0.25 });
      }
      break;
    case "v_formation":
      out.push({ x: 0, z: -0.2 });
      for (let i = 1; i < n; i++) {
        const sgn = i % 2 === 0 ? -1 : 1;
        out.push({ x: sgn * (0.3 + Math.floor(i / 2) * 0.3), z: i * 0.15 });
      }
      break;
    case "circle":
      for (let i = 0; i < n; i++) {
        const a = (i / n) * Math.PI * 2;
        out.push({ x: Math.sin(a) * 0.55, z: Math.cos(a) * 0.55 });
      }
      break;
    case "cluster":
      out.push({ x: 0, z: 0 });
      if (n >= 2) out.push({ x: -0.4, z: 0.18 });
      if (n >= 3) out.push({ x: 0.4, z: 0.22 });
      if (n >= 4) out.push({ x: 0, z: 0.42 });
      break;
    case "single":
    default:
      out.push({ x: 0, z: 0 });
      for (let i = 1; i < n; i++) out.push({ x: i * 0.45, z: 0.1 });
  }
  return out.slice(0, n);
}

function makeGradientTexture(topHex, bottomHex) {
  const c = document.createElement("canvas");
  c.width = 2; c.height = 256;
  const ctx = c.getContext("2d");
  const g = ctx.createLinearGradient(0, 0, 0, 256);
  g.addColorStop(0, "#" + topHex.toString(16).padStart(6, "0"));
  g.addColorStop(1, "#" + bottomHex.toString(16).padStart(6, "0"));
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 2, 256);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}
