// Pure-logic test for the scene-mode branch in render.js.
//
// We can't load render.js directly under node (it pulls in browser-only
// dependencies like Three.js), so we re-test the helper isSceneryShot
// alongside the contract render.js relies on: an empty `poses` array
// means a scenery shot.

import { strict as assert } from "node:assert";
import { test } from "node:test";

function isSceneryShot(shot) {
  return !shot.poses || shot.poses.length === 0;
}

test("scenery shot detected when poses array is empty", () => {
  assert.equal(isSceneryShot({ poses: [] }), true);
});

test("scenery shot detected when poses key is missing", () => {
  assert.equal(isSceneryShot({}), true);
});

test("portrait shot not flagged scenery", () => {
  const shot = {
    poses: [{ person_count: 1, layout: "single", persons: [{ role: "a" }] }],
  };
  assert.equal(isSceneryShot(shot), false);
});

test("scenery shot can still hold composition + camera tips", () => {
  const shot = {
    poses: [],
    composition: { primary: "leading_line" },
    camera: { focal_length_mm: 24, aperture: "f/8", shutter: "1/200", iso: 100 },
    angle: { azimuth_deg: 90, pitch_deg: 0, distance_m: 6 },
  };
  assert.ok(isSceneryShot(shot));
  assert.equal(shot.composition.primary, "leading_line");
  assert.equal(shot.camera.aperture, "f/8");
});
