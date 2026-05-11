import * as THREE from "https://cdn.jsdelivr.net/npm/three@0.165.0/build/three.module.js";

import { buildAvatar } from "./avatar_builder.js";
import { DEFAULT_AVATAR_PICK, getAvatarStyle } from "./avatar_styles.js";
import { loadAvatar } from "./avatar_loader.js";
import { applyPosePreset } from "./pose_presets.js";

const activeViews = new Set();
let rafId = 0;
let lastTs = 0;

function ensureTicker() {
  if (rafId || !activeViews.size) return;
  const tick = (ts) => {
    if (!activeViews.size) {
      rafId = 0;
      lastTs = 0;
      return;
    }
    const dt = lastTs ? Math.min(0.05, (ts - lastTs) / 1000) : 0.016;
    lastTs = ts;
    for (const view of activeViews) view.update(dt);
    rafId = requestAnimationFrame(tick);
  };
  rafId = requestAnimationFrame(tick);
}

function stopTickerIfIdle() {
  if (!activeViews.size && rafId) {
    cancelAnimationFrame(rafId);
    rafId = 0;
    lastTs = 0;
  }
}

export function mountAvatarCardPreview(container, {
  avatarId,
  compact = false,
  interactive = true,
} = {}) {
  const renderer = new THREE.WebGLRenderer({ antialias: !compact, alpha: true });
  renderer.setPixelRatio(Math.min(compact ? 1.0 : 1.5, window.devicePixelRatio || 1));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.02;
  renderer.domElement.className = "avatar-card-canvas";

  const scene = new THREE.Scene();
  scene.background = makeGradientTexture(compact ? 0x101722 : 0x131b28, 0x243149);

  const camera = new THREE.PerspectiveCamera(32, 1, 0.05, 30);
  const hemi = new THREE.HemisphereLight(0xe9f3ff, 0xffe2c4, 1.1);
  scene.add(hemi);
  const key = new THREE.DirectionalLight(0xfff4df, 1.15);
  key.position.set(2.5, 4.5, 3.5);
  scene.add(key);
  const rim = new THREE.DirectionalLight(0x8ebcff, 0.45);
  rim.position.set(-2.8, 2.2, -2.5);
  scene.add(rim);

  const floor = new THREE.Mesh(
    new THREE.CircleGeometry(compact ? 0.68 : 0.9, 40),
    new THREE.MeshStandardMaterial({
      color: 0x19212e,
      roughness: 0.95,
      metalness: 0.0,
      transparent: true,
      opacity: 0.88,
    }),
  );
  floor.rotation.x = -Math.PI / 2;
  floor.position.y = -0.01;
  scene.add(floor);

  const ring = new THREE.Mesh(
    new THREE.RingGeometry(compact ? 0.63 : 0.84, compact ? 0.67 : 0.88, 48),
    new THREE.MeshBasicMaterial({
      color: 0x6db7ff,
      transparent: true,
      opacity: 0.32,
      side: THREE.DoubleSide,
    }),
  );
  ring.rotation.x = -Math.PI / 2;
  ring.position.y = -0.008;
  scene.add(ring);

  const pivot = new THREE.Group();
  scene.add(pivot);

  let disposed = false;
  let modelRoot = null;
  let radius = compact ? 2.8 : 3.2;
  let yaw = compact ? -0.18 : -0.28;
  let pitch = compact ? 0.06 : 0.12;
  let dragging = false;
  let hovering = false;
  let inView = true;
  let needsFrame = true;
  let lastX = 0;
  let lastY = 0;
  let ro = null;
  let io = null;

  container.innerHTML = "";
  container.appendChild(renderer.domElement);

  function frame() {
    const width = Math.max(1, container.clientWidth || (compact ? 56 : 80));
    const height = Math.max(1, container.clientHeight || (compact ? 70 : 100));
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    const targetY = compact ? 0.98 : 1.02;
    camera.position.set(
      Math.sin(yaw) * radius,
      1.0 + pitch * 3.5,
      Math.cos(yaw) * radius,
    );
    camera.lookAt(0, targetY, 0);
    renderer.render(scene, camera);
    needsFrame = false;
  }

  function update(dt) {
    if (disposed || !inView) return;
    // Only auto-rotate while the user is actively hovering / dragging the
    // card; otherwise the card is static (one render after pose change).
    // This is what fixes the gallery-wide jank: 13 cards no longer all
    // re-render every frame just to sit still.
    if (!dragging && hovering) {
      yaw += dt * 0.35;
      needsFrame = true;
    }
    if (needsFrame) frame();
  }

  const viewHandle = { update };
  activeViews.add(viewHandle);
  ensureTicker();

  function requestFrame() { needsFrame = true; }

  function onPointerDown(ev) {
    if (!interactive) return;
    dragging = true;
    lastX = ev.clientX;
    lastY = ev.clientY;
    renderer.domElement.setPointerCapture?.(ev.pointerId);
  }

  function onPointerMove(ev) {
    if (!interactive || !dragging) return;
    const dx = ev.clientX - lastX;
    const dy = ev.clientY - lastY;
    lastX = ev.clientX;
    lastY = ev.clientY;
    yaw -= dx * 0.012;
    pitch = THREE.MathUtils.clamp(pitch - dy * 0.008, -0.08, 0.42);
    needsFrame = true;
  }

  function onPointerUp(ev) {
    dragging = false;
    renderer.domElement.releasePointerCapture?.(ev.pointerId);
  }

  function onPointerEnter() { hovering = true; needsFrame = true; }
  function onPointerLeave(ev) {
    hovering = false;
    dragging = false;
    renderer.domElement.releasePointerCapture?.(ev.pointerId);
  }

  function onWheel(ev) {
    if (!interactive) return;
    ev.preventDefault();
    radius = THREE.MathUtils.clamp(radius + Math.sign(ev.deltaY) * 0.16, compact ? 2.1 : 2.3, compact ? 3.5 : 4.2);
    needsFrame = true;
  }

  function onDblClick() {
    yaw = compact ? -0.18 : -0.28;
    pitch = compact ? 0.06 : 0.12;
    radius = compact ? 2.8 : 3.2;
    needsFrame = true;
  }

  renderer.domElement.addEventListener("pointerdown", onPointerDown);
  renderer.domElement.addEventListener("pointermove", onPointerMove);
  renderer.domElement.addEventListener("pointerup", onPointerUp);
  renderer.domElement.addEventListener("pointerenter", onPointerEnter);
  renderer.domElement.addEventListener("pointerleave", onPointerLeave);
  renderer.domElement.addEventListener("wheel", onWheel, { passive: false });
  renderer.domElement.addEventListener("dblclick", onDblClick);

  ro = new ResizeObserver(() => { needsFrame = true; });
  ro.observe(container);

  // Pause cards that have scrolled off-screen so they consume zero
  // CPU/GPU until the user scrolls them back into view.
  if (typeof IntersectionObserver !== "undefined") {
    io = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        inView = entry.isIntersecting;
        if (inView) needsFrame = true;
      }
    }, { rootMargin: "120px" });
    io.observe(container);
  }

  (async () => {
    const avatar = await buildPreviewAvatar(avatarId);
    if (disposed || !avatar) return;
    forceArmsDownPose(avatar);
    normalizeAvatar(avatar, compact);
    modelRoot = avatar;
    pivot.add(modelRoot);
    needsFrame = true;
  })();

  return {
    dispose() {
      disposed = true;
      activeViews.delete(viewHandle);
      stopTickerIfIdle();
      ro?.disconnect();
      io?.disconnect();
      renderer.domElement.removeEventListener("pointerdown", onPointerDown);
      renderer.domElement.removeEventListener("pointermove", onPointerMove);
      renderer.domElement.removeEventListener("pointerup", onPointerUp);
      renderer.domElement.removeEventListener("pointerenter", onPointerEnter);
      renderer.domElement.removeEventListener("pointerleave", onPointerLeave);
      renderer.domElement.removeEventListener("wheel", onWheel);
      renderer.domElement.removeEventListener("dblclick", onDblClick);
      if (modelRoot?.userData?.previewOwnsResources) disposeObject(modelRoot);
      pivot.remove(modelRoot);
      scene.background?.dispose?.();
      floor.geometry.dispose();
      floor.material.dispose();
      ring.geometry.dispose();
      ring.material.dispose();
      renderer.dispose();
      container.innerHTML = "";
    },
  };
}

async function buildPreviewAvatar(avatarId) {
  const rpmAvatar = await loadAvatar(avatarId);
  if (rpmAvatar) {
    rpmAvatar.userData.previewOwnsResources = false;
    return rpmAvatar;
  }
  const styleId = LEGACY_ALIAS[avatarId] || avatarId || DEFAULT_AVATAR_PICK[0];
  const fallback = buildAvatar(getAvatarStyle(styleId));
  fallback.setExpression?.("joy");
  applyPosePreset("hands_clasped", fallback.joints);
  fallback.root.userData.previewOwnsResources = true;
  return fallback.root;
}

// The Tripo-generated preset glbs ship with a Mixamo-named skeleton in
// T-pose (arms stretched horizontally) and contain NO usable animation
// (the placeholder idle_relaxed.glb only animates the root). To get a
// natural standing silhouette in the gallery we directly rotate the
// upper-arm bones in WORLD space so the result is independent of each
// bone's local axis convention.
//
// World axis convention: avatar faces -X, up is +Y. In the bind T-pose
// the LEFT arm extends along world +Z and the RIGHT arm along world -Z.
// To swing them down to world -Y we rotate around world +X axis:
//   left arm  : +80° around +X  (sends +Z → -Y, leaving 10° A-pose)
//   right arm : -80° around +X  (sends -Z → -Y)
// Verified empirically on female_youth_18.glb via scripts/_verify_pose.mjs:
// hand world-Y drops from ~0.75 to ~0.50, Z collapses to ~0.10.
function forceArmsDownPose(root) {
  if (!root || !root.traverse) return;
  const bones = {};
  root.traverse((node) => {
    if (!node.isBone) return;
    const key = (node.name || "").replace(/^mixamorig:?/i, "").toLowerCase();
    if (!bones[key]) bones[key] = node;
  });
  if (!bones.leftarm && !bones.rightarm) return; // unrecognised rig

  const tmpQ = new THREE.Quaternion();
  const parentInv = new THREE.Quaternion();
  const worldAxisX = new THREE.Vector3(1, 0, 0);

  const rotateBoneWorld = (bone, axis, angleRad) => {
    if (!bone) return;
    bone.parent?.updateWorldMatrix?.(true, false);
    bone.updateWorldMatrix(true, false);
    const worldQ = new THREE.Quaternion();
    bone.getWorldQuaternion(worldQ);
    const delta = new THREE.Quaternion().setFromAxisAngle(axis, angleRad);
    worldQ.premultiply(delta);
    if (bone.parent) {
      bone.parent.getWorldQuaternion(parentInv).invert();
      tmpQ.copy(parentInv).multiply(worldQ);
    } else {
      tmpQ.copy(worldQ);
    }
    bone.quaternion.copy(tmpQ);
    bone.updateMatrixWorld(true);
  };

  const D = Math.PI / 180;
  rotateBoneWorld(bones.leftarm, worldAxisX, 80 * D);
  rotateBoneWorld(bones.rightarm, worldAxisX, -80 * D);

  root.updateMatrixWorld(true);
}

function normalizeAvatar(root, compact) {
  // Tripo glbs export with the character facing world +X (arms spread
  // along ±Z, head's local +Z maps to world +X — verified via
  // scripts/_verify_facing.mjs). Rotate around Y by -90° so the face
  // points to world +Z (camera direction); add a small slant for a
  // 3/4-view silhouette.
  root.rotation.y = -Math.PI / 2 + (compact ? -0.08 : -0.16);
  const box = new THREE.Box3().setFromObject(root);
  const size = new THREE.Vector3();
  const center = new THREE.Vector3();
  box.getSize(size);
  box.getCenter(center);
  const targetHeight = compact ? 1.72 : 1.84;
  const scale = size.y > 0 ? targetHeight / size.y : 1;
  root.scale.multiplyScalar(scale);
  const scaledCenter = center.multiplyScalar(scale);
  root.position.x -= scaledCenter.x;
  root.position.y -= box.min.y * scale;
  root.position.z -= scaledCenter.z;
}

function disposeObject(obj) {
  if (!obj) return;
  obj.traverse?.((node) => {
    if (node.geometry) node.geometry.dispose?.();
    if (Array.isArray(node.material)) {
      node.material.forEach((mat) => {
        mat.map?.dispose?.();
        mat.dispose?.();
      });
    } else if (node.material) {
      node.material.map?.dispose?.();
      node.material.dispose?.();
    }
  });
}

function makeGradientTexture(topHex, bottomHex) {
  const c = document.createElement("canvas");
  c.width = 2;
  c.height = 256;
  const ctx = c.getContext("2d");
  const grad = ctx.createLinearGradient(0, 0, 0, 256);
  grad.addColorStop(0, hexCss(topHex));
  grad.addColorStop(1, hexCss(bottomHex));
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, 2, 256);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

function hexCss(hex) {
  return `#${hex.toString(16).padStart(6, "0")}`;
}

const LEGACY_ALIAS = {
  male_casual_25: "akira",
  male_business_35: "jun",
  male_athletic_28: "akira",
  female_casual_22: "sakura",
  female_elegant_30: "luna",
  female_artsy_25: "rena",
  female_youth_18: "yuki",
  child_boy_8: "akira",
  child_girl_8: "sakura",
};