import { loadResult } from "./store.js";
import { renderResult } from "./render.js";

const content = document.getElementById("content");
const back = document.getElementById("back-btn");
const retake = document.getElementById("retake-btn");
const modelBadge = document.getElementById("model-badge");

const response = loadResult();
if (!response) {
  content.innerHTML = `<div class="empty-state">没有结果数据，回到首页重新拍摄。</div>`;
} else {
  modelBadge.textContent = `model: ${response.model || "?"}`;
  renderResult(content, response);
}

back.addEventListener("click", () => (location.href = "/web/capture.html"));
retake.addEventListener("click", () => (location.href = "/web/capture.html"));
