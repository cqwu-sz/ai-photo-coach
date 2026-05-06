/**
 * Procedural anime-style avatar builder, based on Three.js primitives.
 *
 * Why no VRM? Pulling a VRM file requires the user to either author one
 * in VRoid Studio or download a 3rd-party model with murky licensing.
 * Here we generate everything on the client at runtime: 0 external
 * assets, 0 license risk, and we get full programmatic control over
 * the skeleton so AI rationale → pose mapping is straightforward.
 *
 * Skeleton layout (nested THREE.Group's, no SkinnedMesh — we don't need
 * vertex blending for this stylised character; rotating the joint
 * groups deforms the whole limb):
 *
 *   root
 *   └── pelvis
 *       ├── torso
 *       │   ├── neck → head (with face / hair / expression)
 *       │   ├── leftShoulder → leftArm → leftElbow → leftForearm → leftHand
 *       │   └── rightShoulder → rightArm → rightElbow → rightForearm → rightHand
 *       ├── leftHip → leftThigh → leftKnee → leftCalf → leftFoot
 *       └── rightHip → rightThigh → rightKnee → rightCalf → rightFoot
 *
 * The exported function returns the root group plus a `joints` map so
 * pose_presets.js can address each joint by name.
 *
 * Heights are in meters (Three.js convention: 1 unit = 1 meter).
 * Reference female adult: 1.65m total, head ≈ 1:7 body, anime stylised
 * eyes (slightly larger than realistic).
 */
import * as THREE from "https://cdn.jsdelivr.net/npm/three@0.165.0/build/three.module.js";

import { renderExpressionTexture } from "./expression_system.js";

/**
 * @typedef {Object} AvatarStyle
 * @property {string} id
 * @property {"male"|"female"} gender
 * @property {string} name
 * @property {number} height                  // total height in meters
 * @property {number} skinHue                 // 0..360
 * @property {number} skinLightness           // 0..1
 * @property {string} hairColor               // hex
 * @property {"short"|"buzz"|"bob"|"long_straight"|"twin_tails"|"long_curly"|"side_swept"|"wolf_tail"} hair
 * @property {string} topColor
 * @property {string} bottomColor
 * @property {"short_sleeve"|"jacket"|"dress"|"hoodie"|"sweater"} top
 * @property {"pants"|"shorts"|"skirt"|"long_skirt"|"jeans"} bottom
 * @property {string} [accessoryColor]        // glasses / hairband
 * @property {"none"|"glasses"|"hairband"|"earrings"} [accessory]
 * @property {string} [shoeColor]
 */

/**
 * Build a full avatar.
 *
 * @param {AvatarStyle} style
 * @returns {{ root: THREE.Group, joints: Record<string, THREE.Group>,
 *   setExpression: (name: string) => void, dispose: () => void }}
 */
export function buildAvatar(style) {
  const totalH = style.height ?? (style.gender === "male" ? 1.78 : 1.65);

  // Body proportion (in fractions of total height)
  const headH = totalH * 0.13;
  const neckH = totalH * 0.025;
  const torsoH = totalH * 0.32;
  const upperArmH = totalH * 0.18;
  const forearmH = totalH * 0.16;
  const handH = totalH * 0.045;
  const thighH = totalH * 0.245;
  const calfH = totalH * 0.245;
  const footL = totalH * 0.06;

  // Widths
  const torsoW = totalH * (style.gender === "male" ? 0.16 : 0.14);
  const torsoD = totalH * 0.085;
  const hipW = totalH * (style.gender === "male" ? 0.13 : 0.12);
  const armR = totalH * 0.025;
  const legR = totalH * 0.035;

  const skin = skinColor(style);
  const hairC = new THREE.Color(style.hairColor);
  const topC = new THREE.Color(style.topColor);
  const botC = new THREE.Color(style.bottomColor);
  const shoeC = new THREE.Color(style.shoeColor || "#222");

  const skinMat = new THREE.MeshStandardMaterial({
    color: skin, roughness: 0.78, metalness: 0.0,
  });
  const hairMat = new THREE.MeshStandardMaterial({
    color: hairC, roughness: 0.55, metalness: 0.05,
  });
  const topMat = new THREE.MeshStandardMaterial({
    color: topC, roughness: 0.85, metalness: 0.0,
  });
  const bottomMat = new THREE.MeshStandardMaterial({
    color: botC, roughness: 0.85, metalness: 0.0,
  });
  const shoeMat = new THREE.MeshStandardMaterial({
    color: shoeC, roughness: 0.65, metalness: 0.05,
  });

  /** @type {Record<string, THREE.Group>} */
  const joints = {};

  const root = new THREE.Group();
  root.name = "avatarRoot";

  // Place feet on the ground (y=0 at floor, hips up)
  const pelvis = new THREE.Group();
  pelvis.name = "pelvis";
  pelvis.position.y = footL * 0 + thighH + calfH; // hip height
  root.add(pelvis);
  joints.pelvis = pelvis;

  // Hip block (decorative; the pelvis Group is the actual driver)
  const hipMesh = new THREE.Mesh(
    new THREE.BoxGeometry(hipW * 1.05, totalH * 0.06, torsoD),
    bottomMat,
  );
  hipMesh.position.y = -totalH * 0.005;
  pelvis.add(hipMesh);

  // Torso
  const torso = new THREE.Group();
  torso.name = "torso";
  torso.position.y = torsoH * 0.42;
  pelvis.add(torso);
  joints.torso = torso;

  const torsoMesh = new THREE.Mesh(
    new THREE.BoxGeometry(torsoW, torsoH, torsoD),
    topMat,
  );
  torsoMesh.position.y = 0;
  torso.add(torsoMesh);

  // Top variation: hoodie hood, jacket lapels, dress skirt panel
  decorateTorso(torso, style, { torsoW, torsoH, torsoD, topC, botC });

  // Neck
  const neck = new THREE.Group();
  neck.name = "neck";
  neck.position.y = torsoH * 0.5 + neckH * 0.5;
  torso.add(neck);
  joints.neck = neck;
  const neckMesh = new THREE.Mesh(
    new THREE.CylinderGeometry(totalH * 0.025, totalH * 0.028, neckH, 12),
    skinMat,
  );
  neck.add(neckMesh);

  // Head
  const head = new THREE.Group();
  head.name = "head";
  head.position.y = neckH * 0.5 + headH * 0.5;
  neck.add(head);
  joints.head = head;

  buildHead(head, style, { headH, hairMat, skinMat });

  // Arms
  for (const side of ["left", "right"]) {
    const sgn = side === "left" ? 1 : -1;

    const shoulder = new THREE.Group();
    shoulder.name = side + "Shoulder";
    shoulder.position.set(sgn * (torsoW * 0.5 + armR * 0.4), torsoH * 0.42, 0);
    torso.add(shoulder);
    joints[side + "Shoulder"] = shoulder;

    const upperArm = new THREE.Group();
    upperArm.name = side + "Arm";
    upperArm.position.y = -upperArmH * 0.5;
    shoulder.add(upperArm);
    joints[side + "Arm"] = upperArm;

    const armMesh = new THREE.Mesh(
      new THREE.CylinderGeometry(armR, armR * 0.85, upperArmH, 10),
      sleeveMatFor(style, side === "left", { topMat, skinMat }),
    );
    upperArm.add(armMesh);

    const elbow = new THREE.Group();
    elbow.name = side + "Elbow";
    elbow.position.y = -upperArmH * 0.5;
    upperArm.add(elbow);
    joints[side + "Elbow"] = elbow;

    const forearm = new THREE.Group();
    forearm.name = side + "Forearm";
    forearm.position.y = -forearmH * 0.5;
    elbow.add(forearm);
    joints[side + "Forearm"] = forearm;

    const forearmMesh = new THREE.Mesh(
      new THREE.CylinderGeometry(armR * 0.85, armR * 0.7, forearmH, 10),
      forearmMatFor(style, { topMat, skinMat }),
    );
    forearm.add(forearmMesh);

    const hand = new THREE.Group();
    hand.name = side + "Hand";
    hand.position.y = -forearmH * 0.5;
    forearm.add(hand);
    joints[side + "Hand"] = hand;

    const handMesh = new THREE.Mesh(
      new THREE.SphereGeometry(armR * 1.05, 10, 8),
      skinMat,
    );
    handMesh.scale.set(1, 1.3, 0.7);
    handMesh.position.y = -armR * 0.5;
    hand.add(handMesh);
  }

  // Legs
  for (const side of ["left", "right"]) {
    const sgn = side === "left" ? 1 : -1;

    const hip = new THREE.Group();
    hip.name = side + "Hip";
    hip.position.set(sgn * hipW * 0.3, -totalH * 0.005, 0);
    pelvis.add(hip);
    joints[side + "Hip"] = hip;

    const thigh = new THREE.Group();
    thigh.name = side + "Thigh";
    thigh.position.y = -thighH * 0.5;
    hip.add(thigh);
    joints[side + "Thigh"] = thigh;

    const thighMesh = new THREE.Mesh(
      new THREE.CylinderGeometry(legR, legR * 0.85, thighH, 12),
      legMatFor(style, "thigh", { bottomMat, skinMat }),
    );
    thigh.add(thighMesh);

    const knee = new THREE.Group();
    knee.name = side + "Knee";
    knee.position.y = -thighH * 0.5;
    thigh.add(knee);
    joints[side + "Knee"] = knee;

    const calf = new THREE.Group();
    calf.name = side + "Calf";
    calf.position.y = -calfH * 0.5;
    knee.add(calf);
    joints[side + "Calf"] = calf;

    const calfMesh = new THREE.Mesh(
      new THREE.CylinderGeometry(legR * 0.78, legR * 0.55, calfH, 12),
      legMatFor(style, "calf", { bottomMat, skinMat }),
    );
    calf.add(calfMesh);

    const foot = new THREE.Group();
    foot.name = side + "Foot";
    foot.position.y = -calfH * 0.5;
    calf.add(foot);
    joints[side + "Foot"] = foot;

    const footMesh = new THREE.Mesh(
      new THREE.BoxGeometry(legR * 1.5, legR * 0.6, footL),
      shoeMat,
    );
    footMesh.position.set(0, -legR * 0.3, footL * 0.3);
    foot.add(footMesh);
  }

  // Default A-pose -> rest in a relaxed neutral standing pose so we
  // don't look like a Vitruvian sketch out of the box.
  setRelaxedRest(joints);

  // Expression face plate (a quad attached to the head with a canvas
  // texture; expression_system.js redraws the canvas on demand).
  const facePlate = makeFacePlate(style, totalH);
  facePlate.position.set(0, headH * 0.06, headH * 0.42);
  head.add(facePlate);

  function setExpression(name) {
    const tex = facePlate.userData.texture;
    renderExpressionTexture(tex.image, name, style);
    tex.needsUpdate = true;
  }
  setExpression("neutral");

  function dispose() {
    root.traverse((o) => {
      if (o.geometry && o.geometry.dispose) o.geometry.dispose();
      if (o.material) {
        if (Array.isArray(o.material)) o.material.forEach((m) => m.dispose());
        else o.material.dispose();
      }
    });
    if (facePlate.userData.texture) facePlate.userData.texture.dispose();
  }

  return { root, joints, setExpression, dispose };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function skinColor(style) {
  // HSL-based: hue ~25 (warm), lightness controls how light the skin is.
  const h = style.skinHue ?? 22;
  const s = 0.45;
  const l = style.skinLightness ?? 0.74;
  const c = new THREE.Color();
  c.setHSL(h / 360, s, l);
  return c;
}

function sleeveMatFor(style, isLeft, mats) {
  // Short-sleeve = upper arm shows top color, forearm = skin
  // Long-sleeve hoodie/jacket/sweater = top color all the way
  const longSleeve = ["hoodie", "jacket", "sweater"].includes(style.top);
  if (longSleeve) return mats.topMat;
  return mats.topMat;
}

function forearmMatFor(style, mats) {
  const longSleeve = ["hoodie", "jacket", "sweater"].includes(style.top);
  if (longSleeve) return mats.topMat;
  return mats.skinMat;
}

function legMatFor(style, part, mats) {
  // Skirt + dress: thigh shows bottom (skirt panel covers it), calf shows skin
  if (style.bottom === "skirt" && part === "calf") return mats.skinMat;
  if (style.bottom === "long_skirt") return mats.bottomMat;
  if (style.bottom === "shorts" && part === "calf") return mats.skinMat;
  return mats.bottomMat;
}

function decorateTorso(torso, style, dims) {
  const { torsoW, torsoH, torsoD, topC, botC } = dims;
  if (style.top === "hoodie") {
    // Hood on the back of the neck
    const hoodGeom = new THREE.SphereGeometry(
      torsoW * 0.55, 12, 8, 0, Math.PI * 2, 0, Math.PI * 0.5,
    );
    const hood = new THREE.Mesh(
      hoodGeom,
      new THREE.MeshStandardMaterial({ color: topC, roughness: 0.9 }),
    );
    hood.rotation.x = Math.PI;
    hood.position.set(0, torsoH * 0.45, -torsoD * 0.4);
    hood.scale.set(1, 0.9, 1.4);
    torso.add(hood);
  }
  if (style.top === "jacket") {
    // Jacket front split
    const split = new THREE.Mesh(
      new THREE.PlaneGeometry(torsoW * 0.06, torsoH * 0.95),
      new THREE.MeshStandardMaterial({
        color: new THREE.Color(0x000000), opacity: 0.6, transparent: true,
      }),
    );
    split.position.set(0, 0, torsoD * 0.51);
    torso.add(split);
  }
  if (style.bottom === "skirt" || style.bottom === "long_skirt") {
    const len = style.bottom === "long_skirt" ? torsoH * 1.0 : torsoH * 0.5;
    const skirt = new THREE.Mesh(
      new THREE.ConeGeometry(torsoW * 0.95, len, 16, 1, true),
      new THREE.MeshStandardMaterial({
        color: botC, roughness: 0.9, side: THREE.DoubleSide,
      }),
    );
    skirt.position.y = -torsoH * 0.5 - len * 0.45;
    torso.add(skirt);
  }
  if (style.bottom === "dress" || style.top === "dress") {
    // dress = torso continues down as a long panel
    const dress = new THREE.Mesh(
      new THREE.ConeGeometry(torsoW * 1.0, torsoH * 0.9, 16, 1, true),
      new THREE.MeshStandardMaterial({
        color: topC, roughness: 0.85, side: THREE.DoubleSide,
      }),
    );
    dress.position.y = -torsoH * 0.5 - torsoH * 0.4;
    torso.add(dress);
  }
}

function buildHead(head, style, dims) {
  const { headH, hairMat, skinMat } = dims;

  // Skull (slightly egg-shaped for stylised look)
  const skull = new THREE.Mesh(
    new THREE.SphereGeometry(headH * 0.5, 24, 20),
    skinMat,
  );
  skull.scale.set(0.95, 1.0, 0.95);
  head.add(skull);

  // Hair pieces depending on hairstyle
  buildHair(head, style, { headH, hairMat });

  // Accessory
  if (style.accessory === "glasses") {
    const lensMat = new THREE.MeshStandardMaterial({
      color: 0x333333, roughness: 0.5, metalness: 0.5,
    });
    for (const sgn of [-1, 1]) {
      const l = new THREE.Mesh(
        new THREE.TorusGeometry(headH * 0.13, headH * 0.018, 8, 16),
        lensMat,
      );
      l.position.set(sgn * headH * 0.18, headH * 0.05, headH * 0.42);
      l.rotation.x = Math.PI / 2;
      head.add(l);
    }
    const bridge = new THREE.Mesh(
      new THREE.CylinderGeometry(headH * 0.008, headH * 0.008, headH * 0.1, 6),
      lensMat,
    );
    bridge.rotation.z = Math.PI / 2;
    bridge.position.set(0, headH * 0.05, headH * 0.42);
    head.add(bridge);
  }
  if (style.accessory === "hairband") {
    const band = new THREE.Mesh(
      new THREE.TorusGeometry(headH * 0.46, headH * 0.04, 8, 24),
      new THREE.MeshStandardMaterial({
        color: new THREE.Color(style.accessoryColor || "#ff80a8"),
        roughness: 0.7,
      }),
    );
    band.rotation.x = Math.PI / 2;
    band.position.y = headH * 0.18;
    head.add(band);
  }
}

function buildHair(head, style, dims) {
  const { headH, hairMat } = dims;
  const r = headH * 0.5;

  switch (style.hair) {
    case "buzz": {
      const cap = new THREE.Mesh(
        new THREE.SphereGeometry(r * 1.02, 24, 16, 0, Math.PI * 2, 0, Math.PI * 0.55),
        hairMat,
      );
      cap.position.y = r * 0.05;
      head.add(cap);
      break;
    }
    case "short": {
      const cap = new THREE.Mesh(
        new THREE.SphereGeometry(r * 1.08, 24, 16, 0, Math.PI * 2, 0, Math.PI * 0.62),
        hairMat,
      );
      cap.position.y = r * 0.08;
      head.add(cap);
      // Short fringe
      const fringe = new THREE.Mesh(
        new THREE.BoxGeometry(r * 0.95, r * 0.16, r * 0.18),
        hairMat,
      );
      fringe.position.set(0, r * 0.55, r * 0.6);
      head.add(fringe);
      break;
    }
    case "side_swept": {
      const cap = new THREE.Mesh(
        new THREE.SphereGeometry(r * 1.08, 24, 16, 0, Math.PI * 2, 0, Math.PI * 0.7),
        hairMat,
      );
      cap.position.y = r * 0.05;
      head.add(cap);
      const sweep = new THREE.Mesh(
        new THREE.BoxGeometry(r * 1.15, r * 0.12, r * 0.18),
        hairMat,
      );
      sweep.position.set(r * 0.2, r * 0.6, r * 0.55);
      sweep.rotation.z = -0.4;
      head.add(sweep);
      break;
    }
    case "bob": {
      const cap = new THREE.Mesh(
        new THREE.SphereGeometry(r * 1.12, 24, 16, 0, Math.PI * 2, 0, Math.PI * 0.78),
        hairMat,
      );
      cap.position.y = r * 0.0;
      cap.scale.set(1.05, 1.0, 1.05);
      head.add(cap);
      // Fringe block
      const fringe = new THREE.Mesh(
        new THREE.BoxGeometry(r * 1.05, r * 0.32, r * 0.22),
        hairMat,
      );
      fringe.position.set(0, r * 0.32, r * 0.55);
      head.add(fringe);
      break;
    }
    case "long_straight": {
      const cap = new THREE.Mesh(
        new THREE.SphereGeometry(r * 1.1, 24, 16, 0, Math.PI * 2, 0, Math.PI * 0.85),
        hairMat,
      );
      head.add(cap);
      // Long curtain down the back
      const curtain = new THREE.Mesh(
        new THREE.BoxGeometry(r * 2.0, r * 3.0, r * 0.5),
        hairMat,
      );
      curtain.position.set(0, -r * 1.2, -r * 0.55);
      head.add(curtain);
      // Fringe
      const fringe = new THREE.Mesh(
        new THREE.BoxGeometry(r * 1.0, r * 0.4, r * 0.2),
        hairMat,
      );
      fringe.position.set(0, r * 0.3, r * 0.55);
      head.add(fringe);
      break;
    }
    case "twin_tails": {
      const cap = new THREE.Mesh(
        new THREE.SphereGeometry(r * 1.08, 24, 16, 0, Math.PI * 2, 0, Math.PI * 0.7),
        hairMat,
      );
      cap.position.y = r * 0.04;
      head.add(cap);
      // Side ponytails
      for (const sgn of [-1, 1]) {
        const tail = new THREE.Mesh(
          new THREE.CylinderGeometry(r * 0.18, r * 0.10, r * 2.4, 12),
          hairMat,
        );
        tail.position.set(sgn * r * 0.95, -r * 0.3, -r * 0.05);
        tail.rotation.z = sgn * 0.18;
        head.add(tail);
        // tail tip ball
        const tip = new THREE.Mesh(
          new THREE.SphereGeometry(r * 0.12, 10, 8),
          hairMat,
        );
        tip.position.set(sgn * r * 1.15, -r * 1.45, -r * 0.05);
        head.add(tip);
      }
      break;
    }
    case "long_curly": {
      const cap = new THREE.Mesh(
        new THREE.SphereGeometry(r * 1.16, 24, 16, 0, Math.PI * 2, 0, Math.PI * 0.85),
        hairMat,
      );
      cap.scale.set(1.05, 1.05, 1.0);
      head.add(cap);
      // Curl puffs around the bottom
      for (let i = 0; i < 9; i++) {
        const a = (i / 9) * Math.PI * 2;
        const puff = new THREE.Mesh(
          new THREE.SphereGeometry(r * 0.32, 10, 8),
          hairMat,
        );
        puff.position.set(
          Math.sin(a) * r * 1.08,
          -r * (0.6 + Math.cos(i * 1.2) * 0.18),
          Math.cos(a) * r * 1.08,
        );
        head.add(puff);
      }
      break;
    }
    case "wolf_tail": {
      const cap = new THREE.Mesh(
        new THREE.SphereGeometry(r * 1.08, 24, 16, 0, Math.PI * 2, 0, Math.PI * 0.7),
        hairMat,
      );
      cap.position.y = r * 0.04;
      head.add(cap);
      const tail = new THREE.Mesh(
        new THREE.CylinderGeometry(r * 0.18, r * 0.05, r * 1.6, 12),
        hairMat,
      );
      tail.position.set(0, -r * 0.6, -r * 0.4);
      tail.rotation.x = -0.2;
      head.add(tail);
      const fringe = new THREE.Mesh(
        new THREE.BoxGeometry(r * 1.0, r * 0.3, r * 0.18),
        hairMat,
      );
      fringe.position.set(0, r * 0.4, r * 0.55);
      head.add(fringe);
      break;
    }
    default: {
      const cap = new THREE.Mesh(
        new THREE.SphereGeometry(r * 1.05, 24, 16, 0, Math.PI * 2, 0, Math.PI * 0.6),
        hairMat,
      );
      head.add(cap);
    }
  }
}

function makeFacePlate(style, totalH) {
  const headH = totalH * 0.13;
  const w = headH * 0.92;
  const h = headH * 0.85;
  const canvas = document.createElement("canvas");
  canvas.width = 256;
  canvas.height = 256;
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  const mat = new THREE.MeshStandardMaterial({
    map: tex,
    transparent: true,
    roughness: 1.0,
    metalness: 0.0,
    depthWrite: false,
  });
  const geom = new THREE.PlaneGeometry(w, h);
  const plate = new THREE.Mesh(geom, mat);
  plate.userData.texture = tex;
  return plate;
}

/**
 * Apply a relaxed rest pose so the avatar isn't doing a T-pose by default.
 * Pose presets will overwrite this on top.
 */
function setRelaxedRest(joints) {
  // Slight arm-down rest
  if (joints.leftShoulder) joints.leftShoulder.rotation.z = -1.35;
  if (joints.rightShoulder) joints.rightShoulder.rotation.z = 1.35;
  if (joints.leftElbow) joints.leftElbow.rotation.x = 0.1;
  if (joints.rightElbow) joints.rightElbow.rotation.x = 0.1;
  // Legs straight
  if (joints.leftHip) joints.leftHip.rotation.x = 0;
  if (joints.rightHip) joints.rightHip.rotation.x = 0;
}
