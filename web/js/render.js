import { poseThumbnailURL } from "./api.js";
import { renderPoseSVG } from "./pose_viz.js";
import { renderShotMinimap } from "./shot_minimap.js";
import { renderSceneCompose, pickBackdrop } from "./scene_compose.js";
import {
  loadAvatarPicks,
  loadFrames,
  loadPanoramaUrl,
  loadRefInspiration,
  saveCurrentShot,
} from "./store.js";
import { resolveAvatarPicks } from "./avatar_styles.js";

// Mirrors the labelling logic in iOS/RecommendationView.swift so users
// see the same Chinese terminology in either client.

const LIGHTING_LABEL = {
  golden_hour: "黄金时段",
  blue_hour: "蓝调时段",
  harsh_noon: "正午顶光",
  overcast: "阴天",
  shade: "阴影",
  indoor_warm: "室内暖光",
  indoor_cool: "室内冷光",
  low_light: "弱光",
  backlight: "逆光",
  mixed: "混合光",
};

const COMPOSITION_LABEL = {
  rule_of_thirds: "三分线",
  leading_line: "引导线",
  symmetry: "对称",
  frame_within_frame: "框中框",
  negative_space: "负空间",
  centered: "居中",
  diagonal: "对角线",
  golden_ratio: "黄金比例",
};

const HEIGHT_LABEL = {
  low: "低位",
  eye_level: "平视",
  high: "高位",
  overhead: "俯拍",
};

const LAYOUT_LABEL = {
  single: "单人",
  side_by_side: "并肩",
  high_low_offset: "高低错位",
  triangle: "三角",
  line: "一字排开",
  cluster: "簇拥",
  diagonal: "对角分布",
  v_formation: "V 型",
  circle: "围圈",
  custom: "自定义",
};

const LENS_LABEL = {
  ultrawide_0_5x: "0.5x 超广角",
  wide_1x: "1x 主摄",
  tele_2x: "2x 长焦",
  tele_3x: "3x 长焦",
  tele_5x: "5x 长焦",
};

export function renderResult(container, response) {
  container.innerHTML = "";

  // v7 — stash the response on window so the 3D scene's lighting can
  // lock onto environment.sun azimuth/altitude. Avoids drilling the
  // environment object through every renderShot call.
  try { window.__lastAnalyzeResponse = response; } catch {}

  const frames = loadFrames();
  const refInsp = loadRefInspiration();

  // Banner merge (v9 UX polish #21) — show at most ONE red banner at the
  // top so the user isn't punched in the face by two negative messages
  // and tempted to abandon. Severity ladder:
  //   1. capture_quality.should_retake (score ≤ 2)  ← strongest negative
  //   2. light_recapture_hint                       ← positive nudge
  //   3. capture_quality with score == 3            ← soft advisory
  // The non-winning banner is degraded to an inline note inside the
  // winning banner so the user still sees it.
  const advisory = response.scene && response.scene.capture_quality;
  const recapture = response.light_recapture_hint && response.light_recapture_hint.enabled
    ? response.light_recapture_hint
    : null;
  const advisoryCritical = advisory && advisory.should_retake;
  if (advisoryCritical) {
    container.appendChild(renderCaptureAdvisory(advisory, { degradedHint: recapture }));
  } else if (recapture) {
    container.appendChild(renderRecaptureBanner(recapture, { degradedAdvisory: advisory && advisory.score <= 3 ? advisory : null }));
  } else if (advisory && advisory.score <= 3) {
    container.appendChild(renderCaptureAdvisory(advisory, { degradedHint: null }));
  }

  // Environment strip — show whenever we have *anything* useful: sun
  // snapshot (geo-derived), weather, or a vision_light fallback. The
  // compass adapts its rendering accordingly.
  const env = response.environment;
  const hasEnvData =
    env && (env.sun || env.weather || (env.vision_light && env.vision_light.direction_deg != null));
  if (hasEnvData) {
    container.appendChild(
      renderEnvironmentStrip(env, response.shots || []),
    );
  }

  container.appendChild(renderScene(response.scene));

  // Style inspiration: "AI 借鉴了你这几张图的 X". Show only when we
  // have reference images and the model actually said something.
  if (refInsp && refInsp.count > 0) {
    container.appendChild(
      renderInspirationCard(response.style_inspiration, refInsp),
    );
  } else if (response.style_inspiration && response.style_inspiration.summary) {
    // Mock mode might still send a summary string; pass empty refs.
    container.appendChild(
      renderInspirationCard(response.style_inspiration, { count: 0, thumbs: [] }),
    );
  }

  if (response.debug && response.debug.personalization) {
    container.appendChild(renderPersonalizationNote(response.debug.personalization));
  }

  // Phase 3.3 — local ranking chip. The backend already pre-computed
  // overall_score for every shot; this UI just toggles the ordering on
  // the client (no network round-trip). The chip stays hidden when
  // there's only one shot or no overall_score values.
  const hasOverall = (response.shots || []).some(
    (s) => typeof s.overall_score === "number"
  );

  // v7 Phase A — shots are now a horizontal swipe pager so the user
  // doesn't have to scroll past 3 full plans. Each plan gets its own
  // 100% column, snap-aligned. The pager header (chips on top) and
  // ranking toolbar share a row — both stay sticky as the user
  // scrolls inside any single plan.
  const shotsContext = { current: 0, response, frames };
  const pagerHeader = renderShotsPagerHeader(shotsContext);
  if ((response.shots || []).length > 1) {
    container.appendChild(pagerHeader);
  }
  if ((response.shots || []).length > 1 && hasOverall) {
    container.appendChild(renderRankingChip(response, shotsContext, pagerHeader));
  }
  const shotsHost = el("div", { class: "shots-host shots-pager" });
  shotsContext.host = shotsHost;
  container.appendChild(shotsHost);
  renderShotsList(shotsHost, response.shots || [], frames, shotsContext);
}

/**
 * Build the top-of-page swipe header — chips ("机位 1 · 2 · 3") that
 * highlight as the user pages, plus a subtle scroll-progress bar.
 * Returns the DOM node and stashes setters on shotsContext.
 */
function renderShotsPagerHeader(ctx) {
  const wrap = el("section", { class: "shots-pager-header" });
  const chipRow = el("div", { class: "shots-pager-chips" });
  ctx.chips = [];
  (ctx.response.shots || []).forEach((shot, i) => {
    const score = typeof shot.overall_score === "number"
      ? `${shot.overall_score.toFixed(1)}`
      : null;
    const chip = el(
      "button",
      {
        type: "button",
        class: "shots-pager-chip" + (i === 0 ? " is-active" : ""),
        "data-index": String(i),
      },
      [
        el("span", { class: "shots-pager-chip-num" }, `#${i + 1}`),
        shot.title ? el("span", { class: "shots-pager-chip-title" }, shot.title) : null,
        score ? el("span", { class: "shots-pager-chip-score" }, score) : null,
      ].filter(Boolean),
    );
    chip.addEventListener("click", () => {
      gotoShot(ctx, i, { fromChip: true });
    });
    chipRow.appendChild(chip);
    ctx.chips.push(chip);
  });
  wrap.appendChild(chipRow);
  return wrap;
}

/**
 * Programmatic scroll to shot N. Two reasons we do this manually instead
 * of relying on `<a href="#shot-N">`:
 *   1. We need to keep `ctx.current` in sync so the chips highlight
 *   2. Smooth scroll inside a horizontal scroll-snap container has
 *      different inertia behavior on iOS vs Chrome — explicit JS gives
 *      a predictable feel.
 */
function gotoShot(ctx, idx, { fromChip = false } = {}) {
  if (!ctx.host) return;
  const slides = ctx.host.querySelectorAll(".shot-slide");
  if (!slides[idx]) return;
  ctx.current = idx;
  if (fromChip) {
    slides[idx].scrollIntoView({
      behavior: "smooth",
      block: "nearest",
      inline: "start",
    });
  }
  ctx.chips.forEach((c, i) => c.classList.toggle("is-active", i === idx));
}

function renderShotsList(host, shots, frames, ctx) {
  host.innerHTML = "";
  shots.forEach((shot, i) => {
    const slide = el("div", { class: "shot-slide", "data-shot-index": String(i) });
    slide.appendChild(renderShot(shot, i, frames));
    host.appendChild(slide);
  });
  // Re-wire the scroll listener so chip highlight tracks horizontal
  // scrollLeft. We compare with each slide's offsetLeft to find the
  // nearest one snapped at the leading edge.
  if (ctx) {
    const onScroll = () => {
      const x = host.scrollLeft;
      const slides = host.querySelectorAll(".shot-slide");
      let bestIdx = 0, bestDist = Infinity;
      slides.forEach((s, i) => {
        const d = Math.abs(s.offsetLeft - x);
        if (d < bestDist) { bestDist = d; bestIdx = i; }
      });
      if (bestIdx !== ctx.current) {
        ctx.current = bestIdx;
        if (ctx.chips) {
          ctx.chips.forEach((c, i) => c.classList.toggle("is-active", i === bestIdx));
        }
      }
    };
    host.addEventListener("scroll", onScroll, { passive: true });
  }
}

const RANKING_KEY = "shotRankingMode";

function renderRankingChip(response, ctx, pagerHeader) {
  const wrap = el("section", { class: "ranking-toolbar" });
  wrap.appendChild(
    el("span", { class: "ranking-label" }, "排序方式"),
  );
  const initial = (() => {
    try { return sessionStorage.getItem(RANKING_KEY) || "default"; } catch { return "default"; }
  })();
  const modes = [
    { id: "default", label: "推荐序" },
    { id: "score",   label: "综合分" },
  ];
  modes.forEach(({ id, label }) => {
    const btn = el("button", {
      type: "button",
      class: "ranking-chip" + (id === initial ? " is-active" : ""),
      "data-mode": id,
    }, label);
    btn.addEventListener("click", () => {
      wrap.querySelectorAll(".ranking-chip").forEach((b) => {
        b.classList.toggle("is-active", b.dataset.mode === id);
      });
      try { sessionStorage.setItem(RANKING_KEY, id); } catch {}
      const sorted = sortShots(response.shots, id);
      response.shots = sorted;
      // v7 — re-render pager chips to reflect the new order, then
      // jump back to slide 0 (the new top pick).
      if (pagerHeader) {
        const oldChipRow = pagerHeader.querySelector(".shots-pager-chips");
        const fresh = renderShotsPagerHeader(ctx).firstChild;
        if (oldChipRow && fresh) pagerHeader.replaceChild(fresh, oldChipRow);
      }
      renderShotsList(ctx.host, sorted, ctx.frames, ctx);
      ctx.host.scrollLeft = 0;
      ctx.current = 0;
    });
    wrap.appendChild(btn);
  });
  // Apply persisted preference immediately if it differs from default —
  // we wired the chip but renderResult ran before this; resort now.
  if (initial === "score") {
    response.shots = sortShots(response.shots, "score");
  }
  return wrap;
}

function sortShots(shots, mode) {
  const copy = [...shots];
  if (mode === "score") {
    copy.sort((a, b) => (b.overall_score ?? 0) - (a.overall_score ?? 0));
  }
  // "default" leaves the backend order intact — that's already a
  // best-effort time-sensitivity ranking from analyze_service.
  return copy;
}

function renderInspirationCard(styleInsp, refInsp) {
  const card = el("section", {
    class: "section inspiration-card",
  });
  const head = el("div", { class: "inspiration-head" });
  head.appendChild(el("h3", {}, "AI 借鉴了你的参考图"));
  if (refInsp.count > 0) {
    head.appendChild(
      el("span", { class: "tag accent" }, `${refInsp.count} 张`),
    );
  }
  card.appendChild(head);

  if (refInsp.thumbs && refInsp.thumbs.length) {
    const grid = el("div", { class: "inspiration-thumbs" });
    refInsp.thumbs.forEach((src, i) => {
      const t = el("div", { class: "inspiration-thumb" });
      const img = el("img", { src, alt: refInsp.names?.[i] || `ref ${i + 1}` });
      img.onerror = () => (t.style.display = "none");
      t.appendChild(img);
      grid.appendChild(t);
    });
    card.appendChild(grid);
  }

  const summaryText =
    (styleInsp && styleInsp.summary) ||
    "AI 已把你这些参考图作为风格锚点喂入模型，下面的 rationale 会显式说明它从哪些图里借鉴了什么。";
  card.appendChild(el("p", { class: "rationale inspiration-summary" }, summaryText));

  if (styleInsp && styleInsp.inherited_traits && styleInsp.inherited_traits.length) {
    const tags = el("div", { class: "kv-row" });
    styleInsp.inherited_traits.slice(0, 6).forEach((t) => {
      tags.appendChild(el("span", { class: "tag accent" }, t));
    });
    card.appendChild(tags);
  }
  return card;
}

function renderScene(scene) {
  const card = el("section", { class: "section scene-card" });
  card.appendChild(el("h3", {}, "环境分析"));
  card.appendChild(
    el("div", { class: "kv-row" }, [
      tag(LIGHTING_LABEL[scene.lighting] || scene.lighting, "accent"),
      tag(scene.type),
      ...(scene.cautions || []).slice(0, 1).map((c) => tag("注意", "warn")),
    ]),
  );
  card.appendChild(
    el("p", { class: "rationale" }, scene.background_summary || ""),
  );
  if (scene.cautions && scene.cautions.length) {
    const list = el("ul", {
      style:
        "list-style: none; padding-left: 0; margin: 4px 0 0; color: var(--warn); font-size: 13px;",
    });
    scene.cautions.forEach((c) => list.appendChild(el("li", {}, "⚠ " + c)));
    card.appendChild(list);
  }
  return card;
}

function renderPersonalizationNote(text) {
  const card = el("section", {
    class: "section",
    style: "border-color: rgba(91,156,255,0.4); background: var(--accent-soft);",
  });
  card.appendChild(el("h3", { style: "color: var(--accent);" }, "个性化"));
  card.appendChild(el("div", {}, text));
  return card;
}

export function isSceneryShot(shot) {
  // True when the backend signalled that this shot is environment-only
  // (poses array deliberately empty). The render code uses this to
  // collapse the pose card and the 3D-with-avatar toggle.
  return !shot.poses || shot.poses.length === 0;
}

function renderShot(shot, idx, frames) {
  const scenery = isSceneryShot(shot);
  const card = el("section", { class: "section shot-card" });
  if (scenery) card.dataset.scenery = "1";
  const header = el("div", { class: "shot-header" });
  header.appendChild(
    el("div", {}, [
      el("h4", { style: "display: inline;" }, `机位 #${idx + 1}`),
      shot.title ? el("span", { class: "subtitle" }, shot.title) : null,
      scenery ? el("span", { class: "tag accent" }, "风景") : null,
    ]),
  );
  header.appendChild(
    el(
      "span",
      { class: "confidence" },
      `${Math.round((shot.confidence || 0) * 100)}%`,
    ),
  );
  card.appendChild(header);

  // ── HERO: visual mock-up. Scenery shots show only 2D (no avatars). ──
  const heroWrap = el("div", { class: "hero-mode-wrap" });
  const toggle = scenery
    ? null
    : el("div", { class: "hero-toggle" }, [
        el("button", { class: "hero-toggle-btn active", "data-mode": "2d", type: "button" }, "平面预览"),
        el("button", { class: "hero-toggle-btn", "data-mode": "3d", type: "button" }, "立体预览 (含虚拟角色)"),
      ]);
  if (toggle) heroWrap.appendChild(toggle);
  const heroStage = el("div", { class: "hero-stage" });
  heroWrap.appendChild(heroStage);

  const backdropFrame = pickBackdrop(frames || [], shot);
  let scene3DInstance = null;

  function renderHero(mode) {
    heroStage.innerHTML = "";
    if (scene3DInstance) {
      try { scene3DInstance.dispose(); } catch {}
      scene3DInstance = null;
    }
    if (mode === "2d") {
      heroStage.appendChild(
        renderSceneCompose(backdropFrame ? backdropFrame.src : null, shot, { idx }),
      );
      return;
    }
    // 3D mode: dynamic import to avoid loading Three.js until needed
    const placeholder = el("div", { class: "hero-3d-loading" }, [
      el("div", { class: "spinner" }),
      el("div", {}, "正在搭场景…"),
    ]);
    heroStage.appendChild(placeholder);
    Promise.all([
      import("./scene_3d.js"),
      import("./composition_overlay.js"),
    ])
      .then(([sceneMod, overlayMod]) => {
        heroStage.innerHTML = "";
        const stage = el("div", { class: "hero-3d-stage" });
        heroStage.appendChild(stage);
        // v7 — picks here MUST be the raw RPM preset ids the user
        // saved (e.g. "female_casual_22"); scene_3d.js does its own
        // legacy fallback via resolveAvatarPicks for the procedural
        // placeholder mesh, but `picks` itself is the RPM upgrade
        // path. The previous version pre-mapped these to legacy
        // ids (akira/yuki/...) which always missed the RPM lookup
        // and left every shot showing the procedural placeholder.
        const rawPicks = loadAvatarPicks();
        // v7 — pass environment so the directional light can lock onto
        // the actual sun position the analyze step computed.
        const env = (window.__lastAnalyzeResponse && window.__lastAnalyzeResponse.environment) || null;
        scene3DInstance = sceneMod.createSceneView(stage, {
          panoramaUrl: loadPanoramaUrl(),
          shot,
          picks: rawPicks,
          environment: env,
        });
        // v7 — overlay the composition guide + parameter HUD chip on
        // top of the 3D canvas so the user *sees* what AI composed for.
        const overlay = overlayMod.mountCompositionOverlay(stage, {
          shot, sceneView: scene3DInstance,
        });
        const prevDispose = scene3DInstance.dispose;
        scene3DInstance.dispose = () => {
          try { overlay.dispose(); } catch {}
          prevDispose();
        };
        const hint = el("div", { class: "hero-3d-hint" }, "可拖动微调 · 双指缩放");
        stage.appendChild(hint);
        setTimeout(() => hint.classList.add("fade"), 3500);
      })
      .catch((e) => {
        heroStage.innerHTML = "";
        heroStage.appendChild(el("div", { class: "hero-3d-error" }, `3D 加载失败: ${e?.message || e}`));
      });
  }

  if (toggle) {
    toggle.addEventListener("click", (e) => {
      const btn = e.target.closest(".hero-toggle-btn");
      if (!btn) return;
      [...toggle.children].forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      renderHero(btn.dataset.mode);
    });
  }

  renderHero("2d");
  card.appendChild(heroWrap);

  // ── camera dial row: focal / aperture / shutter / ISO at a glance ──
  // v9 UX polish #6 — dial row sits right under the hero so the four
  // most-asked numbers (焦段/光圈/快门/ISO) are visible BEFORE any
  // scrolling. This is the literal answer to "我该怎么按快门".
  const dialRow = renderCameraDial(shot.camera);
  if (dialRow) card.appendChild(dialRow);

  // ── coach bubble: first-person rationale (kept above the fold) ──
  if (shot.coach_brief || shot.rationale) {
    const bubble = el("div", { class: "coach-bubble" });
    bubble.appendChild(el("div", { class: "coach-avatar" }, "📷"));
    const body = el("div", { class: "coach-body" });
    if (shot.coach_brief) {
      body.appendChild(el("p", { class: "coach-brief" }, `"${shot.coach_brief}"`));
    }
    if (shot.rationale) {
      body.appendChild(el("p", { class: "coach-rationale" }, shot.rationale));
      // Dev-only persona-tone sanity check. Mirrors shared/copy/persona_tone.json.
      // Logs a warning when the AI slips back into teacher-y openers so we
      // can spot prompt regressions without bothering the user with UI.
      const banned = ["我建议你","你应该","你需要","让我们","我们一起","试想一下","不妨"];
      const hit = banned.find((b) => shot.rationale.startsWith(b) || shot.rationale.includes(`。${b}`));
      if (hit) {
        console.warn(`[persona-tone] shot ${idx} rationale uses banned opener "${hit}":`, shot.rationale);
      }
    }
    bubble.appendChild(body);
    card.appendChild(bubble);
  }

  // ── PRIMARY CTA — v9 UX polish #6. The single biggest call-to-action
  // sits right under the coach bubble so the user can act WITHOUT
  // scrolling past evaluation panels. On the web side this means
  // "保存方案截图" (the core deliverable when there's no iOS shoot
  // screen at hand); the AR/guide button moves into the secondary row.
  const primaryCta = renderPrimaryCta(shot, idx);
  card.appendChild(primaryCta);

  // ── secondary visuals: minimap + AR rehearsal (de-emphasised) ──
  const visualRow = el("div", { class: "shot-visual-row" });
  const mapWrap = el("div", { class: "shot-minimap" });
  mapWrap.appendChild(renderShotMinimap(shot.angle, { label: shot.title }));
  visualRow.appendChild(mapWrap);

  const ctaWrap = el("div", { class: "shot-cta shot-cta--secondary" });
  const guideBtn = el(
    "button",
    { class: "btn btn-guide btn--secondary", type: "button" },
    [el("span", {}, "AR 演练人物站位"), el("span", { class: "cta-arrow" }, "→")],
  );
  guideBtn.addEventListener("click", () => {
    saveCurrentShot({ shot, idx });
    location.href = "/web/guide.html";
  });
  ctaWrap.appendChild(guideBtn);
  ctaWrap.appendChild(
    el("p", { class: "cta-note" }, "切到摄像头视图 · AR 提示你转向并站位"),
  );
  visualRow.appendChild(ctaWrap);
  card.appendChild(visualRow);

  // ── POSES — kept visible (core deliverable). Scenery shots fall back
  // to scenery tips. Everything below this is collapsed by default. ──
  if (scenery) {
    card.appendChild(el("div", { class: "divider" }));
    card.appendChild(renderSceneryTips(shot));
  } else if ((shot.poses || []).length) {
    card.appendChild(el("div", { class: "divider" }));
    card.appendChild(
      el(
        "h3",
        { style: "color: var(--text); margin: 0; text-transform: none; font-size: 15px; letter-spacing: 0;" },
        "姿势建议",
      ),
    );
    (shot.poses || []).forEach((p) => card.appendChild(renderPose(p)));
  }

  // ── COLLAPSED LONG TAIL — v9 UX polish #6.
  // Quality scoring (7 dims), style match clamp report, iPhone tips,
  // foreground doctrine, raw camera/angle/composition rows — all the
  // "expert detail" goes here, behind a single click. Default closed
  // so the user gets a clean primary→secondary→action flow first.
  const longTail = el("details", { class: "shot-longtail" });
  longTail.appendChild(
    el("summary", { class: "shot-longtail-summary" }, "展开更多分析 · 评分 / 构图 / iPhone 适配"),
  );

  if (shot.criteria_score) {
    longTail.appendChild(renderCriteriaPanel(shot));
  }
  if (shot.style_match) {
    longTail.appendChild(renderStyleMatch(shot));
  }
  if (
    (shot.iphone_tips && shot.iphone_tips.length) ||
    (shot.camera && shot.camera.iphone_apply_plan)
  ) {
    longTail.appendChild(renderIphoneTipsCard(shot));
  }

  const techDetails = el("div", { class: "shot-tech" });
  techDetails.appendChild(el("h4", { class: "shot-tech-title" }, "相机参数 / 构图 / 角度"));
  techDetails.appendChild(renderAngle(shot.angle));
  techDetails.appendChild(renderComposition(shot.composition));
  techDetails.appendChild(renderCamera(shot.camera));
  longTail.appendChild(techDetails);

  card.appendChild(longTail);
  return card;
}

// v9 UX polish #6 — primary CTA. Single biggest action right under
// the coach bubble. On web we steer to "保存方案截图" (the universally
// available deliverable); the AR/guide button stays in the secondary
// row below the minimap so power users can still reach it.
function renderPrimaryCta(shot, idx) {
  const wrap = el("div", { class: "shot-primary-cta" });
  const btn = el(
    "button",
    { class: "btn btn-primary-shot", type: "button" },
    [
      el("span", { class: "btn-primary-icon", "aria-hidden": "true" }, "↓"),
      el("span", { class: "btn-primary-label" }, "保存方案截图带去现场"),
    ],
  );
  btn.addEventListener("click", () => {
    saveCurrentShot({ shot, idx });
    // Lazy-import the share helper to keep initial render light. The
    // helper falls back to a download when Share API is unavailable
    // (Chromium desktop / Firefox).
    import("./share_plan.js")
      .then((mod) => mod.shareOrDownloadPlan(shot, idx))
      .catch((e) => {
        console.warn("[render] plan share fallback failed", e);
        alert("保存失败，截图工具未加载。请稍后再试或手动截屏。");
      });
  });
  wrap.appendChild(btn);
  wrap.appendChild(
    el(
      "p",
      { class: "shot-primary-cta-note" },
      "导出方案 #" + (idx + 1) + " 为一张可分享的卡片图",
    ),
  );
  return wrap;
}

// ────────────────────────────────────────────────────────────────────────────
// Environment strip
//
// Light-shadow mode (and any future mode that ships geo data) lights this
// up at the top of the result page:
//   - A circular compass: N/E/S/W ring + sun dot at its real azimuth +
//     thin arrows for each shot's recommended camera azimuth.
//   - A countdown chip: "黄金时刻还剩 23 分钟" with a pulsing dot.
//   - Color-temp chip and altitude chip for at-a-glance lighting context.
// ────────────────────────────────────────────────────────────────────────────

const PHASE_LABELS = {
  night: "夜间",
  blue_hour_dawn: "蓝调（清晨）",
  golden_hour_dawn: "黄金时刻（清晨）",
  day: "白天",
  golden_hour_dusk: "黄金时刻（傍晚）",
  blue_hour_dusk: "蓝调（傍晚）",
};

function renderEnvironmentStrip(env, shots) {
  const sun = env.sun || null;
  const weather = env.weather || null;
  const visionLight = env.vision_light || null;
  const wrap = el("section", { class: "env-strip" });

  // Left: compass — handles three cases:
  //   1. Real sun (geo) -> solid sun glyph + shot arrows
  //   2. Vision-only (no geo, but LLM gave us direction_deg) -> dashed glyph
  //   3. Nothing useful -> compass omitted, chips alone
  if (sun || (visionLight && visionLight.direction_deg != null)) {
    wrap.appendChild(renderSunCompass(sun, shots, visionLight));
  }

  // Right: textual chips (phase / countdown / temp / weather)
  const chips = el("div", { class: "env-chips" });

  if (sun) {
    chips.appendChild(
      el("div", { class: "env-phase" }, [
        el("span", { class: "env-phase-dot" }),
        el("span", {}, PHASE_LABELS[sun.phase] || sun.phase),
      ]),
    );

    const countdownText = formatCountdown(sun);
    if (countdownText) {
      const tight =
        (sun.minutes_to_golden_end != null && sun.minutes_to_golden_end <= 30) ||
        (sun.minutes_to_blue_end != null && sun.minutes_to_blue_end <= 30);
      chips.appendChild(
        el("div", { class: `env-chip env-countdown${tight ? " is-tight" : ""}` }, [
          el("span", { class: "env-chip-glyph" }, "⏱"),
          el("div", { class: "env-chip-body" }, [
            el("b", {}, countdownText.title),
            el("span", {}, countdownText.subtitle),
          ]),
        ]),
      );
    }

    chips.appendChild(
      el("div", { class: "env-chip" }, [
        el("span", { class: "env-chip-glyph" }, "☀"),
        el("div", { class: "env-chip-body" }, [
          el("b", {}, `${sun.color_temp_k_estimate}K`),
          el("span", {}, `估算色温 · 高度角 ${Math.round(sun.altitude_deg)}°`),
        ]),
      ]),
    );
  } else if (visionLight && visionLight.direction_deg != null) {
    // Vision-only: emphasise the source so the user knows the indicator
    // is an AI estimate rather than a sun calculation.
    chips.appendChild(
      el("div", { class: "env-phase env-phase--vision" }, [
        el("span", { class: "env-phase-dot" }),
        el("span", {}, "视觉估算光向"),
      ]),
    );
    const conf = Math.round((visionLight.confidence ?? 0) * 100);
    chips.appendChild(
      el("div", { class: "env-chip env-chip--vision" }, [
        el("span", { class: "env-chip-glyph" }, "✦"),
        el("div", { class: "env-chip-body" }, [
          el(
            "b",
            {},
            `${LIGHT_QUALITY_LABEL[visionLight.quality] || "未知"} · ${Math.round(visionLight.direction_deg)}°`,
          ),
          el("span", {}, `置信度 ${conf}% · 来自视频帧分析`),
        ]),
      ]),
    );
  }

  if (weather) {
    chips.appendChild(renderWeatherChip(weather));
  }

  if (sun && sun.minutes_to_golden_end != null && sun.minutes_to_golden_end <= 30) {
    chips.appendChild(
      el(
        "p",
        { class: "env-tight-note" },
        "AI 已按主光方向重排方案：第 1 张是当下最该抢拍的角度",
      ),
    );
  }

  wrap.appendChild(chips);
  return wrap;
}

const LIGHT_QUALITY_LABEL = {
  hard: "硬光",
  soft: "软光",
  mixed: "半软半硬",
  unknown: "未知",
};

const SOFTNESS_GLYPH = { soft: "☁", hard: "☀", mixed: "⛅", unknown: "·" };
const SOFTNESS_LABEL = { soft: "软光", hard: "硬光", mixed: "半软半硬", unknown: "未判定" };

function renderWeatherChip(weather) {
  const softness = weather.softness || "unknown";
  const cloud = weather.cloud_cover_pct != null ? `${weather.cloud_cover_pct}%` : "—";
  const subtitleParts = [`云量 ${cloud}`];
  if (weather.code_label_zh) subtitleParts.unshift(weather.code_label_zh);
  if (weather.temperature_c != null) {
    subtitleParts.push(`${Math.round(weather.temperature_c)}°C`);
  }
  return el("div", { class: `env-chip env-chip--weather is-${softness}` }, [
    el("span", { class: "env-chip-glyph" }, SOFTNESS_GLYPH[softness] || "·"),
    el("div", { class: "env-chip-body" }, [
      el("b", {}, SOFTNESS_LABEL[softness] || "天气"),
      el("span", {}, subtitleParts.join(" · ")),
    ]),
  ]);
}

function renderRecaptureBanner(hint, opts = {}) {
  const banner = el("section", { class: "recapture-banner" });
  const row = el("div", { class: "recapture-row" });
  row.appendChild(el("div", { class: "recapture-icon", "aria-hidden": "true" }, "✦"));
  const body = el("div", { class: "recapture-body" });
  body.appendChild(el("h4", { class: "recapture-title" }, hint.title || "建议补一段定向环视"));
  body.appendChild(
    el("p", { class: "recapture-detail" }, hint.detail || "对着最亮的方向慢转 10 秒，AI 会更准。"),
  );
  if (hint.suggested_azimuth_deg != null) {
    body.appendChild(
      el(
        "p",
        { class: "recapture-suggest" },
        `建议中心方位：${Math.round(hint.suggested_azimuth_deg)}°（已为你预设）`,
      ),
    );
  }
  row.appendChild(body);

  const cta = el("button", {
    type: "button",
    class: "recapture-cta",
  }, "去补一段");
  cta.addEventListener("click", () => {
    try {
      sessionStorage.setItem(
        "lightRecaptureHint",
        JSON.stringify({
          azimuth_deg: hint.suggested_azimuth_deg ?? null,
          ts: Date.now(),
        }),
      );
    } catch (e) { /* sessionStorage may be disabled */ }
    // Send the user back to the wizard. They'll land on Step 4 (their
    // furthest step) and tap the big CTA to start a fresh capture; index.js
    // picks up `lightRecaptureHint` from sessionStorage to centre the new
    // pass on the suggested azimuth.
    if (typeof window !== "undefined") {
      window.location.href = "/web/";
    }
  });
  row.appendChild(cta);
  banner.appendChild(row);

  // Degraded advisory inline note — banner merge keeps secondary
  // negative signal visible without a second red block.
  const degraded = opts.degradedAdvisory;
  if (degraded && degraded.summary_zh) {
    banner.appendChild(
      el(
        "p",
        { class: "banner-inline-note" },
        `素材质量 ${degraded.score}/5 · ${degraded.summary_zh}`,
      ),
    );
  }
  return banner;
}

// Capture-quality advisory: red banner when the LLM judged the env video
// unfit for analysis. Does NOT block the user — they can scroll past, but
// they'll see exactly *why* the AI's confidence is dialled back. UX-wise
// this is more honest than silently producing garbage shots.
const CAPTURE_ISSUE_LABEL = {
  cluttered_bg: "背景太杂",
  no_subject: "没有可识别的主体",
  ground_only: "镜头主要对着地面",
  too_dark: "环境太暗",
  too_many_passersby: "路人过多",
  blurry: "画面糊（设备晃动 / 失焦）",
  narrow_pan: "环视范围太窄",
};

function renderCaptureAdvisory(advisory, opts = {}) {
  const banner = el("section", {
    class: `capture-advisory capture-advisory-score-${advisory.score}`,
  });
  banner.dataset.shouldRetake = advisory.should_retake ? "1" : "0";

  const head = el("div", { class: "capture-advisory-head" });
  const stars = "★".repeat(advisory.score) + "☆".repeat(5 - advisory.score);
  head.appendChild(
    el("div", { class: "capture-advisory-score" }, [
      el("span", { class: "capture-advisory-stars" }, stars),
      el(
        "span",
        { class: "capture-advisory-score-text" },
        `素材质量 ${advisory.score}/5`,
      ),
    ]),
  );
  if (advisory.should_retake) {
    head.appendChild(
      el(
        "span",
        { class: "tag warn capture-advisory-tag" },
        "建议重拍环视",
      ),
    );
  }
  banner.appendChild(head);

  if (advisory.summary_zh) {
    banner.appendChild(
      el("p", { class: "capture-advisory-summary" }, advisory.summary_zh),
    );
  }

  if (advisory.issues && advisory.issues.length) {
    const list = el("ul", { class: "capture-advisory-issues" });
    advisory.issues.forEach((issue) => {
      const label = CAPTURE_ISSUE_LABEL[issue] || issue;
      list.appendChild(el("li", {}, "· " + label));
    });
    banner.appendChild(list);
  }

  if (advisory.should_retake) {
    const cta = el(
      "button",
      { type: "button", class: "capture-advisory-cta" },
      "重新环视一段",
    );
    cta.addEventListener("click", () => {
      try { sessionStorage.setItem("captureRetakeHint", "1"); } catch {}
      if (typeof window !== "undefined") window.location.href = "/web/";
    });
    banner.appendChild(cta);
  }

  // Degraded light-recapture hint inline — banner merge keeps the
  // secondary positive nudge visible without a competing red block.
  const degraded = opts.degradedHint;
  if (degraded && (degraded.title || degraded.detail)) {
    const note = el("p", { class: "banner-inline-note" });
    if (degraded.title) note.appendChild(el("b", {}, degraded.title));
    if (degraded.title && degraded.detail) note.appendChild(document.createTextNode(" · "));
    if (degraded.detail) note.appendChild(document.createTextNode(degraded.detail));
    banner.appendChild(note);
  }
  return banner;
}

function formatCountdown(sun) {
  if (sun.minutes_to_golden_end != null) {
    const m = Math.round(sun.minutes_to_golden_end);
    return {
      title: `黄金时刻还剩 ${m} 分钟`,
      subtitle: m <= 30 ? "光线在加速消失，先拍排前面的方案" : "暖光柔光最佳窗口",
    };
  }
  if (sun.minutes_to_blue_end != null) {
    const m = Math.round(sun.minutes_to_blue_end);
    return {
      title: `蓝调时刻还剩 ${m} 分钟`,
      subtitle: "天空冷蓝调 · 适合做电影感剪影",
    };
  }
  if (sun.minutes_to_sunset != null && sun.minutes_to_sunset <= 90) {
    const m = Math.round(sun.minutes_to_sunset);
    return {
      title: `距日落 ${m} 分钟`,
      subtitle: "光线方向开始向西偏低，注意逆光保留高光",
    };
  }
  return null;
}

function renderSunCompass(sun, shots, visionLight) {
  const SIZE = 132;
  const C = SIZE / 2;
  const R_OUTER = C - 4;
  const R_INNER = R_OUTER - 14;

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "sun-compass");
  svg.setAttribute("width", String(SIZE));
  svg.setAttribute("height", String(SIZE));
  svg.setAttribute("viewBox", `0 0 ${SIZE} ${SIZE}`);
  svg.setAttribute("aria-label", "太阳罗盘");

  // Outer ring
  const ring = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  ring.setAttribute("cx", String(C));
  ring.setAttribute("cy", String(C));
  ring.setAttribute("r", String(R_OUTER));
  ring.setAttribute("class", "compass-ring");
  svg.appendChild(ring);

  // Inner ring
  const innerRing = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  innerRing.setAttribute("cx", String(C));
  innerRing.setAttribute("cy", String(C));
  innerRing.setAttribute("r", String(R_INNER));
  innerRing.setAttribute("class", "compass-ring-inner");
  svg.appendChild(innerRing);

  // N/E/S/W labels (azimuth 0/90/180/270)
  const cardinals = [
    { label: "N", az: 0 },
    { label: "E", az: 90 },
    { label: "S", az: 180 },
    { label: "W", az: 270 },
  ];
  for (const c of cardinals) {
    const p = polar(C, C, R_OUTER + 1, c.az);
    const tx = document.createElementNS("http://www.w3.org/2000/svg", "text");
    tx.setAttribute("x", String(p.x));
    tx.setAttribute("y", String(p.y));
    tx.setAttribute("class", "compass-cardinal");
    tx.setAttribute("text-anchor", "middle");
    tx.setAttribute("dominant-baseline", "middle");
    tx.textContent = c.label;
    svg.appendChild(tx);
  }

  // Tick marks every 30°
  for (let az = 0; az < 360; az += 30) {
    const t1 = polar(C, C, R_INNER, az);
    const t2 = polar(C, C, R_INNER + 4, az);
    const ln = document.createElementNS("http://www.w3.org/2000/svg", "line");
    ln.setAttribute("x1", String(t1.x));
    ln.setAttribute("y1", String(t1.y));
    ln.setAttribute("x2", String(t2.x));
    ln.setAttribute("y2", String(t2.y));
    ln.setAttribute("class", "compass-tick");
    svg.appendChild(ln);
  }

  // Shot azimuth arrows (thin)
  (shots || []).forEach((shot, i) => {
    if (!shot.angle) return;
    const az = shot.angle.azimuth_deg ?? 0;
    const p1 = polar(C, C, 14, az);
    const p2 = polar(C, C, R_INNER - 6, az);
    const arrow = document.createElementNS("http://www.w3.org/2000/svg", "line");
    arrow.setAttribute("x1", String(p1.x));
    arrow.setAttribute("y1", String(p1.y));
    arrow.setAttribute("x2", String(p2.x));
    arrow.setAttribute("y2", String(p2.y));
    arrow.setAttribute("class", `compass-shot${i === 0 ? " is-primary" : ""}`);
    svg.appendChild(arrow);

    // Shot index dot at the tip
    const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    dot.setAttribute("cx", String(p2.x));
    dot.setAttribute("cy", String(p2.y));
    dot.setAttribute("r", "5");
    dot.setAttribute("class", `compass-shot-dot${i === 0 ? " is-primary" : ""}`);
    svg.appendChild(dot);
    const lbl = document.createElementNS("http://www.w3.org/2000/svg", "text");
    lbl.setAttribute("x", String(p2.x));
    lbl.setAttribute("y", String(p2.y + 0.5));
    lbl.setAttribute("class", "compass-shot-label");
    lbl.setAttribute("text-anchor", "middle");
    lbl.setAttribute("dominant-baseline", "middle");
    lbl.textContent = String(i + 1);
    svg.appendChild(lbl);
  });

  // Light source — prefer the real sun, fall back to vision_light. The
  // class name flips so CSS can render the vision-only case as a dashed
  // halo + softer dot (lower confidence visual treatment).
  const lightAz =
    sun && sun.azimuth_deg != null
      ? sun.azimuth_deg
      : (visionLight && visionLight.direction_deg != null
          ? visionLight.direction_deg
          : null);
  if (lightAz != null) {
    const isVision = !sun;
    const sunPos = polar(C, C, R_INNER - 2, lightAz);
    const halo = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    halo.setAttribute("cx", String(sunPos.x));
    halo.setAttribute("cy", String(sunPos.y));
    halo.setAttribute("r", "12");
    halo.setAttribute(
      "class",
      `compass-sun-halo${isVision ? " is-vision" : ""}`,
    );
    svg.appendChild(halo);

    const sunDot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    sunDot.setAttribute("cx", String(sunPos.x));
    sunDot.setAttribute("cy", String(sunPos.y));
    sunDot.setAttribute("r", "5");
    sunDot.setAttribute(
      "class",
      `compass-sun-dot${isVision ? " is-vision" : ""}`,
    );
    svg.appendChild(sunDot);
  }

  // Centre camera glyph
  const centre = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  centre.setAttribute("cx", String(C));
  centre.setAttribute("cy", String(C));
  centre.setAttribute("r", "8");
  centre.setAttribute("class", "compass-centre");
  svg.appendChild(centre);
  const centreLabel = document.createElementNS("http://www.w3.org/2000/svg", "text");
  centreLabel.setAttribute("x", String(C));
  centreLabel.setAttribute("y", String(C + 0.5));
  centreLabel.setAttribute("class", "compass-centre-label");
  centreLabel.setAttribute("text-anchor", "middle");
  centreLabel.setAttribute("dominant-baseline", "middle");
  centreLabel.textContent = "📷";
  svg.appendChild(centreLabel);

  return svg;
}

function polar(cx, cy, r, azDeg) {
  // Compass: 0° = north, increases clockwise. SVG y axis points down so we
  // use a -90° rotation to put north at the top.
  const rad = ((azDeg - 90) * Math.PI) / 180;
  return { x: cx + Math.cos(rad) * r, y: cy + Math.sin(rad) * r };
}

// ────────────────────────────────────────────────────────────────────────────
// 4-dimension quality panel
//
// Shows the LLM's composition / light / color / depth scores as 4 mini bars
// with one-line rule citations underneath. Highlights strongest/weakest axes
// so the user immediately sees *why* a shot is recommended (and where it's
// vulnerable). This is what turns the "abstract" advice into something you
// can see at a glance.
// ────────────────────────────────────────────────────────────────────────────

const CRITERIA_AXES = [
  { key: "composition", label: "构图",   glyph: "▦" },
  { key: "subject_fit", label: "主体感", glyph: "◉" },
  { key: "background",  label: "背景",   glyph: "▤" },
  { key: "theme",       label: "主题",   glyph: "♦" },
  { key: "light",       label: "光线",   glyph: "☀" },
  { key: "color",       label: "色彩",   glyph: "◐" },
  { key: "depth",       label: "景深",   glyph: "⌽" },
];

// ────────────────────────────────────────────────────────────────────────────
// iPhone-specific apply plan + tips card.
//
// Shows the user that:
//   1. The iOS App will auto-apply zoom / ISO / shutter / EV / WB.
//   2. The aperture honesty note ("镜头光圈固定 f/1.78 ...") so the user
//      knows what the iPhone *can't* physically replicate.
//   3. 2-3 LLM-curated tips specific to iPhone (lens switching, ProRAW,
//      portrait mode, exposure lock, etc.).
// ────────────────────────────────────────────────────────────────────────────
function renderIphoneTipsCard(shot) {
  const tips = shot.iphone_tips || [];
  const plan = (shot.camera && shot.camera.iphone_apply_plan) || null;
  const wrap = el("section", { class: "iphone-tips-card" });

  const head = el("div", { class: "iphone-tips-head" });
  head.appendChild(el("span", { class: "iphone-tips-glyph", "aria-hidden": "true" }, "📱"));
  const headBody = el("div", { class: "iphone-tips-head-body" });
  headBody.appendChild(el("b", {}, "iPhone 适配建议"));
  headBody.appendChild(
    el("span", {}, "在我们 iOS App 里这些参数会自动应用，下面是和通用建议的差异"),
  );
  head.appendChild(headBody);
  wrap.appendChild(head);

  if (plan && plan.can_apply) {
    const equivFocal = Math.round((plan.zoom_factor || 0) * 26);
    const denom = plan.shutter_seconds > 0 ? Math.round(1 / plan.shutter_seconds) : 0;
    const shutterDisplay = denom >= 2 ? `1/${denom}` : `${plan.shutter_seconds.toFixed(2)}s`;
    const planRow = el("div", { class: "iphone-plan-row" });
    [
      ["焦段", `${equivFocal}mm · ${plan.zoom_factor.toFixed(1)}x`],
      ["ISO",  String(plan.iso)],
      ["快门", shutterDisplay],
      ["EV",   `${plan.ev_compensation >= 0 ? "+" : ""}${plan.ev_compensation.toFixed(1)}`],
      ["白平衡", `${plan.white_balance_k}K`],
    ].forEach(([label, value]) => {
      const chip = el("div", { class: "iphone-plan-chip" });
      chip.appendChild(el("span", { class: "iphone-plan-label" }, label));
      chip.appendChild(el("span", { class: "iphone-plan-value" }, value));
      planRow.appendChild(chip);
    });
    wrap.appendChild(planRow);
  }

  if (plan && plan.aperture_note) {
    const note = el("p", { class: "iphone-aperture-note" });
    note.appendChild(el("span", { class: "iphone-aperture-glyph", "aria-hidden": "true" }, "◐"));
    note.appendChild(el("span", {}, plan.aperture_note));
    wrap.appendChild(note);
  }

  if (tips.length) {
    const list = el("ol", { class: "iphone-tips-list" });
    tips.slice(0, 3).forEach((tip) => {
      list.appendChild(el("li", {}, tip));
    });
    wrap.appendChild(list);
  }

  return wrap;
}

function renderCriteriaPanel(shot) {
  const score = shot.criteria_score || {};
  const notes = shot.criteria_notes || {};
  const strong = shot.strongest_axis;
  const weak   = shot.weakest_axis;

  const wrap = el("div", { class: "criteria-panel" });

  const head = el("div", { class: "criteria-head" });
  const headLeft = el("div", { class: "criteria-head-left" });
  headLeft.appendChild(el("span", { class: "criteria-title" }, "7 维质量分析"));
  headLeft.appendChild(
    el(
      "span",
      { class: "criteria-sub" },
      "构图 · 主体感 · 背景 · 主题 · 光线 · 色彩 · 景深，每项 1-5 分",
    ),
  );
  head.appendChild(headLeft);
  if (typeof shot.overall_score === "number") {
    const overall = el("div", { class: "criteria-overall" });
    overall.appendChild(el("span", { class: "criteria-overall-value" }, shot.overall_score.toFixed(2)));
    overall.appendChild(el("span", { class: "criteria-overall-label" }, "综合分 / 5"));
    head.appendChild(overall);
  }
  wrap.appendChild(head);

  const grid = el("div", { class: "criteria-grid" });
  for (const axis of CRITERIA_AXES) {
    const value = clampScore(score[axis.key]);
    const isStrong = strong === axis.key;
    const isWeak   = weak   === axis.key;
    const note = notes[axis.key] || "";

    const row = el("div", {
      class:
        "criteria-row" +
        (isStrong ? " is-strong" : "") +
        (isWeak ? " is-weak" : ""),
    });

    const labelCol = el("div", { class: "criteria-label" });
    labelCol.appendChild(el("span", { class: "criteria-glyph" }, axis.glyph));
    labelCol.appendChild(el("span", { class: "criteria-name" }, axis.label));
    if (isStrong) labelCol.appendChild(el("span", { class: "criteria-tag strong" }, "亮点"));
    if (isWeak)   labelCol.appendChild(el("span", { class: "criteria-tag weak" }, "可改"));
    row.appendChild(labelCol);

    const barWrap = el("div", { class: "criteria-bar" });
    const bar = el("div", { class: "criteria-bar-fill" });
    bar.style.width = `${(value / 5) * 100}%`;
    barWrap.appendChild(bar);
    // 5 tick marks for reference
    for (let i = 1; i <= 5; i++) {
      const tick = el("span", { class: "criteria-tick" + (i <= value ? " on" : "") });
      tick.style.left = `${((i - 0.5) / 5) * 100}%`;
      barWrap.appendChild(tick);
    }
    row.appendChild(barWrap);

    row.appendChild(el("span", { class: "criteria-value" }, `${value}/5`));

    if (note) {
      row.appendChild(el("p", { class: "criteria-note" }, note));
    }

    grid.appendChild(row);
  }
  wrap.appendChild(grid);

  return wrap;
}

function clampScore(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return 3;
  return Math.max(1, Math.min(5, Math.round(v)));
}

function renderSceneryTips(shot) {
  const wrap = el("div", { class: "scenery-tips" });
  wrap.appendChild(
    el(
      "h3",
      { style: "color: var(--text); margin: 0; text-transform: none; font-size: 15px; letter-spacing: 0;" },
      "风景出片要点",
    ),
  );
  const tips = [];
  const c = shot.composition || {};
  if (c.primary) {
    tips.push(`构图：${COMPOSITION_LABEL[c.primary] || c.primary}`);
  }
  if (Array.isArray(c.secondary) && c.secondary.length) {
    tips.push(`辅助：${c.secondary.join(" + ")}`);
  }
  if (shot.angle) {
    tips.push(
      `站位：朝向 ${Math.round(shot.angle.azimuth_deg)}°，${
        HEIGHT_LABEL[shot.angle.height_hint] || "平视"
      }，距主景 ${shot.angle.distance_m.toFixed(1)} m`,
    );
  }
  if (shot.camera) {
    tips.push(
      `相机：${Math.round(shot.camera.focal_length_mm)}mm · ${
        shot.camera.aperture
      } · ${shot.camera.shutter} · ISO ${shot.camera.iso}`,
    );
  }
  const ul = el("ul", {
    style:
      "margin: 8px 0 0; padding-left: 18px; line-height: 1.6; color: var(--text-soft);",
  });
  tips.forEach((t) => ul.appendChild(el("li", {}, t)));
  wrap.appendChild(ul);
  return wrap;
}

function renderAngle(a) {
  const row = el("div", { class: "metric-row" });
  row.appendChild(metric("方向", `${Math.round(a.azimuth_deg)}°`));
  row.appendChild(
    metric("俯仰", `${a.pitch_deg >= 0 ? "+" : ""}${Math.round(a.pitch_deg)}°`),
  );
  row.appendChild(metric("距离", `${a.distance_m.toFixed(1)} m`));
  if (a.height_hint) {
    row.appendChild(tag(HEIGHT_LABEL[a.height_hint] || a.height_hint));
  }
  return row;
}

function renderComposition(c) {
  const row = el("div", { class: "metric-row" });
  row.appendChild(metric("构图", COMPOSITION_LABEL[c.primary] || c.primary));
  if (c.secondary && c.secondary.length) {
    row.appendChild(tag(c.secondary.join(" + ")));
  }
  if (c.notes) {
    row.appendChild(el("span", { class: "rationale" }, c.notes));
  }
  return row;
}

function renderStyleMatch(shot) {
  const m = shot.style_match;
  const cam = shot.camera || {};
  const wrap = el("div", {
    class: "style-match" + (m.in_range ? " in-range" : " clamped"),
  });

  // Header — which style + verdict pill.
  const head = el("div", { class: "style-match-head" });
  head.appendChild(el("strong", { class: "style-match-label" }, m.label_zh));
  head.appendChild(
    el(
      "span",
      { class: "style-match-pill" },
      m.in_range ? "✓ AI 已按风格出图" : "△ 已校准至风格区间",
    ),
  );
  wrap.appendChild(head);

  // Three knob rows: WB / focal / EV. Show range vs actual.
  const rows = [
    {
      label: "白平衡",
      range: m.white_balance_k_range,
      actual: cam.white_balance_k,
      fmt: (v) => (v == null ? "—" : `${v}K`),
    },
    {
      label: "焦段",
      range: m.focal_length_mm_range,
      actual: cam.focal_length_mm,
      fmt: (v) => (v == null ? "—" : `${Math.round(v)}mm`),
    },
    {
      label: "曝光补偿",
      range: m.ev_range,
      actual: cam.ev_compensation,
      fmt: (v) =>
        v == null ? "—" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(1)} EV`,
    },
  ];
  const list = el("div", { class: "style-match-rows" });
  for (const r of rows) {
    const inRange =
      r.actual != null && r.actual >= r.range[0] && r.actual <= r.range[1];
    const row = el("div", {
      class: "style-match-row" + (inRange ? " ok" : " warn"),
    });
    row.appendChild(el("span", { class: "smr-label" }, r.label));
    row.appendChild(
      el(
        "span",
        { class: "smr-range" },
        `推荐 ${r.fmt(r.range[0])}–${r.fmt(r.range[1])}`,
      ),
    );
    row.appendChild(
      el(
        "span",
        { class: "smr-actual" },
        `实际 ${r.fmt(r.actual)} ${inRange ? "✓" : "⚠"}`,
      ),
    );
    list.appendChild(row);
  }
  wrap.appendChild(list);
  return wrap;
}

function renderCamera(cam) {
  const wrap = el("div", { class: "metric-row", style: "flex-direction: column; align-items: flex-start; gap: 8px;" });
  const top = el("div", { class: "metric-row" }, [
    metric("焦段", `${Math.round(cam.focal_length_mm)}mm`),
    tag(cam.aperture),
    tag(cam.shutter),
    tag(`ISO ${cam.iso}`),
    cam.white_balance_k ? tag(`${cam.white_balance_k}K`) : null,
    cam.ev_compensation != null
      ? tag(
          `${cam.ev_compensation >= 0 ? "+" : ""}${cam.ev_compensation.toFixed(
            1,
          )} EV`,
        )
      : null,
  ]);
  wrap.appendChild(top);

  if (cam.device_hints && cam.device_hints.iphone_lens) {
    wrap.appendChild(
      el(
        "div",
        { class: "rationale" },
        "iPhone 镜头：" +
          (LENS_LABEL[cam.device_hints.iphone_lens] || cam.device_hints.iphone_lens),
      ),
    );
  }
  if (cam.rationale) {
    wrap.appendChild(el("div", { class: "rationale" }, cam.rationale));
  }
  return wrap;
}

// Camera dial row — Halide / Pro Camera 风格的关键参数大字读数。
// 4 columns: focal length, aperture, shutter, ISO. Each with a thin
// gradient bar that visually maps the numeric value to a min..max range.
function renderCameraDial(cam) {
  if (!cam) return null;
  const row = el("div", { class: "dial-row" });

  const focal = Number(cam.focal_length_mm) || 0;
  const focalPct = clamp01((focal - 14) / (200 - 14));
  row.appendChild(makeDial("FOCAL", `${Math.round(focal)}mm`, focalPct));

  const apMatch = /([\d.]+)/.exec(cam.aperture || "");
  const f = apMatch ? parseFloat(apMatch[1]) : 4;
  // Smaller f-stop = wider aperture = bigger fill. Map f1.4..f22 in log2.
  const apPct =
    1 -
    clamp01((Math.log2(f) - Math.log2(1.4)) / (Math.log2(22) - Math.log2(1.4)));
  row.appendChild(makeDial("APERTURE", cam.aperture || "—", apPct));

  // Shutter: read denominator e.g. "1/320" -> 320; map 1/8000..1/2 in log2.
  let shutPct = 0.5;
  const shutterStr = cam.shutter || "";
  const shMatch = /1\/(\d+(?:\.\d+)?)/.exec(shutterStr);
  if (shMatch) {
    const denom = parseFloat(shMatch[1]);
    shutPct = clamp01(
      (Math.log2(denom) - Math.log2(2)) / (Math.log2(8000) - Math.log2(2)),
    );
  } else if (/^\d/.test(shutterStr)) {
    // Long exposure, max bar
    shutPct = 1;
  }
  row.appendChild(makeDial("SHUTTER", shutterStr || "—", shutPct));

  const iso = Number(cam.iso) || 100;
  const isoPct = clamp01(
    (Math.log2(iso) - Math.log2(50)) / (Math.log2(12800) - Math.log2(50)),
  );
  row.appendChild(makeDial("ISO", String(iso), isoPct));
  return row;
}

function makeDial(label, value, pct) {
  const wrap = el("div", { class: "dial" });
  wrap.appendChild(el("div", { class: "dial-label" }, label));
  wrap.appendChild(el("div", { class: "dial-value" }, value));
  const bar = el("div", { class: "dial-bar" });
  const fill = el("i", {
    style: `width: ${Math.round((pct || 0) * 100)}%`,
  });
  bar.appendChild(fill);
  wrap.appendChild(bar);
  return wrap;
}

function clamp01(v) {
  return Math.max(0, Math.min(1, v));
}

function renderPose(p) {
  const block = el("div", { class: "pose-block" });

  // SVG stick-figure visualization (the "diagram" view)
  const vizWrap = el("div", { class: "pose-viz" });
  vizWrap.appendChild(renderPoseSVG(p));
  block.appendChild(vizWrap);

  // Optional fallback / additional context: the pre-baked PNG thumbnail.
  if (p.reference_thumbnail_id) {
    const t = el("div", { class: "pose-thumb" });
    const img = el("img", {
      src: poseThumbnailURL(p.reference_thumbnail_id),
      alt: p.layout,
    });
    img.onerror = () => (t.style.display = "none");
    t.appendChild(img);
    block.appendChild(t);
  }

  const body = el("div", { class: "pose-body" });
  body.appendChild(
    el("h5", {}, `${LAYOUT_LABEL[p.layout] || p.layout} · ${p.person_count} 人`),
  );
  if (p.interaction) {
    body.appendChild(el("div", { class: "rationale" }, p.interaction));
  }
  const personList = el("div", { class: "person-list" });
  (p.persons || []).forEach((person) => personList.appendChild(renderPerson(person)));
  body.appendChild(personList);
  block.appendChild(body);
  return block;
}

function renderPerson(person) {
  const wrap = el("div", { class: "person" });
  wrap.appendChild(el("div", { class: "role" }, person.role));
  const ul = el("ul", {});
  const fields = [
    ["站姿", person.stance],
    ["上身", person.upper_body],
    ["手部", person.hands],
    ["视线", person.gaze],
    ["表情", person.expression],
    ["站位", person.position_hint],
  ];
  fields.forEach(([k, v]) => {
    if (!v) return;
    const li = el("li", {});
    const b = el("b", {}, k + "：");
    li.appendChild(b);
    li.appendChild(document.createTextNode(v));
    ul.appendChild(li);
  });
  wrap.appendChild(ul);
  return wrap;
}

// ===== tiny DOM helpers =====
function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null) continue;
    if (k === "class") node.className = v;
    else if (k === "style") node.style.cssText = v;
    else node.setAttribute(k, v);
  }
  if (Array.isArray(children)) {
    for (const c of children) {
      if (c == null) continue;
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
  } else if (typeof children === "string") {
    node.textContent = children;
  } else if (children) {
    node.appendChild(children);
  }
  return node;
}

function tag(text, kind) {
  return el("span", { class: "tag" + (kind ? " " + kind : "") }, text);
}

function metric(label, value) {
  return el("div", { style: "display: flex; gap: 4px; align-items: center;" }, [
    el("span", { class: "label" }, label),
    el("strong", {}, value),
  ]);
}
