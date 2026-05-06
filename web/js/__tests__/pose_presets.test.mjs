// Unit test for pose_presets — only the pure-logic classifiers.
// Skips the joint-mutation parts since those need Three.js objects.

import { strict as assert } from "node:assert";
import { test } from "node:test";

import {
  classifyExpression,
  pickPosePreset,
  listPosePresets,
} from "../pose_presets.js";

test("listPosePresets returns at least 10 presets", () => {
  const list = listPosePresets();
  assert.ok(list.length >= 10, `expected ≥10 presets, got ${list.length}`);
  assert.ok(list.includes("standing"));
  assert.ok(list.includes("walking"));
  assert.ok(list.includes("crouch"));
});

test("pickPosePreset matches Chinese stance keywords", () => {
  assert.equal(pickPosePreset({ stance: "蹲下来" }), "crouch");
  assert.equal(pickPosePreset({ stance: "半坐", upper_body: "" }), "half_sit");
  assert.equal(pickPosePreset({ stance: "向前漫步" }), "walking");
  assert.equal(pickPosePreset({ stance: "靠墙站着" }), "leaning");
  assert.equal(pickPosePreset({ hands: "比 V 字" }), "v_sign");
  assert.equal(pickPosePreset({ hands: "牵手" }), "holding_hands");
  assert.equal(pickPosePreset({ upper_body: "抱臂" }), "arms_crossed");
  assert.equal(pickPosePreset({ upper_body: "手插腰" }), "hand_on_hip");
  assert.equal(pickPosePreset({ gaze: "回头看" }), "looking_back");
});

test("pickPosePreset matches English stance keywords", () => {
  assert.equal(pickPosePreset({ stance: "crouch low" }), "crouch");
  assert.equal(pickPosePreset({ stance: "walking forward" }), "walking");
  assert.equal(pickPosePreset({ hands: "peace sign" }), "v_sign");
  assert.equal(pickPosePreset({ stance: "leaning against the wall" }), "leaning");
});

test("pickPosePreset defaults to standing", () => {
  assert.equal(pickPosePreset({}), "standing");
  assert.equal(pickPosePreset({ stance: "" }), "standing");
  assert.equal(pickPosePreset(null), "standing");
});

test("classifyExpression maps to 5 face states", () => {
  assert.equal(classifyExpression({ expression: "" }), "neutral");
  assert.equal(classifyExpression({ expression: "微笑" }), "joy");
  assert.equal(classifyExpression({ expression: "smile" }), "joy");
  assert.equal(classifyExpression({ expression: "抿嘴" }), "smirk");
  assert.equal(classifyExpression({ expression: "smirk" }), "smirk");
  assert.equal(classifyExpression({ expression: "惊讶" }), "surprised");
  assert.equal(classifyExpression({ expression: "surprised" }), "surprised");
  assert.equal(classifyExpression({ expression: "认真" }), "pensive");
  assert.equal(classifyExpression({ expression: "frown" }), "pensive");
  assert.equal(classifyExpression({ expression: "中性" }), "neutral");
});
