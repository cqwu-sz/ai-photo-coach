// Style picker — visual cards with foldable reference photos.
//
// Replaces the earlier "input + 5 English chip" UX. The user picks
// up to 2 style presets by clicking cards; clicking "+N 张参考" expands
// a drawer below the grid showing 4-6 Unsplash reference photos
// (a mix of portrait + scenery) so they actually understand what each
// keyword set means.
//
// Selected presets are written back to the existing #style-input as
// a comma-separated list of keywords (e.g. "cinematic, moody"), so
// the rest of the wizard / backend keeps working unchanged.

const MANIFEST_URL = "/web/img/style/manifest.json";
const FEASIBILITY_URL = "/style-feasibility";
const MAX_PICKS = 2;

// In-memory cache of the latest feasibility result per (lat,lon,hour).
// Picker is rebuilt every time the user enters Step 3, but the result
// stays valid for the same hour so we don't hammer Open-Meteo.
const _feasibilityCache = new Map();

async function fetchFeasibility(geoFix, picks = null) {
  if (!geoFix?.lat || !geoFix?.lon) {
    // No geo — server returns "unknown" tier for all 5; we skip the
    // call and treat that as the default state.
    return null;
  }
  const hour = new Date().toISOString().slice(0, 13);   // YYYY-MM-DDTHH
  const pickKey = (picks && picks.length) ? picks.join(",") : "_";
  const key = `${geoFix.lat.toFixed(3)}|${geoFix.lon.toFixed(3)}|${hour}|${pickKey}`;
  if (_feasibilityCache.has(key)) return _feasibilityCache.get(key);
  try {
    let url = `${FEASIBILITY_URL}?lat=${geoFix.lat}&lon=${geoFix.lon}`;
    if (picks && picks.length) url += `&picks=${encodeURIComponent(picks.join(","))}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`http ${res.status}`);
    const data = await res.json();
    _feasibilityCache.set(key, data);
    return data;
  } catch (err) {
    console.warn("[style-picker] feasibility fetch failed (non-fatal)", err);
    return null;
  }
}

export async function initStylePicker({
  gridHost,
  drawerHost,
  input,
  onChange,
  geoFix = null,        // optional: cached fix from web/js/geo.js
}) {
  if (!gridHost) return null;

  let manifest;
  try {
    const res = await fetch(MANIFEST_URL, { cache: "force-cache" });
    if (!res.ok) throw new Error(`manifest http ${res.status}`);
    manifest = await res.json();
  } catch (err) {
    console.warn("[style-picker] manifest load failed", err);
    return null;
  }

  const styles = Array.isArray(manifest.styles) ? manifest.styles : [];
  if (!styles.length) return null;

  const picks = new Set();
  let activeDrawer = null;

  // Pre-fill from existing input value (returning user / Step-4 jump back)
  if (input?.value) {
    const tokens = tokenize(input.value);
    for (const s of styles) {
      const sk = (s.keywords || []).map((k) => k.toLowerCase());
      if (sk.some((k) => tokens.includes(k))) picks.add(s.id);
    }
  }

  function tokenize(value) {
    return (value || "")
      .split(/[,，;；]/)
      .map((s) => s.trim().toLowerCase())
      .filter(Boolean);
  }

  function syncInput() {
    if (!input) return;
    const tokens = [];
    for (const s of styles) {
      if (picks.has(s.id)) tokens.push(...(s.keywords || []));
    }
    // Mark the dispatched event as picker-originated so our own
    // input listener doesn't re-derive picks from this same value.
    input.dataset.fromPicker = "1";
    input.value = tokens.join(", ");
    input.dispatchEvent(new Event("input", { bubbles: true }));
    delete input.dataset.fromPicker;
  }

  function togglePick(id) {
    if (picks.has(id)) {
      picks.delete(id);
    } else {
      if (picks.size >= MAX_PICKS) {
        // Drop the oldest pick so the click still feels responsive
        // — beats silently ignoring it when the cap is reached.
        const first = picks.values().next().value;
        picks.delete(first);
      }
      picks.add(id);
    }
    syncInput();
    refreshSelected();
    refreshDrawerHighlight();
    refreshBetterTimeBanner();
    onChange?.(getSelected());
  }

  function refreshSelected() {
    const cards = gridHost.querySelectorAll(".style-card");
    cards.forEach((c) => {
      c.classList.toggle("is-active", picks.has(c.dataset.styleId));
    });
  }

  function refreshDrawerHighlight() {
    const cards = gridHost.querySelectorAll(".style-card");
    cards.forEach((c) => {
      c.classList.toggle("is-expanded", c.dataset.styleId === activeDrawer);
    });
    const drawerPick = drawerHost?.querySelector(".style-drawer-pick");
    if (drawerPick && activeDrawer) {
      const isPicked = picks.has(activeDrawer);
      drawerPick.textContent = isPicked ? "✓ 已选这个风格" : "选这个风格";
      drawerPick.classList.toggle("is-picked", isPicked);
    }
  }

  function openDrawer(styleId) {
    if (!drawerHost) return;
    if (activeDrawer === styleId) {
      closeDrawer();
      return;
    }
    const style = styles.find((s) => s.id === styleId);
    if (!style) return;
    drawerHost.innerHTML = renderDrawer(style);
    drawerHost.hidden = false;
    drawerHost
      .querySelector(".style-drawer-close")
      ?.addEventListener("click", closeDrawer);
    drawerHost
      .querySelector(".style-drawer-pick")
      ?.addEventListener("click", () => togglePick(styleId));
    activeDrawer = styleId;
    refreshDrawerHighlight();
    // Scroll the drawer into view on smaller screens; harmless on desktop.
    requestAnimationFrame(() => {
      drawerHost.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  }

  function closeDrawer() {
    if (!drawerHost) return;
    drawerHost.hidden = true;
    drawerHost.innerHTML = "";
    activeDrawer = null;
    refreshDrawerHighlight();
  }

  function renderCard(style) {
    const main = style.images?.[0];
    const more = Math.max(0, (style.images?.length || 1) - 1);
    const moreText = more > 0 ? `+${more} 张参考 →` : "查看参考 →";
    return `
      <div class="style-card" data-style-id="${style.id}">
        <button type="button" class="style-card-pick" data-pick="${style.id}"
                aria-label="选择 ${style.label_zh} 风格">
          <span class="style-card-img"
                style="background-image:url('/web/img/style/${style.id}/${main?.file ?? "01.jpg"}')"></span>
          <span class="style-card-check" aria-hidden="true">✓</span>
        </button>
        <div class="style-card-meta">
          <div class="style-card-title">
            <strong>${style.label_zh}</strong>
          </div>
          <p class="style-card-blurb">${style.blurb_zh}</p>
        </div>
        <button type="button" class="style-card-more" data-more="${style.id}">
          ${moreText}
        </button>
      </div>
    `;
  }

  function renderDrawer(style) {
    const isPicked = picks.has(style.id);
    const thumbs = (style.images || [])
      .map(
        (im) => `
      <a class="style-thumb" href="https://unsplash.com/photos/${im.id}"
         target="_blank" rel="noopener" title="去 Unsplash 看原图与作者">
        <img src="/web/img/style/${style.id}/${im.file}"
             alt="${style.label_zh} · ${im.kind === "scenery" ? "环境" : "人像"}"
             loading="lazy" />
        <span class="style-thumb-tag">${im.kind === "scenery" ? "环境" : "人像"}</span>
      </a>
    `,
      )
      .join("");
    return `
      <div class="style-drawer-inner">
        <div class="style-drawer-head">
          <div class="style-drawer-title-wrap">
            <strong class="style-drawer-title">${style.label_zh}</strong>
          </div>
          <button type="button" class="style-drawer-pick ${isPicked ? "is-picked" : ""}">
            ${isPicked ? "✓ 已选这个风格" : "选这个风格"}
          </button>
          <button type="button" class="style-drawer-close" aria-label="关闭预览">×</button>
        </div>
        <p class="style-drawer-desc">${style.summary_long_zh ?? style.blurb_zh}</p>
        <div class="style-drawer-thumbs">${thumbs}</div>
        <p class="style-drawer-credit">
          参考图来自 <a href="https://unsplash.com/license" target="_blank" rel="noopener">Unsplash</a> ·
          仅展示风格倾向，不代表你的实际出片
        </p>
      </div>
    `;
  }

  function getSelected() {
    return styles.filter((s) => picks.has(s.id));
  }

  // Initial render
  gridHost.innerHTML = styles.map(renderCard).join("");
  refreshSelected();

  // Background fetch the feasibility verdict (no GPS prompt — we only
  // use a previously cached fix) and decorate cards once it lands.
  let lastScores = null;     // cached for the better-time banner logic
  if (geoFix) {
    fetchFeasibility(geoFix).then((data) => {
      if (data?.scores) {
        lastScores = data.scores;
        applyFeasibility(data.scores);
        refreshBetterTimeBanner();
      }
    });
  }

  // ── Better-time banner ──
  // Only shown when the user has picked styles AND every picked style
  // currently scores < 0.5 (i.e. environment is genuinely bad). Calls
  // /style-feasibility with `picks=` so the server returns its
  // suggest_better_time result. Banner sits above the grid.
  let bannerEl = null;
  function ensureBannerHost() {
    if (bannerEl) return bannerEl;
    bannerEl = document.createElement("div");
    bannerEl.className = "style-better-time-banner";
    bannerEl.hidden = true;
    gridHost.parentNode?.insertBefore(bannerEl, gridHost);
    return bannerEl;
  }

  async function refreshBetterTimeBanner() {
    if (!geoFix) return;
    const host = ensureBannerHost();
    const pickedIds = [...picks];
    if (!pickedIds.length || !lastScores) {
      host.hidden = true;
      return;
    }
    const pickedScores = lastScores.filter((s) => pickedIds.includes(s.style_id));
    const allBad = pickedScores.length > 0 && pickedScores.every((s) => s.score < 0.5);
    if (!allBad) {
      host.hidden = true;
      return;
    }
    const data = await fetchFeasibility(geoFix, pickedIds);
    if (!data?.better_time) {
      host.hidden = true;
      return;
    }
    const bt = data.better_time;
    host.innerHTML = `
      <div class="sbt-icon" aria-hidden="true">⏱</div>
      <div class="sbt-body">
        <strong>当前环境对你选的风格不太友好</strong>
        <p>${bt.reason_zh}</p>
      </div>
    `;
    host.hidden = false;
  }

  function applyFeasibility(scores) {
    const byId = new Map(scores.map((s) => [s.style_id, s]));
    gridHost.querySelectorAll(".style-card").forEach((card) => {
      const sid = card.dataset.styleId;
      const v = byId.get(sid);
      if (!v) return;
      card.classList.remove(
        "tier-recommended", "tier-marginal", "tier-discouraged", "tier-unknown",
      );
      card.classList.add(`tier-${v.tier}`);
      card.dataset.feasibilityScore = String(v.score);
      card.dataset.feasibilityReason = v.reason_zh;
      // Inject a small badge into the card meta when not "unknown".
      let badge = card.querySelector(".style-card-badge");
      if (!badge) {
        badge = document.createElement("div");
        badge.className = "style-card-badge";
        card.querySelector(".style-card-meta")?.appendChild(badge);
      }
      if (v.tier === "discouraged") {
        badge.textContent = "⚠ " + v.reason_zh;
        badge.dataset.tier = "discouraged";
      } else if (v.tier === "marginal") {
        badge.textContent = "△ " + v.reason_zh;
        badge.dataset.tier = "marginal";
      } else if (v.tier === "recommended") {
        badge.textContent = "✓ " + v.reason_zh;
        badge.dataset.tier = "recommended";
      } else {
        badge.remove();
      }
    });
  }

  gridHost.addEventListener("click", (e) => {
    const pickBtn = e.target.closest("[data-pick]");
    if (pickBtn) {
      togglePick(pickBtn.dataset.pick);
      return;
    }
    const moreBtn = e.target.closest("[data-more]");
    if (moreBtn) {
      openDrawer(moreBtn.dataset.more);
    }
  });

  // External edits to the input (custom keyword typing) should
  // visually keep the cards in sync — if the user manually types
  // "cinematic, moody" we want that card to light up too.
  input?.addEventListener("input", () => {
    if (input.dataset.fromPicker === "1") return;
    const tokens = tokenize(input.value);
    let changed = false;
    for (const s of styles) {
      const sk = (s.keywords || []).map((k) => k.toLowerCase());
      const hit = sk.some((k) => tokens.includes(k));
      if (hit && !picks.has(s.id)) {
        picks.add(s.id);
        changed = true;
      } else if (!hit && picks.has(s.id)) {
        picks.delete(s.id);
        changed = true;
      }
    }
    if (changed) {
      refreshSelected();
      refreshDrawerHighlight();
      onChange?.(getSelected());
    }
  });

  return {
    getSelected,
    setSelected(ids) {
      picks.clear();
      for (const id of ids) picks.add(id);
      syncInput();
      refreshSelected();
      refreshDrawerHighlight();
    },
  };
}
