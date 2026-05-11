// post_process.js (W10.4)
//
// Web修图: 8 个滤镜 (CSS-style + Canvas pixel ops) + 5 个美颜旋钮.
// Pure Canvas2D — no WebGL needed for the demo. Real LUT support could
// be wired via WebGL later; for now we approximate the iOS preset look
// with brightness / contrast / saturation / sepia / hue-rotate matrix
// operations applied per-pixel.

const PRESETS = [
  { id: "original",    label: "原图",     fn: identity },
  { id: "cinematic",   label: "电影感",   fn: chain([{ contrast: 1.15 }, { saturate: 0.85 }, { temp: +800 }]) },
  { id: "filmWarm",    label: "胶片暖",   fn: chain([{ brightness: 0.05 }, { temp: -500 }, { saturate: 0.92 }]) },
  { id: "streetCool",  label: "街拍冷调", fn: chain([{ contrast: 1.1 }, { temp: +2300 }, { saturate: 0.7 }]) },
  { id: "cleanBright", label: "干净亮调", fn: chain([{ brightness: 0.1 }, { contrast: 1.05 }, { saturate: 0.92 }]) },
  { id: "bw",          label: "黑白",     fn: monochrome },
  { id: "japanCrisp",  label: "日系小清新", fn: chain([{ brightness: 0.08 }, { saturate: 0.85 }, { temp: +800 }]) },
  { id: "retroFade",   label: "复古褪色", fn: chain([{ contrast: 0.92 }, { saturate: 0.78 }, { fade: 0.18 }]) },
  { id: "hkVibe",      label: "港风",     fn: chain([{ contrast: 1.2 }, { saturate: 1.05 }, { temp: -300 }, { vignette: 0.4 }]) },
];

const SLIDERS = [
  { id: "smooth",      label: "磨皮" },
  { id: "brighten",    label: "美白" },
  { id: "slim",        label: "瘦脸（实验）" },
  { id: "enlargeEye",  label: "大眼（实验）" },
  { id: "brightenEye", label: "亮眼" },
];

const state = {
  presetId: "original",
  beauty: Object.fromEntries(SLIDERS.map(s => [s.id, 0])),
  baseImage: null,
};

const canvas = document.getElementById("pp-canvas");
const ctx = canvas.getContext("2d");
const msg = document.getElementById("pp-msg");

document.getElementById("pp-presets").innerHTML = PRESETS.map(p =>
  `<button data-id="${p.id}" class="${p.id === state.presetId ? "active" : ""}">${p.label}</button>`
).join("");
document.getElementById("pp-presets").addEventListener("click", (e) => {
  const id = e.target.dataset.id;
  if (!id) return;
  state.presetId = id;
  for (const b of e.currentTarget.querySelectorAll("button")) {
    b.classList.toggle("active", b.dataset.id === id);
  }
  rerender();
});

document.getElementById("pp-sliders").innerHTML = SLIDERS.map(s =>
  `<label>${s.label}<input type="range" min="0" max="100" value="0" data-id="${s.id}"><span data-out="${s.id}">0</span></label>`
).join("");
document.getElementById("pp-sliders").addEventListener("input", (e) => {
  const id = e.target.dataset.id;
  if (!id) return;
  state.beauty[id] = Number(e.target.value) / 100;
  document.querySelector(`[data-out="${id}"]`).textContent = e.target.value;
  rerender();
});

document.getElementById("pp-reset").addEventListener("click", () => {
  state.presetId = "original";
  state.beauty = Object.fromEntries(SLIDERS.map(s => [s.id, 0]));
  for (const i of document.querySelectorAll('input[type=range]')) i.value = 0;
  for (const o of document.querySelectorAll('[data-out]')) o.textContent = "0";
  document.querySelectorAll(".pp-presets button").forEach(b =>
    b.classList.toggle("active", b.dataset.id === "original"));
  rerender();
});
document.getElementById("pp-open").addEventListener("click", () => {
  document.getElementById("pp-file").click();
});
document.getElementById("pp-file").addEventListener("change", async (e) => {
  const file = e.target.files?.[0];
  if (!file) return;
  const url = URL.createObjectURL(file);
  const img = new Image();
  img.onload = () => {
    state.baseImage = img;
    fitCanvas(img);
    rerender();
    URL.revokeObjectURL(url);
  };
  img.src = url;
});
document.getElementById("pp-save").addEventListener("click", () => {
  if (!state.baseImage) { msg.textContent = "请先选择图片"; return; }
  const link = document.createElement("a");
  link.href = canvas.toDataURL("image/jpeg", 0.92);
  link.download = "edited.jpg";
  link.click();
  msg.textContent = "已下载副本";
  // P1-7.1 telemetry — fire and forget.
  postProcessTelemetry();
});

function postProcessTelemetry() {
  try {
    const analyzeRequestId = window.__lastAnalyzeRequestId || null;
    fetch("/feedback/post_process", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        analyze_request_id: analyzeRequestId,
        preset_id: state.presetId,
        ...state.beauty,
      }),
      keepalive: true,
    }).catch(() => {});
  } catch (e) {
    /* never blocks the user */
  }
}

function fitCanvas(img) {
  const ratio = img.width / img.height;
  const maxW = 720;
  canvas.width  = Math.min(img.width, maxW);
  canvas.height = Math.round(canvas.width / ratio);
}

function rerender() {
  if (!state.baseImage) return;
  ctx.drawImage(state.baseImage, 0, 0, canvas.width, canvas.height);
  const preset = PRESETS.find(p => p.id === state.presetId) || PRESETS[0];
  const data = ctx.getImageData(0, 0, canvas.width, canvas.height);
  preset.fn(data);
  applyBeauty(data, state.beauty);
  ctx.putImageData(data, 0, 0);
}

// ---- pixel ops -------------------------------------------------------
function identity(_data) { /* no-op */ }
function monochrome(data) {
  const d = data.data;
  for (let i = 0; i < d.length; i += 4) {
    const y = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
    d[i] = d[i + 1] = d[i + 2] = y;
  }
}
function chain(ops) {
  return (data) => { for (const op of ops) applyOp(data, op); };
}
function applyOp(data, op) {
  const d = data.data;
  if ("brightness" in op) {
    const v = op.brightness * 255;
    for (let i = 0; i < d.length; i += 4) {
      d[i] = clamp(d[i] + v); d[i+1] = clamp(d[i+1] + v); d[i+2] = clamp(d[i+2] + v);
    }
  }
  if ("contrast" in op) {
    const c = op.contrast;
    for (let i = 0; i < d.length; i += 4) {
      d[i]   = clamp((d[i]   - 128) * c + 128);
      d[i+1] = clamp((d[i+1] - 128) * c + 128);
      d[i+2] = clamp((d[i+2] - 128) * c + 128);
    }
  }
  if ("saturate" in op) {
    const s = op.saturate;
    for (let i = 0; i < d.length; i += 4) {
      const y = 0.299 * d[i] + 0.587 * d[i+1] + 0.114 * d[i+2];
      d[i]   = clamp(y + (d[i]   - y) * s);
      d[i+1] = clamp(y + (d[i+1] - y) * s);
      d[i+2] = clamp(y + (d[i+2] - y) * s);
    }
  }
  if ("temp" in op) {
    // Positive = cooler (more blue, less red).
    const k = op.temp / 1000;
    for (let i = 0; i < d.length; i += 4) {
      d[i]   = clamp(d[i]   - k * 4);
      d[i+2] = clamp(d[i+2] + k * 4);
    }
  }
  if ("fade" in op) {
    const f = op.fade;
    for (let i = 0; i < d.length; i += 4) {
      d[i]   = clamp(d[i]   * (1 - f) + 220 * f);
      d[i+1] = clamp(d[i+1] * (1 - f) + 215 * f);
      d[i+2] = clamp(d[i+2] * (1 - f) + 200 * f);
    }
  }
  if ("vignette" in op) {
    const w = canvas.width, h = canvas.height;
    const cx = w / 2, cy = h / 2;
    const maxD = Math.hypot(cx, cy);
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const dist = Math.hypot(x - cx, y - cy) / maxD;
        const v = 1 - op.vignette * dist * dist;
        const i = (y * w + x) * 4;
        d[i]   *= v; d[i+1] *= v; d[i+2] *= v;
      }
    }
  }
}
function applyBeauty(data, b) {
  if (b.smooth > 0) {
    // Cheap separable blur — degrades sharpness modestly.
    boxBlur(data, Math.round(1 + 2 * b.smooth));
  }
  if (b.brighten > 0) {
    applyOp(data, { brightness: 0.04 * b.brighten });
    applyOp(data, { saturate: 1 - 0.05 * b.brighten });
  }
  if (b.brightenEye > 0) {
    // No face detection — apply mild global highlights as proxy.
    applyOp(data, { brightness: 0.02 * b.brightenEye });
  }
  // slim / enlargeEye are no-ops here (would require face mesh / mesh warp).
}
function boxBlur(data, radius) {
  if (radius < 1) return;
  const w = canvas.width, h = canvas.height;
  const src = data.data;
  const out = new Uint8ClampedArray(src);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      let r = 0, g = 0, bl = 0, n = 0;
      for (let ky = -radius; ky <= radius; ky++) {
        const yy = y + ky;
        if (yy < 0 || yy >= h) continue;
        for (let kx = -radius; kx <= radius; kx++) {
          const xx = x + kx;
          if (xx < 0 || xx >= w) continue;
          const i = (yy * w + xx) * 4;
          r += src[i]; g += src[i+1]; bl += src[i+2]; n++;
        }
      }
      const i = (y * w + x) * 4;
      out[i] = r / n; out[i+1] = g / n; out[i+2] = bl / n;
    }
  }
  data.data.set(out);
}
function clamp(v) { return v < 0 ? 0 : v > 255 ? 255 : v; }
