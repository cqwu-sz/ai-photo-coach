// User-facing error message normaliser.
//
// v9 UX polish #4 — the original `friendlyError` covered only 3 cases
// (503 / quota / network) and `slice(0, 220)` of the raw payload
// otherwise. That meant production users saw raw JSON / HTTP codes /
// stack traces, which is the single biggest "feels unpolished" leak.
//
// This module:
//   - covers all common backend / model failure modes with calm,
//     actionable Chinese copy
//   - hides raw text in production unless ?debug=1 is set
//   - returns a code so callers can copy the error to clipboard for
//     support without exposing the user to it
//
// Pure JS, no dependencies. Safe to import from any client page.

const PATTERNS = [
  // ── 503 / overload — Gemini & friends frequently throttle ──────────
  {
    code: "BUSY",
    test: /\b503\b|UNAVAILABLE|overloaded|high demand|backend_overloaded|model_overloaded/i,
    msg: "服务当前繁忙，稍等几秒点重试。",
  },
  // ── quota / rate limit ─────────────────────────────────────────────
  {
    code: "QUOTA",
    test: /\bquota\b|RESOURCE_EXHAUSTED|rate.?limit|429|too.?many.?requests/i,
    msg: "今天的免费额度已用完，明天再试，或在设置里填自己的模型 API key。",
  },
  // ── content safety filter — Gemini / OpenAI moderation ─────────────
  {
    code: "SAFETY",
    test: /\bsafety\b|\bblocked\b|RECITATION|content_filter|moderation|prohibited_content/i,
    msg: "AI 觉得这段画面里的内容不太适合处理。换个角度或人少一点的方向再录一段试试。",
  },
  // ── BYOK key invalid / 401 ─────────────────────────────────────────
  {
    code: "BAD_KEY",
    test: /\b401\b|invalid.?(api.?)?key|model_api_key|api_key_invalid|unauthorized|authentication.?failed/i,
    msg: "你填的模型 API key 不对或已失效。去「设置 · 模型」检查一下，或切回拾光默认模型。",
  },
  // ── payload too large ──────────────────────────────────────────────
  {
    code: "TOO_BIG",
    test: /\b413\b|payload.?too.?large|request_entity_too_large|maximum.?upload/i,
    msg: "这次录的画面太多了。把出片速度从「精致」切到「快速」、或少录几秒再试。",
  },
  // ── multipart / form-data — network dropped mid-upload ─────────────
  {
    code: "UPLOAD_BROKEN",
    test: /multipart|form-data|boundary|incomplete.?body|partial_upload|stream_reset/i,
    msg: "上传被中断了，检查一下网络后重试就好。",
  },
  // ── timeout ────────────────────────────────────────────────────────
  {
    code: "TIMEOUT",
    test: /\btimeout\b|timed?.?out|deadline_exceeded|504\b/i,
    msg: "等了太久 AI 还没回。稍等一下重试，或切到「快速出片」模式。",
  },
  // ── network ────────────────────────────────────────────────────────
  {
    code: "NETWORK",
    test: /network|fetch|failed to fetch|net::|ERR_INTERNET|offline/i,
    msg: "网络连接不上，检查 Wi-Fi / 流量后重试。",
  },
  // ── auth / token expired (post-A0 auth integration) ────────────────
  {
    code: "AUTH",
    test: /\bjwt\b|token.?expired|token.?invalid|refresh.?token|403\b/i,
    msg: "登录状态过期了，回首页重新进入一次。",
  },
  // ── server 500 — generic backend bug ───────────────────────────────
  {
    code: "SERVER",
    test: /\b500\b|internal_server_error|unexpected_error|traceback/i,
    msg: "服务端遇到点意外，我们已记下来，稍后再试。",
  },
  // ── client-side analyze precheck failures ──────────────────────────
  {
    code: "FRAMES_FEW",
    test: /提取关键帧失败|keyframe.?failed|too.?few.?frames/i,
    msg: "录的时间太短，请再环视一段（建议 10 秒以上）。",
  },
];

/**
 * Convert a raw error / message into:
 *   { code, message, raw, showRaw }
 *
 *   - code:    short stable id ("BUSY" / "QUOTA" / …) for telemetry
 *   - message: calm Chinese sentence safe to render to any user
 *   - raw:     the original text (kept for debug / copy-to-clipboard)
 *   - showRaw: true only when debug mode is on
 *
 * Always returns a usable object even on null / undefined input.
 */
export function normaliseError(err) {
  const raw = errorToString(err);
  const debug = isDebugMode();

  for (const p of PATTERNS) {
    if (p.test.test(raw)) {
      return { code: p.code, message: p.msg, raw, showRaw: debug };
    }
  }

  // Unmatched — production users see a calm fallback, dev sees the raw.
  return {
    code: "UNKNOWN",
    message: debug ? raw : "出了点意外，已经记下来了，稍后再试。",
    raw,
    showRaw: debug,
  };
}

/** Backwards-compat helper for capture.js's old `friendlyError(raw)`. */
export function friendlyError(raw) {
  return normaliseError(raw).message;
}

/**
 * Build a DOM node that shows the user-facing message + an
 * unobtrusive "复制错误码" button (so a friendly support
 * conversation can still recover the raw text without ever
 * showing it to the casual user).
 */
export function buildErrorView(err, { onCopy } = {}) {
  const norm = normaliseError(err);
  const wrap = document.createElement("div");
  wrap.className = "err-view";

  const msg = document.createElement("p");
  msg.className = "err-view-msg";
  msg.textContent = norm.message;
  wrap.appendChild(msg);

  if (norm.showRaw) {
    const pre = document.createElement("pre");
    pre.className = "err-view-raw";
    pre.textContent = norm.raw;
    wrap.appendChild(pre);
  } else if (norm.raw && norm.raw !== norm.message) {
    const row = document.createElement("div");
    row.className = "err-view-meta";
    const code = document.createElement("span");
    code.className = "err-view-code";
    code.textContent = norm.code;
    row.appendChild(code);
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "err-view-copy";
    copy.textContent = "复制错误码";
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(`${norm.code}: ${norm.raw}`);
        copy.textContent = "已复制 ✓";
        setTimeout(() => (copy.textContent = "复制错误码"), 2000);
      } catch {
        copy.textContent = "复制失败";
      }
      if (typeof onCopy === "function") onCopy(norm);
    });
    row.appendChild(copy);
    wrap.appendChild(row);
  }
  return wrap;
}

function errorToString(err) {
  if (err == null) return "";
  if (typeof err === "string") return err;
  if (err instanceof Error) return err.message || String(err);
  try { return JSON.stringify(err); } catch { return String(err); }
}

function isDebugMode() {
  try {
    if (typeof URLSearchParams === "undefined") return false;
    return new URLSearchParams(location.search).get("debug") === "1";
  } catch { return false; }
}
