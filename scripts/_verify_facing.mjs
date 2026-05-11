import * as THREE from 'three';
import fs from 'fs';
globalThis.self = globalThis;
globalThis.window = globalThis;
globalThis.document = { createElementNS: () => ({}) };
globalThis.URL = globalThis.URL || class {};
const { GLTFLoader } = await import('three/examples/jsm/loaders/GLTFLoader.js');
const buf = fs.readFileSync('web/avatars/preset/female_youth_18.glb');
const loader = new GLTFLoader();
loader.parse(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength), '', (gltf) => {
  const root = gltf.scene;
  root.updateMatrixWorld(true);
  const bones = {};
  root.traverse(n => { if (n.isBone) bones[(n.name||'').replace(/^mixamorig:?/i,'').toLowerCase()] = n; });
  const head = bones.head, hips = bones.hips, leftArm = bones.leftarm;
  const hp = new THREE.Vector3(); head.getWorldPosition(hp);
  const ip = new THREE.Vector3(); hips.getWorldPosition(ip);
  const lap = new THREE.Vector3(); leftArm.getWorldPosition(lap);
  console.log('Head world :', hp.x.toFixed(3), hp.y.toFixed(3), hp.z.toFixed(3));
  console.log('Hips world :', ip.x.toFixed(3), ip.y.toFixed(3), ip.z.toFixed(3));
  console.log('LeftArm xz :', lap.x.toFixed(3), lap.z.toFixed(3));
  // Head forward axis: local Z direction in world
  const fwd = new THREE.Vector3(0,0,1).applyQuaternion(head.getWorldQuaternion(new THREE.Quaternion()));
  console.log('Head local +Z in world:', fwd.x.toFixed(3), fwd.y.toFixed(3), fwd.z.toFixed(3));
  const fwdY = new THREE.Vector3(0,1,0).applyQuaternion(head.getWorldQuaternion(new THREE.Quaternion()));
  console.log('Head local +Y in world:', fwdY.x.toFixed(3), fwdY.y.toFixed(3), fwdY.z.toFixed(3));
  const fwdX = new THREE.Vector3(1,0,0).applyQuaternion(head.getWorldQuaternion(new THREE.Quaternion()));
  console.log('Head local +X in world:', fwdX.x.toFixed(3), fwdX.y.toFixed(3), fwdX.z.toFixed(3));
}, (err) => { console.error('parse err', err); });
