/**
 * Tiny IndexedDB wrapper for the reference-image library.
 *
 * Stores user-picked sample photos as Blobs (so we don't bloat the JSON
 * profile). Each record:
 *   {
 *     id: number (auto),
 *     blob: Blob (image/jpeg|png),
 *     thumbDataUrl: string (small data URL for grid),
 *     name: string,
 *     addedAt: number (ms epoch),
 *     bytes: number,
 *   }
 *
 * Why not localStorage? Browsers cap localStorage at ~5 MB and force
 * base64 strings; IndexedDB happily stores large Blobs and survives a
 * tab close.
 */

const DB_NAME = "aipc-references";
const DB_VERSION = 1;
const STORE = "refs";
export const REF_LIMIT = 8;
export const THUMB_PX = 220;

function open() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: "id", autoIncrement: true });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function tx(mode, fn) {
  const db = await open();
  return new Promise((resolve, reject) => {
    const t = db.transaction(STORE, mode);
    const store = t.objectStore(STORE);
    const result = fn(store);
    t.oncomplete = () => resolve(result);
    t.onerror = () => reject(t.error);
    t.onabort = () => reject(t.error);
  });
}

export async function listReferences() {
  const db = await open();
  return new Promise((resolve, reject) => {
    const t = db.transaction(STORE, "readonly");
    const req = t.objectStore(STORE).getAll();
    req.onsuccess = () => resolve(req.result || []);
    req.onerror = () => reject(req.error);
  });
}

export async function countReferences() {
  const db = await open();
  return new Promise((resolve, reject) => {
    const t = db.transaction(STORE, "readonly");
    const req = t.objectStore(STORE).count();
    req.onsuccess = () => resolve(req.result || 0);
    req.onerror = () => reject(req.error);
  });
}

export async function addReference(file) {
  if (!file || !file.type.startsWith("image/")) {
    throw new Error("不是图片文件");
  }
  const have = await countReferences();
  if (have >= REF_LIMIT) {
    throw new Error(`参考图最多 ${REF_LIMIT} 张，先删几张吧`);
  }
  const blob = await downscaleAsJpeg(file, 1280, 0.82);
  const thumbDataUrl = await downscaleToDataUrl(blob, THUMB_PX, 0.78);
  const record = {
    blob,
    thumbDataUrl,
    name: file.name || `ref_${Date.now()}.jpg`,
    addedAt: Date.now(),
    bytes: blob.size,
  };
  return tx("readwrite", (store) => {
    store.add(record);
  });
}

export async function removeReference(id) {
  return tx("readwrite", (store) => {
    store.delete(id);
  });
}

export async function clearReferences() {
  return tx("readwrite", (store) => {
    store.clear();
  });
}

/**
 * Helper for capture.js: returns Blobs in DB insertion order, ready to
 * append to the multipart `reference_thumbnails` field.
 */
export async function getReferenceBlobs() {
  const items = await listReferences();
  return items.map((it) => it.blob);
}

// ---------------------------------------------------------------------------
// Image utilities
// ---------------------------------------------------------------------------

function loadImage(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("decode failed"));
    img.src = src;
  });
}

async function fileToObjectURL(file) {
  return URL.createObjectURL(file);
}

async function downscaleAsJpeg(file, maxLongSide, quality) {
  const url = await fileToObjectURL(file);
  try {
    const img = await loadImage(url);
    const { canvas } = drawDownscaled(img, maxLongSide);
    const blob = await new Promise((resolve) =>
      canvas.toBlob(resolve, "image/jpeg", quality),
    );
    return blob;
  } finally {
    URL.revokeObjectURL(url);
  }
}

async function downscaleToDataUrl(blob, maxLongSide, quality) {
  const url = URL.createObjectURL(blob);
  try {
    const img = await loadImage(url);
    const { canvas } = drawDownscaled(img, maxLongSide);
    return canvas.toDataURL("image/jpeg", quality);
  } finally {
    URL.revokeObjectURL(url);
  }
}

function drawDownscaled(img, maxLongSide) {
  const w0 = img.naturalWidth;
  const h0 = img.naturalHeight;
  const scale = Math.min(1, maxLongSide / Math.max(w0, h0));
  const w = Math.round(w0 * scale);
  const h = Math.round(h0 * scale);
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(img, 0, 0, w, h);
  return { canvas };
}
