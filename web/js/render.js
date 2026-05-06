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

  const frames = loadFrames();
  const refInsp = loadRefInspiration();

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
  response.shots.forEach((shot, i) => {
    container.appendChild(renderShot(shot, i, frames));
  });
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

function renderShot(shot, idx, frames) {
  const card = el("section", { class: "section shot-card" });
  const header = el("div", { class: "shot-header" });
  header.appendChild(
    el("div", {}, [
      el("h4", { style: "display: inline;" }, `机位 #${idx + 1}`),
      shot.title ? el("span", { class: "subtitle" }, shot.title) : null,
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

  // ── HERO: visual mock-up with 2D / 3D toggle ──
  const heroWrap = el("div", { class: "hero-mode-wrap" });
  const toggle = el("div", { class: "hero-toggle" }, [
    el("button", { class: "hero-toggle-btn active", "data-mode": "2d", type: "button" }, "2D 合成图"),
    el("button", { class: "hero-toggle-btn", "data-mode": "3d", type: "button" }, "3D 场景 (含虚拟人物)"),
  ]);
  heroWrap.appendChild(toggle);
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
    import("./scene_3d.js")
      .then((mod) => {
        heroStage.innerHTML = "";
        const stage = el("div", { class: "hero-3d-stage" });
        heroStage.appendChild(stage);
        const personCount = (shot.poses?.[0]?.persons || []).length || 1;
        const picks = resolveAvatarPicks(loadAvatarPicks(), personCount);
        scene3DInstance = mod.createSceneView(stage, {
          panoramaUrl: loadPanoramaUrl(),
          shot,
          picks,
        });
        // Add a hint overlay
        const hint = el("div", { class: "hero-3d-hint" }, "拖屏 360° 看 / 双指缩放");
        stage.appendChild(hint);
        setTimeout(() => hint.classList.add("fade"), 3500);
      })
      .catch((e) => {
        heroStage.innerHTML = "";
        heroStage.appendChild(el("div", { class: "hero-3d-error" }, `3D 加载失败: ${e?.message || e}`));
      });
  }

  toggle.addEventListener("click", (e) => {
    const btn = e.target.closest(".hero-toggle-btn");
    if (!btn) return;
    [...toggle.children].forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    renderHero(btn.dataset.mode);
  });

  renderHero("2d");
  card.appendChild(heroWrap);

  // ── coach bubble: first-person rationale ──
  if (shot.coach_brief || shot.rationale) {
    const bubble = el("div", { class: "coach-bubble" });
    bubble.appendChild(el("div", { class: "coach-avatar" }, "📷"));
    const body = el("div", { class: "coach-body" });
    if (shot.coach_brief) {
      body.appendChild(el("p", { class: "coach-brief" }, `"${shot.coach_brief}"`));
    }
    if (shot.rationale) {
      body.appendChild(el("p", { class: "coach-rationale" }, shot.rationale));
    }
    bubble.appendChild(body);
    card.appendChild(bubble);
  }

  // ── secondary visuals: minimap + AR CTA ──
  const visualRow = el("div", { class: "shot-visual-row" });
  const mapWrap = el("div", { class: "shot-minimap" });
  mapWrap.appendChild(renderShotMinimap(shot.angle, { label: shot.title }));
  visualRow.appendChild(mapWrap);

  const ctaWrap = el("div", { class: "shot-cta" });
  const guideBtn = el(
    "button",
    { class: "btn btn-guide", type: "button" },
    [el("span", {}, "按这个方案试拍"), el("span", { class: "cta-arrow" }, "→")],
  );
  guideBtn.addEventListener("click", () => {
    saveCurrentShot({ shot, idx });
    location.href = "/web/guide.html";
  });
  ctaWrap.appendChild(guideBtn);
  ctaWrap.appendChild(
    el("p", { class: "cta-note" }, "切换到摄像头视图，AR 提示你转向并站位"),
  );
  visualRow.appendChild(ctaWrap);

  card.appendChild(visualRow);

  // ── collapsible technical details ──
  const details = el("details", { class: "shot-details" });
  details.appendChild(el("summary", {}, "查看相机参数 / 构图 / 角度详细"));
  details.appendChild(renderAngle(shot.angle));
  details.appendChild(renderComposition(shot.composition));
  details.appendChild(renderCamera(shot.camera));
  card.appendChild(details);

  card.appendChild(el("div", { class: "divider" }));
  card.appendChild(
    el(
      "h3",
      { style: "color: var(--text); margin: 0; text-transform: none; font-size: 15px; letter-spacing: 0;" },
      "姿势建议",
    ),
  );
  shot.poses.forEach((p) => card.appendChild(renderPose(p)));
  return card;
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
