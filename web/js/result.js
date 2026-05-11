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

  // v9 UX polish #19 — demo / mock responses are *not* AI output. Pin a
  // banner above the shots so a first-time visitor can never mistake
  // the canned content for a real recommendation.
  const isDemo =
    params.get("demo") === "v7" ||
    /^mock(-\d+)?$/i.test(String(response.model || "")) ||
    response?.debug?.mode === "mock";
  if (isDemo) {
    const demoBanner = document.createElement("div");
    demoBanner.className = "demo-banner";
    demoBanner.innerHTML = `
      <span class="demo-banner-dot" aria-hidden="true">●</span>
      <span class="demo-banner-text">
        这是 <b>示范数据</b>，用来体验交互——回首页录一段真实环境就能拿到 AI 真实方案。
      </span>
      <a class="demo-banner-cta" href="/web/welcome.html">回首页录一段</a>
    `;
    content.parentElement?.insertBefore(demoBanner, content);
  }

  renderResult(content, response);
}

back.addEventListener("click", () => (location.href = "/web/capture.html"));
retake.addEventListener("click", () => (location.href = "/web/capture.html"));
