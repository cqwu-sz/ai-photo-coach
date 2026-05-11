import * as THREE from 'three';
import fs from 'fs';

// Polyfills required by GLTFLoader on Node.
globalThis.self = globalThis;
globalThis.window = globalThis;
globalThis.document = { createElementNS: () => ({}) };
globalThis.URL = globalThis.URL || class {};
globalThis.createImageBitmap = undefined;

const { GLTFLoader } = await import('three/examples/jsm/loaders/GLTFLoader.js');

const buf = fs.readFileSync('web/avatars/preset/female_youth_18.glb');
const loader = new GLTFLoader();
loader.parse(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength), '', (gltf) => {
  const root = gltf.scene;
  const bones = {};
  root.traverse(n => { if (n.isBone) bones[(n.name||'').replace(/^mixamorig:?/i,'').toLowerCase()] = n; });
  const leftArm = bones.leftarm, leftHand = bones.lefthand;
  const rightArm = bones.rightarm, rightHand = bones.righthand;
  root.updateMatrixWorld(true);
  const lhBefore = new THREE.Vector3(); leftHand.getWorldPosition(lhBefore);
  const rhBefore = new THREE.Vector3(); rightHand.getWorldPosition(rhBefore);
  const laPos = new THREE.Vector3(); leftArm.getWorldPosition(laPos);
  console.log('leftArm  world :', laPos.x.toFixed(3), laPos.y.toFixed(3), laPos.z.toFixed(3));
  console.log('leftHand BEFORE:', lhBefore.x.toFixed(3), lhBefore.y.toFixed(3), lhBefore.z.toFixed(3));
  console.log('rightHand BEFORE:', rhBefore.x.toFixed(3), rhBefore.y.toFixed(3), rhBefore.z.toFixed(3));

  function rotateBoneWorld(bone, axis, angleRad) {
    bone.parent.updateWorldMatrix(true, false);
    bone.updateWorldMatrix(true, false);
    const wq = new THREE.Quaternion(); bone.getWorldQuaternion(wq);
    const delta = new THREE.Quaternion().setFromAxisAngle(axis, angleRad);
    wq.premultiply(delta);
    const pInv = new THREE.Quaternion(); bone.parent.getWorldQuaternion(pInv).invert();
    bone.quaternion.copy(pInv.multiply(wq));
    bone.updateMatrixWorld(true);
  }

  const X = new THREE.Vector3(1,0,0);
  const D = Math.PI/180;
  rotateBoneWorld(leftArm,  X,  80*D);
  rotateBoneWorld(rightArm, X, -80*D);
  root.updateMatrixWorld(true);

  const lhAfter = new THREE.Vector3(); leftHand.getWorldPosition(lhAfter);
  const rhAfter = new THREE.Vector3(); rightHand.getWorldPosition(rhAfter);
  console.log('leftHand AFTER :', lhAfter.x.toFixed(3), lhAfter.y.toFixed(3), lhAfter.z.toFixed(3));
  console.log('rightHand AFTER:', rhAfter.x.toFixed(3), rhAfter.y.toFixed(3), rhAfter.z.toFixed(3));
  console.log('left  hand dy=', (lhAfter.y - lhBefore.y).toFixed(3), '(want negative)');
  console.log('right hand dy=', (rhAfter.y - rhBefore.y).toFixed(3), '(want negative)');
}, (err) => { console.error('parse err', err); process.exit(1); });
