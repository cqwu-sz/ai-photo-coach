// IndexedDB-backed cache for the most recent panorama capture.
//
// Why IndexedDB and not localStorage / sessionStorage?
//   - localStorage caps at ~5MB and stores strings only — 8 jpeg blobs at
//     ~600KB each blow past that.
//   - sessionStorage clears on tab close, defeating the point.
//
// We only ever keep the LAST capture: every fresh sweep wipes the store.
// Scheme:
//   db:    aphc-frames   (v1)
//   store: 'frames'      keyPath = 'index'  -> { index, blob, meta }
//   store: 'meta'        keyPath = 'id'     -> single 'singleton' record:
//                          { id, capturedAt, sceneMode, panoramaUrl, count }

const DB_NAME = "aphc-frames";
const DB_VERSION = 1;
const STORE_FRAMES = "frames";
const STORE_META = "meta";
const META_KEY = "singleton";

/** Maximum age before we treat cached frames as stale. 24h. */
const MAX_AGE_MS = 24 * 60 * 60 * 1000;

function openDB() {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === "undefined") {
      reject(new Error("indexedDB unavailable"));
      return;
    }
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE_FRAMES)) {
        db.createObjectStore(STORE_FRAMES, { keyPath: "index" });
      }
      if (!db.objectStoreNames.contains(STORE_META)) {
        db.createObjectStore(STORE_META, { keyPath: "id" });
      }
    };
    req.onerror = () => reject(req.error);
    req.onsuccess = () => resolve(req.result);
  });
}

function tx(db, stores, mode) {
  return db.transaction(stores, mode);
}

function promisify(req) {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

/**
 * Persist a fresh capture. Wipes any previous cache.
 *
 * @param {object} payload
 * @param {Blob[]} payload.frames        ordered jpeg/png blobs (length = N)
 * @param {object[]} payload.meta        per-frame meta (azimuth_deg, ...)
 * @param {string}   payload.sceneMode   the scene that was active at capture
 * @param {string|null} [payload.panoramaUrl] optional stitched panorama
 */
export async function saveCapturedFrames(payload) {
  if (!payload?.frames?.length) {
    throw new Error("saveCapturedFrames: empty frames");
  }
  const db = await openDB();
  try {
    // Clear and rewrite atomically.
    await new Promise((resolve, reject) => {
      const t = tx(db, [STORE_FRAMES, STORE_META], "readwrite");
      const fStore = t.objectStore(STORE_FRAMES);
      const mStore = t.objectStore(STORE_META);
      fStore.clear();
      mStore.clear();
      payload.frames.forEach((blob, i) => {
        const meta = payload.meta?.[i] ?? { index: i };
        fStore.put({ index: i, blob, meta });
      });
      mStore.put({
        id: META_KEY,
        capturedAt: Date.now(),
        sceneMode: payload.sceneMode || "portrait",
        panoramaUrl: payload.panoramaUrl || null,
        count: payload.frames.length,
      });
      t.oncomplete = () => resolve();
      t.onerror = () => reject(t.error);
      t.onabort = () => reject(t.error || new Error("tx aborted"));
    });
  } finally {
    db.close();
  }
}

/**
 * Cheap probe — returns the meta record without loading frame blobs.
 * Used by Step 4 to decide whether to render the "reuse" chip.
 *
 * @returns {Promise<null | {capturedAt:number, sceneMode:string, count:number,
 *   panoramaUrl:string|null, ageMs:number, freshEnough:boolean}>}
 */
export async function getCapturedMeta() {
  let db;
  try { db = await openDB(); } catch { return null; }
  try {
    const t = tx(db, [STORE_META], "readonly");
    const rec = await promisify(t.objectStore(STORE_META).get(META_KEY));
    if (!rec || !rec.capturedAt || !rec.count) return null;
    const ageMs = Date.now() - rec.capturedAt;
    return {
      ...rec,
      ageMs,
      freshEnough: ageMs <= MAX_AGE_MS,
    };
  } catch {
    return null;
  } finally {
    db.close();
  }
}

/**
 * Load every cached frame (in index order) plus meta.
 *
 * @returns {Promise<null | {
 *   frames:{index:number, blob:Blob, meta:object}[],
 *   capturedAt:number, sceneMode:string, panoramaUrl:string|null,
 * }>}
 */
export async function loadCapturedFrames() {
  let db;
  try { db = await openDB(); } catch { return null; }
  try {
    const t = tx(db, [STORE_FRAMES, STORE_META], "readonly");
    const meta = await promisify(t.objectStore(STORE_META).get(META_KEY));
    if (!meta) return null;
    const frames = await promisify(t.objectStore(STORE_FRAMES).getAll());
    if (!frames?.length) return null;
    frames.sort((a, b) => a.index - b.index);
    return {
      frames,
      capturedAt: meta.capturedAt,
      sceneMode: meta.sceneMode,
      panoramaUrl: meta.panoramaUrl || null,
    };
  } catch {
    return null;
  } finally {
    db.close();
  }
}

export async function clearCapturedFrames() {
  let db;
  try { db = await openDB(); } catch { return; }
  try {
    await new Promise((resolve, reject) => {
      const t = tx(db, [STORE_FRAMES, STORE_META], "readwrite");
      t.objectStore(STORE_FRAMES).clear();
      t.objectStore(STORE_META).clear();
      t.oncomplete = () => resolve();
      t.onerror = () => reject(t.error);
    });
  } finally {
    db.close();
  }
}

/** "5 分钟前 / 2 小时前 / 1 天前" — used in the reuse chip. */
export function relativeTime(ageMs) {
  if (!Number.isFinite(ageMs) || ageMs < 0) return "";
  const sec = Math.floor(ageMs / 1000);
  if (sec < 60) return "刚刚";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} 分钟前`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} 小时前`;
  const day = Math.floor(hr / 24);
  return `${day} 天前`;
}
