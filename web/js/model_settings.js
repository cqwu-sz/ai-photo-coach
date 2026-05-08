// Model settings drawer.
//
// Shows the BYOK form for picking a vision model and (optionally) feeding
// the user's own API key. The key never leaves the browser unless the user
// runs an analysis. We write to localStorage so picks survive a refresh.
//
// Public API:
//   openModelSettings()         -> async, lazy-fetches /models the first time
//   getActiveModelConfig()      -> { model_id, api_key, base_url }
//   getActiveModelDisplayLabel() -> human-readable label for the badge

import { loadModelConfig, saveModelConfig, clearModelConfig } from "./store.js";

let _cachedRegistry = null; // { default_model_id, enable_byok, models: [...] }
let _drawerEl = null;

const VENDOR_LABELS = {
  google: "Google · Gemini",
  openai: "OpenAI",
  zhipu: "智谱 · GLM",
  dashscope: "阿里 · 通义千问",
  deepseek: "DeepSeek",
  moonshot: "Moonshot · Kimi",
  custom: "自定义",
};

const VENDOR_KEY_HINT = {
  google: "https://aistudio.google.com/app/apikey 申请",
  openai: "https://platform.openai.com/api-keys 申请",
  zhipu: "https://open.bigmodel.cn/usercenter/apikeys 申请",
  dashscope: "https://dashscope.console.aliyun.com/apiKey 申请",
  deepseek: "https://platform.deepseek.com/api_keys 申请",
  moonshot: "https://platform.moonshot.cn/console/api-keys 申请",
};

async function fetchRegistry() {
  if (_cachedRegistry) return _cachedRegistry;
  const r = await fetch("/models");
  if (!r.ok) throw new Error("无法加载模型列表");
  _cachedRegistry = await r.json();
  return _cachedRegistry;
}

export function getActiveModelConfig() {
  const stored = loadModelConfig();
  return {
    model_id: stored.model_id || "",
    api_key: stored.api_key || "",
    base_url: stored.base_url || "",
  };
}

/** Update the page badge with the current model name. */
export async function refreshModeBadge(badgeEl) {
  if (!badgeEl) return;
  const cfg = getActiveModelConfig();
  try {
    const reg = await fetchRegistry();
    const target = cfg.model_id || reg.default_model_id;
    const found = (reg.models || []).find((m) => m.id === target);
    badgeEl.textContent = found ? found.display_name : (target || "default");
    badgeEl.title = found
      ? `${VENDOR_LABELS[found.vendor] || found.vendor} · ${found.notes || ""}`
      : "";
  } catch {
    badgeEl.textContent = cfg.model_id || "default";
  }
}

export async function openModelSettings() {
  if (_drawerEl) {
    _drawerEl.classList.add("open");
    return;
  }
  const reg = await fetchRegistry().catch(() => ({
    default_model_id: "gemini-2.5-flash",
    enable_byok: true,
    models: [],
  }));
  _drawerEl = renderDrawer(reg);
  document.body.appendChild(_drawerEl);
  // next frame so the CSS transition fires
  requestAnimationFrame(() => _drawerEl.classList.add("open"));
}

function renderDrawer(reg) {
  const stored = loadModelConfig();
  const drawer = document.createElement("aside");
  drawer.className = "settings-drawer";
  drawer.innerHTML = `
    <div class="drawer-backdrop" data-close></div>
    <div class="drawer-panel" role="dialog" aria-modal="true" aria-label="模型设置">
      <header class="drawer-head">
        <h3>模型与密钥</h3>
        <button class="drawer-close" type="button" aria-label="关闭" data-close>×</button>
      </header>
      <div class="drawer-body">
        <p class="drawer-note">
          密钥仅保存在你的浏览器（localStorage），分析时随请求发给后端，不会被存盘。
          ${reg.enable_byok ? "" : "（当前后端关闭了 BYOK，密钥会被忽略）"}
        </p>

        <label class="form-label">视觉模型</label>
        <select class="form-input" data-field="model_id">
          <option value="">使用后端默认 (${reg.default_model_id || "gemini-2.5-flash"})</option>
          ${groupOptions(reg.models || [], stored.model_id)}
        </select>

        <label class="form-label">API Key</label>
        <input
          type="password"
          class="form-input"
          data-field="api_key"
          placeholder="留空则使用后端 fallback key"
          autocomplete="off"
          value="${escapeAttr(stored.api_key || "")}"
        />
        <p class="form-hint" data-key-hint></p>

        <label class="form-label">自定义 Base URL（可选）</label>
        <input
          type="text"
          class="form-input"
          data-field="base_url"
          placeholder="留空使用预设地址（仅自定义代理时用）"
          value="${escapeAttr(stored.base_url || "")}"
        />

        <div class="drawer-actions">
          <button class="btn secondary" type="button" data-action="test">测试连通性</button>
          <button class="btn" type="button" data-action="save">保存</button>
        </div>
        <p class="drawer-result" data-result></p>
        <button class="btn-link" type="button" data-action="clear">清除本地保存</button>
      </div>
    </div>
  `;

  const select = drawer.querySelector('[data-field="model_id"]');
  const keyInput = drawer.querySelector('[data-field="api_key"]');
  const baseInput = drawer.querySelector('[data-field="base_url"]');
  const keyHint = drawer.querySelector("[data-key-hint]");
  const result = drawer.querySelector("[data-result]");

  function syncHint() {
    const id = select.value;
    const m = (reg.models || []).find((x) => x.id === id);
    if (!m) {
      keyHint.textContent = "未选择具体模型时使用后端默认与 fallback key.";
      return;
    }
    const lines = [`Vendor: ${VENDOR_LABELS[m.vendor] || m.vendor}`];
    if (VENDOR_KEY_HINT[m.vendor]) lines.push(VENDOR_KEY_HINT[m.vendor]);
    if (m.has_operator_key) {
      lines.push("（后端已配置该家的 fallback key，留空也可用）");
    } else if (m.requires_key) {
      lines.push("（后端没有 fallback，必须填你自己的 key）");
    }
    keyHint.textContent = lines.join(" · ");
    if (m.base_url) baseInput.placeholder = m.base_url;
  }
  syncHint();
  select.addEventListener("change", syncHint);

  drawer.addEventListener("click", async (e) => {
    const closer = e.target.closest("[data-close]");
    if (closer) {
      drawer.classList.remove("open");
      setTimeout(() => drawer.remove(), 200);
      _drawerEl = null;
      return;
    }
    const action = e.target.dataset.action;
    if (action === "save") {
      saveModelConfig({
        model_id: select.value,
        api_key: keyInput.value,
        base_url: baseInput.value,
      });
      result.textContent = "已保存。下次分析将使用该配置。";
      result.className = "drawer-result ok";
      const badge = document.getElementById("mode-badge");
      if (badge) refreshModeBadge(badge);
    } else if (action === "clear") {
      clearModelConfig();
      keyInput.value = "";
      baseInput.value = "";
      select.value = "";
      result.textContent = "已清除。";
      result.className = "drawer-result";
    } else if (action === "test") {
      result.textContent = "测试中…";
      result.className = "drawer-result";
      try {
        const r = await fetch("/models/test", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            model_id: select.value || reg.default_model_id,
            api_key: keyInput.value || null,
            base_url: baseInput.value || null,
          }),
        });
        const body = await r.json();
        if (body.ok) {
          result.textContent = "连通成功 · " + (body.snippet || "");
          result.className = "drawer-result ok";
        } else {
          result.textContent = "失败：" + (body.error || "");
          result.className = "drawer-result err";
        }
      } catch (err) {
        result.textContent = "请求失败：" + err.message;
        result.className = "drawer-result err";
      }
    }
  });

  return drawer;
}

function groupOptions(models, selectedId) {
  const groups = {};
  for (const m of models) {
    if (!groups[m.vendor]) groups[m.vendor] = [];
    groups[m.vendor].push(m);
  }
  const order = ["google", "openai", "zhipu", "dashscope", "deepseek", "moonshot"];
  const html = [];
  for (const v of order) {
    if (!groups[v]) continue;
    html.push(`<optgroup label="${escapeAttr(VENDOR_LABELS[v] || v)}">`);
    for (const m of groups[v]) {
      const sel = m.id === selectedId ? " selected" : "";
      html.push(
        `<option value="${escapeAttr(m.id)}"${sel}>${escapeAttr(m.display_name)}${m.has_operator_key ? "" : " ⚠"}</option>`,
      );
    }
    html.push("</optgroup>");
  }
  return html.join("");
}

function escapeAttr(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
