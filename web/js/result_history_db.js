// IndexedDB store for full analyze responses, keyed by entry id.
//
// Why split storage:
//   * `store.js:listResultHistory` keeps a SMALL summary in
//     localStorage (id, ts, title, scene, shot_count, model). That
//     summary is ~200 bytes per entry → 10 entries fits comfortably
//     in the 5 MB localStorage quota even on Safari Private Mode.
//   * The full response (rationale, scene_aggregate, style match,
//     coach brief, etc.) is 100-300 KB and goes here in IndexedDB,
//     which has GB-class quota everywhere.
//
// Schema:
//   db:    aphc-history   (v1)
//   store: 'payloads'     keyPath = 'id'  -> { id, payload, savedAt }
//
// We piggy-back on the same id allocation logic in store.js so list
// items and payloads stay in lockstep.

const DB_NAME = "aphc-history";
const DB_VERSION = 1;
const STORE = "payloads";

function openDB() {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === "undefined") {
      reject(new Error("indexedDB unavailable"));
      return;
    }
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: "id" });
      }
    };
    req.onerror = () => reject(req.error);
    req.onsuccess = () => resolve(req.result);
  });
}

export async function saveResultPayload(id, payload) {
  if (!id || !payload) return;
  try {
    const db = await openDB();
    await new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, "readwrite");
      tx.objectStore(STORE).put({ id, payload, savedAt: Date.now() });
      tx.oncomplete = resolve;
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error);
    });
    db.close();
  } catch (e) {
    // Non-fatal — history list still works, point-and-replay just won't.
    console.warn("history payload save failed", e);
  }
}

export async function loadResultPayload(id) {
  if (!id) return null;
  try {
    const db = await openDB();
    const payload = await new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, "readonly");
      const req = tx.objectStore(STORE).get(id);
      req.onsuccess = () => resolve(req.result ? req.result.payload : null);
      req.onerror = () => reject(req.error);
    });
    db.close();
    return payload;
  } catch (e) {
    console.warn("history payload load failed", e);
    return null;
  }
}

export async function deleteResultPayload(id) {
  if (!id) return;
  try {
    const db = await openDB();
    await new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, "readwrite");
      tx.objectStore(STORE).delete(id);
      tx.oncomplete = resolve;
      tx.onerror = () => reject(tx.error);
    });
    db.close();
  } catch {}
}

export async function clearAllResultPayloads() {
  try {
    const db = await openDB();
    await new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, "readwrite");
      tx.objectStore(STORE).clear();
      tx.oncomplete = resolve;
      tx.onerror = () => reject(tx.error);
    });
    db.close();
  } catch {}
}
