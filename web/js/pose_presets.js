/**
 * Library of pose presets — sets of joint rotations applied to an avatar's
 * skeleton. Each preset is a function `apply(joints, mirror)` that mutates
 * the joints' Euler rotations.
 *
 * mirror=true applies a left-right mirrored variant (useful when you have
 * two people facing each other).
 *
 * The classifier `pickPosePreset(personDescription)` reads the AI-produced
 * Chinese stance/upper_body/hands/gaze fields and returns a preset name.
 */

// ---------------------------------------------------------------------------
// Helpers — Three.js Euler angles in radians.
// ---------------------------------------------------------------------------

const D2R = Math.PI / 180;

function setRot(joint, x = 0, y = 0, z = 0) {
  if (!joint) return;
  joint.rotation.set(x * D2R, y * D2R, z * D2R);
}

function maybeMirror(side, mirror) {
  if (!mirror) return side;
  return side === "left" ? "right" : "left";
}

// ---------------------------------------------------------------------------
// Each preset writes into the joints map. Side-effects only.
// All angles in degrees (converted in setRot).
//
// Coordinate convention (Three.js with Y up):
//   - Shoulder rotation.z negative → arm raises sideways (away from torso)
//   - Shoulder rotation.x positive → arm reaches forward
//   - Elbow rotation.x positive → forearm bends up
//   - Hip rotation.x positive → leg lifts forward
//   - Knee rotation.x positive → calf bends back (kneeling)
// ---------------------------------------------------------------------------

const PRESETS = {
  /** Default standing, hands at side, feet shoulder-width. */
  standing(joints) {
    setRot(joints.leftShoulder, 0, 0, -78);
    setRot(joints.rightShoulder, 0, 0, 78);
    setRot(joints.leftElbow, 5, 0, 0);
    setRot(joints.rightElbow, 5, 0, 0);
    setRot(joints.leftHip, 0, 0, 0);
    setRot(joints.rightHip, 0, 0, 0);
    setRot(joints.leftKnee, 0, 0, 0);
    setRot(joints.rightKnee, 0, 0, 0);
    setRot(joints.head, -2, 0, 0);
    setRot(joints.neck, 0, 0, 0);
  },

  /** Hands clasped in front, gentle smile. */
  hands_clasped(joints) {
    setRot(joints.leftShoulder, 0, 10, -50);
    setRot(joints.rightShoulder, 0, -10, 50);
    setRot(joints.leftElbow, 90, 0, 0);
    setRot(joints.rightElbow, 90, 0, 0);
    setRot(joints.leftForearm, 0, -25, 0);
    setRot(joints.rightForearm, 0, 25, 0);
    setRot(joints.head, 0, 0, 0);
  },

  /** Walking stride, opposite arm/leg swing. */
  walking(joints, mirror) {
    const fwd = mirror ? -1 : 1;
    setRot(joints.leftShoulder, -25 * fwd, 0, -75);
    setRot(joints.rightShoulder, 25 * fwd, 0, 75);
    setRot(joints.leftElbow, 25, 0, 0);
    setRot(joints.rightElbow, 25, 0, 0);
    setRot(joints.leftHip, 22 * fwd, 0, 0);
    setRot(joints.rightHip, -22 * fwd, 0, 0);
    setRot(joints.leftKnee, 12, 0, 0);
    setRot(joints.rightKnee, 0, 0, 0);
    setRot(joints.head, 5, mirror ? 8 : -8, 0);
  },

  /** Half squat sitting on a low surface. */
  half_sit(joints) {
    setRot(joints.leftHip, 70, 6, 0);
    setRot(joints.rightHip, 70, -6, 0);
    setRot(joints.leftKnee, -90, 0, 0);
    setRot(joints.rightKnee, -90, 0, 0);
    setRot(joints.leftShoulder, 0, 5, -60);
    setRot(joints.rightShoulder, 0, -5, 60);
    setRot(joints.leftElbow, 65, 0, 0);
    setRot(joints.rightElbow, 65, 0, 0);
  },

  /** Crouching low. */
  crouch(joints) {
    setRot(joints.leftHip, 95, 8, 0);
    setRot(joints.rightHip, 95, -8, 0);
    setRot(joints.leftKnee, -130, 0, 0);
    setRot(joints.rightKnee, -130, 0, 0);
    setRot(joints.leftShoulder, 25, 0, -50);
    setRot(joints.rightShoulder, 25, 0, 50);
    setRot(joints.leftElbow, 50, 0, 0);
    setRot(joints.rightElbow, 50, 0, 0);
    setRot(joints.head, 6, 0, 0);
  },

  /** Looking back over shoulder. */
  looking_back(joints, mirror) {
    setRot(joints.head, -3, mirror ? -110 : 110, 0);
    setRot(joints.neck, 0, mirror ? -25 : 25, 0);
    setRot(joints.torso, 0, mirror ? -8 : 8, 0);
    // Hands lightly held in front
    setRot(joints.leftShoulder, 0, 10, -55);
    setRot(joints.rightShoulder, 0, -10, 55);
    setRot(joints.leftElbow, 70, 0, 0);
    setRot(joints.rightElbow, 70, 0, 0);
  },

  /** Holding hands sideways (use side='left' = inner hand). */
  holding_hands(joints, mirror) {
    // Inner arm extended out, outer arm relaxed
    const inner = mirror ? "right" : "left";
    const outer = mirror ? "left" : "right";
    setRot(joints[`${inner}Shoulder`], 5, 0, mirror ? 70 : -70);
    setRot(joints[`${inner}Elbow`], 12, 0, 0);
    setRot(joints[`${outer}Shoulder`], 0, 0, mirror ? -75 : 75);
    setRot(joints[`${outer}Elbow`], 5, 0, 0);
    setRot(joints.head, -3, mirror ? -8 : 8, 0);
  },

  /** Hand on hip, the other relaxed, slight contrapposto. */
  hand_on_hip(joints, mirror) {
    const side = mirror ? "right" : "left";
    const other = mirror ? "left" : "right";
    setRot(joints[`${side}Shoulder`], 0, 0, mirror ? 30 : -30);
    setRot(joints[`${side}Elbow`], 100, 0, 0);
    setRot(joints[`${other}Shoulder`], 0, 0, mirror ? -78 : 78);
    setRot(joints[`${other}Elbow`], 5, 0, 0);
    setRot(joints.torso, 0, 0, mirror ? 4 : -4);
    setRot(joints.head, -2, mirror ? 6 : -6, 0);
  },

  /** V-sign with right hand (or left if mirrored), other hand at side. */
  v_sign(joints, mirror) {
    const v = mirror ? "left" : "right";
    const other = mirror ? "right" : "left";
    setRot(joints[`${v}Shoulder`], -50, 0, mirror ? -45 : 45);
    setRot(joints[`${v}Elbow`], 95, 0, 0);
    setRot(joints[`${other}Shoulder`], 0, 0, mirror ? 78 : -78);
    setRot(joints[`${other}Elbow`], 5, 0, 0);
    setRot(joints.head, -2, mirror ? -4 : 4, 0);
  },

  /** Arms crossed in front. */
  arms_crossed(joints) {
    setRot(joints.leftShoulder, 0, 30, -50);
    setRot(joints.rightShoulder, 0, -30, 50);
    setRot(joints.leftElbow, 95, 0, 0);
    setRot(joints.rightElbow, 95, 0, 0);
    setRot(joints.leftForearm, 0, 35, 0);
    setRot(joints.rightForearm, 0, -35, 0);
    setRot(joints.head, -2, 0, 0);
  },

  /** Looking at partner with subtle lean (use mirror to face the other side). */
  facing_partner(joints, mirror) {
    setRot(joints.torso, 0, mirror ? -18 : 18, 0);
    setRot(joints.head, -3, mirror ? -25 : 25, 0);
    setRot(joints.leftShoulder, 0, 5, -65);
    setRot(joints.rightShoulder, 0, -5, 65);
    setRot(joints.leftElbow, 35, 0, 0);
    setRot(joints.rightElbow, 35, 0, 0);
  },

  /** Leaning against something (slight tilt + hand back). */
  leaning(joints, mirror) {
    setRot(joints.torso, -3, 0, mirror ? 6 : -6);
    setRot(joints.leftShoulder, mirror ? -30 : 0, 0, mirror ? 30 : -78);
    setRot(joints.rightShoulder, mirror ? 0 : -30, 0, mirror ? 78 : -30);
    setRot(joints.leftElbow, mirror ? 25 : 5, 0, 0);
    setRot(joints.rightElbow, mirror ? 5 : 25, 0, 0);
    setRot(joints.head, 0, mirror ? -10 : 10, 0);
  },
};

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export function listPosePresets() {
  return Object.keys(PRESETS);
}

export function applyPosePreset(name, joints, opts = {}) {
  const fn = PRESETS[name] || PRESETS.standing;
  // Always start from a relaxed default before stacking the preset, so a
  // pose with fewer joints set still looks natural (e.g. legs stay rest).
  PRESETS.standing(joints);
  fn(joints, opts.mirror === true);
  if (opts.bowHead) {
    setRot(joints.head, joints.head.rotation.x * (180 / Math.PI) - 6, 0, 0);
  }
}

/**
 * Heuristic mapping from a single AI-generated PersonPose's text fields
 * (stance, upper_body, hands, gaze) to a preset name. Plays it safe and
 * defaults to "standing" when nothing matches.
 */
export function pickPosePreset(person) {
  const blob = [
    person?.stance || "",
    person?.upper_body || "",
    person?.hands || "",
    person?.gaze || "",
    person?.position_hint || "",
  ].join(" ").toLowerCase();

  // Order matters — most specific patterns first.
  if (/v\s?字|v[-\s]?sign|比耶|比v|peace/i.test(blob)) return "v_sign";
  if (/牵手|拉手|hold(?:ing)?\s+hand/i.test(blob)) return "holding_hands";
  if (/回头|看向|看着|gaze|turn(?:ing)?\s+(?:back|head)|over\s+shoulder/i.test(blob)) return "looking_back";
  if (/抱臂|交叉|cross(?:ed)?\s+arms/i.test(blob)) return "arms_crossed";
  if (/叉腰|手插腰|hand\s+on\s+hip/i.test(blob)) return "hand_on_hip";
  if (/坐|sit|半坐/i.test(blob)) return "half_sit";
  if (/蹲|crouch|squat|半蹲/i.test(blob)) return "crouch";
  if (/靠|lean(?:ing)?/i.test(blob)) return "leaning";
  if (/走|漫步|散步|walk(?:ing)?|stride|前行|向前/i.test(blob)) return "walking";
  if (/对视|面向|相对|face|看向\s*person/i.test(blob)) return "facing_partner";
  if (/双手|交握|clasp/i.test(blob)) return "hands_clasped";
  return "standing";
}

/**
 * Map AI expression keyword (Chinese / English) to one of our 5 supported
 * face states.
 */
export function classifyExpression(person) {
  const t = (person?.expression || "").toLowerCase();
  if (!t) return "neutral";
  if (/抿嘴|smirk|淡笑|微微一笑/i.test(t)) return "smirk";
  if (/惊|surprised|wide-?eyed|睁大|意外/i.test(t)) return "surprised";
  if (/认真|沉思|皱眉|pensive|思|frown/i.test(t)) return "pensive";
  if (/笑|smile|joy|happy|开心|愉悦|轻松/i.test(t)) return "joy";
  return "neutral";
}
