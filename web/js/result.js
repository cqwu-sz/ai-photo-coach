import { loadResult, saveResult } from "./store.js";
import { renderResult } from "./render.js";

const content = document.getElementById("content");
const back = document.getElementById("back-btn");
const retake = document.getElementById("retake-btn");
const modelBadge = document.getElementById("model-badge");

// v7 demo hook — visiting result.html?demo=v7 (no wizard data needed)
// injects the same fixture used by the snapshot script so reviewers
// can land directly on the 3D shot preview.
const params = new URLSearchParams(location.search);
if (params.get("demo") === "v7") {
  const { DEMO_RESPONSE_V7 } = await import("./demo_data.js");
  saveResult(DEMO_RESPONSE_V7);
  // Pretend we're a returning user with one preset already picked so
  // the 3D scene loads the RPM avatar instead of the procedural one.
  try {
    if (!localStorage.getItem("apc.avatarPicks")) {
      localStorage.setItem("apc.avatarPicks", JSON.stringify(["female_youth_18"]));
    }
  } catch {}
}

const response = loadResult();
if (!response) {
  content.innerHTML = `<div class="empty-state">没有结果数据，回到首页重新拍摄。</div>`;
} else {
  modelBadge.textContent = `model: ${response.model || "?"}`;
  renderResult(content, response);
}

back.addEventListener("click", () => (location.href = "/web/capture.html"));
retake.addEventListener("click", () => (location.href = "/web/capture.html"));
