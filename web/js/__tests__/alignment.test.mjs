/**
 * Headless smoke tests for AlignmentMachine. Run with `node alignment.test.mjs`
 * from the web/js/__tests__ folder. Doesn't depend on a test runner; uses
 * plain assert + process.exitCode.
 */
import assert from "node:assert/strict";
import { AlignmentMachine, circDelta } from "../alignment.js";

let failed = 0;
const queue = [];
function test(name, fn) {
  queue.push(async () => {
    try {
      await fn();
      console.log("  ok   " + name);
    } catch (e) {
      failed++;
      console.error("  FAIL " + name);
      console.error("       " + (e.stack || e.message));
    }
  });
}

console.log("circDelta");
test("simple", () => assert.equal(circDelta(10, 0), 10));
test("wraps short way around", () => assert.equal(circDelta(350, 10), -20));
test("zero", () => assert.equal(circDelta(0, 0), 0));

console.log("\nAlignmentMachine - heading classification");
{
  const a = new AlignmentMachine({ target: { headingDeg: 90, pitchDeg: 0, distanceM: 2 } });
  test("ok band", () => {
    a.update({ headingDeg: 92 });
    assert.equal(a.snapshot().heading.status, "ok");
  });
  test("warn band", () => {
    a.update({ headingDeg: 100 });
    assert.equal(a.snapshot().heading.status, "warn");
  });
  test("far band", () => {
    a.update({ headingDeg: 130 });
    assert.equal(a.snapshot().heading.status, "far");
  });
}

console.log("\nAlignmentMachine - aggregate ok requires all dims");
test("aggregate not ok with missing pitch/distance/person", () => {
  const a = new AlignmentMachine({
    target: { headingDeg: 0, pitchDeg: 0, distanceM: 2 },
    holdMs: 0,
  });
  a.update({ headingDeg: 0 });
  assert.equal(a.snapshot().aggregateOk, false);
});
test("aggregate ok once all dims present and inside band", () => {
  const a = new AlignmentMachine({
    target: { headingDeg: 0, pitchDeg: 0, distanceM: 2 },
    holdMs: 0,
  });
  a.update({ headingDeg: 0 });
  a.update({ pitchDeg: 0 });
  a.update({ distanceM: 2.0 });
  a.update({ personPresent: true });
  assert.equal(a.snapshot().aggregateOk, true);
});

console.log("\nAlignmentMachine - green fires after holdMs");
test("green does not fire before holdMs but fires after", async () => {
  const a = new AlignmentMachine({
    target: { headingDeg: 0, pitchDeg: 0, distanceM: 2 },
    holdMs: 50,
  });
  let firedCount = 0;
  a.onGreen(() => firedCount++);
  a.update({ headingDeg: 0, pitchDeg: 0, distanceM: 2.0, personPresent: true });
  assert.equal(firedCount, 0, "must not fire immediately");
  await new Promise((r) => setTimeout(r, 80));
  a.update({ headingDeg: 0 }); // tick the timer
  assert.equal(firedCount, 1, "must fire exactly once after hold");
  // Stays green → must not re-fire
  a.update({ headingDeg: 0 });
  a.update({ headingDeg: 1 });
  a.update({ headingDeg: 0 });
  assert.equal(firedCount, 1, "must not double-fire while staying green");
});

console.log("\nAlignmentMachine - disable removes dim from aggregate");
test("disabled dims excluded from aggregate", () => {
  const a = new AlignmentMachine({
    target: { headingDeg: 0, pitchDeg: 0, distanceM: 2 },
    holdMs: 0,
  });
  a.disable("distance");
  a.disable("person");
  a.update({ headingDeg: 0, pitchDeg: 0 });
  assert.equal(a.snapshot().aggregateOk, true);
});

console.log("\nAlignmentMachine - leaving ok resets the timer");
test("re-fires green after going far and coming back", async () => {
  const a = new AlignmentMachine({ holdMs: 40 });
  let firedCount = 0;
  a.onGreen(() => firedCount++);
  a.disable("distance");
  a.disable("person");

  a.update({ headingDeg: 0, pitchDeg: 0 });
  await new Promise((r) => setTimeout(r, 60));
  a.update({ headingDeg: 0 });
  assert.equal(firedCount, 1, "first green");

  a.update({ headingDeg: 60 }); // far → unsets greenFired
  assert.equal(a.snapshot().aggregateOk, false);
  a.update({ headingDeg: 0 }); // back to ok, restart timer
  await new Promise((r) => setTimeout(r, 60));
  a.update({ headingDeg: 0 });
  assert.equal(firedCount, 2, "second green after recovery");
});

async function run() {
  for (const job of queue) await job();
  console.log("");
  if (failed > 0) {
    console.error(`\n${failed} test(s) failed`);
    process.exitCode = 1;
  } else {
    console.log(`All ${queue.length} alignment tests passed.`);
  }
}
run();
