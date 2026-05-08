// 4-step onboarding wizard for the home screen.
//
// Steps:
//   1 — SCENE       场景
//   2 — CAST        人数 + 虚拟角色  (skipped automatically when scene = scenery)
//   3 — TONE        风格 + 质量档
//   4 — REVIEW      总览 + 大 CTA
//
// State is persisted in localStorage so a returning user lands directly
// on Step 4 with their previous picks. Steps 1-3 are still reachable via
// the progress bar beads or the summary chips on Step 4.
//
// This module purposefully knows nothing about avatars, references, or the
// /analyze API — it only drives navigation. `index.js` listens to
// `wizard:step` / `wizard:enter` events and refreshes the relevant pieces
// of UI when needed.

const KEY_PROGRESS = "aphc.wizardProgress";
const STEPS = [1, 2, 3, 4];

/** Read the persisted wizard progress, returning `null` if missing/broken. */
function readProgress() {
  try {
    const raw = localStorage.getItem(KEY_PROGRESS);
    if (!raw) return null;
    const obj = JSON.parse(raw);
    if (!obj || typeof obj !== "object") return null;
    return {
      furthestStep: Math.max(1, Math.min(4, Number(obj.furthestStep) || 1)),
      completed: !!obj.completed,
      lastUpdated: Number(obj.lastUpdated) || 0,
    };
  } catch {
    return null;
  }
}

function writeProgress(p) {
  try {
    localStorage.setItem(
      KEY_PROGRESS,
      JSON.stringify({
        furthestStep: p.furthestStep,
        completed: p.completed,
        lastUpdated: Date.now(),
      }),
    );
  } catch {
    /* private mode etc. */
  }
}

export function clearWizardProgress() {
  try { localStorage.removeItem(KEY_PROGRESS); } catch {}
}

/**
 * Bootstrap the wizard.
 *
 * @param {object} opts
 * @param {() => string|undefined} [opts.getSceneMode]  read current scene
 *        so we can skip step 2 in scenery mode.
 * @param {(step: number) => void} [opts.onValidate]    optional gate that
 *        throws to block the next-button when the current step isn't done.
 * @returns {object} controller with `goto(step)`, `next()`, `back()`,
 *        `current()`, `markCompleted()`.
 */
export function initWizard(opts = {}) {
  const { getSceneMode = () => "portrait" } = opts;
  const stage = document.getElementById("wizard-stage");
  const stepEls = [...document.querySelectorAll(".step-view[data-step]")];
  const beadEls = [...document.querySelectorAll(".progress-bead[data-jump]")];
  const beadLineEls = [...document.querySelectorAll(".progress-bead-line")];
  const backBtn = document.getElementById("back-btn");
  const nextBtn = document.getElementById("next-btn");
  const nextLabel = document.getElementById("next-label");
  const summaryChips = [...document.querySelectorAll(".summary-chip[data-jump]")];

  if (!stage || stepEls.length === 0 || !backBtn || !nextBtn) {
    console.warn("[wizard] missing required DOM, aborting init");
    return { goto() {}, next() {}, back() {}, current: () => 1, markCompleted() {} };
  }

  let currentStep = 1;
  let furthestStep = 1;

  // --- helpers --------------------------------------------------------------

  /** Resolve which step to show given direction & scenery mode. */
  function effectiveStep(target, direction) {
    let s = Math.max(1, Math.min(4, target));
    // Scenery mode skips Step 2 (no avatars to choose).
    if (s === 2 && getSceneMode() === "scenery") {
      s = direction === "back" ? 1 : 3;
    }
    return s;
  }

  function setProgressUI() {
    beadEls.forEach((b, i) => {
      const step = Number(b.dataset.jump);
      b.classList.toggle("active", step === currentStep);
      b.classList.toggle("completed", step < currentStep || step <= furthestStep);
      // Disallow jumping forward past the furthest reached step.
      const reachable = step <= Math.max(currentStep, furthestStep);
      b.disabled = !reachable;
      b.classList.toggle("locked", !reachable);
    });
    beadLineEls.forEach((line, i) => {
      const before = beadEls[i];
      const stepBefore = before ? Number(before.dataset.jump) : 0;
      line.style.background = stepBefore < currentStep
        ? "linear-gradient(90deg, var(--accent), var(--accent-3))"
        : "rgba(255,255,255,0.12)";
    });
  }

  function setFooterUI() {
    backBtn.disabled = currentStep === 1;
    if (currentStep === 4) {
      nextLabel.textContent = "开始环视拍摄";
    } else if (currentStep === 3 && getSceneMode() === "scenery") {
      nextLabel.textContent = "下一步：开拍";
    } else {
      nextLabel.textContent = "继续";
    }
  }

  function show(step, direction = "forward") {
    const target = effectiveStep(step, direction);

    stepEls.forEach((view) => {
      const s = Number(view.dataset.step);
      const wasActive = view.classList.contains("is-active");
      if (s === target) {
        view.classList.add("is-active");
        view.classList.toggle("back-direction", direction === "back");
        view.scrollIntoView?.({ block: "start", behavior: "instant" });
      } else if (wasActive) {
        view.classList.remove("is-active");
      }
    });

    currentStep = target;
    if (currentStep > furthestStep) furthestStep = currentStep;

    setProgressUI();
    setFooterUI();

    // Persist immediately so a refresh keeps you in place.
    writeProgress({
      furthestStep,
      completed: furthestStep >= 4,
    });

    document.dispatchEvent(
      new CustomEvent("wizard:step", { detail: { step: currentStep, direction } }),
    );

    // Top of page when changing step (mobile-friendly).
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function next() {
    try { opts.onValidate?.(currentStep); } catch (e) {
      // Validation failure: surface a tiny shake animation.
      nextBtn.animate(
        [
          { transform: "translateX(0)" },
          { transform: "translateX(-4px)" },
          { transform: "translateX(4px)" },
          { transform: "translateX(0)" },
        ],
        { duration: 240 },
      );
      console.warn("[wizard] step", currentStep, "blocked:", e?.message || e);
      return;
    }
    if (currentStep === 4) {
      // Last step's CTA is "start capture", handed off to index.js.
      document.dispatchEvent(new CustomEvent("wizard:start-capture"));
      return;
    }
    show(currentStep + 1, "forward");
  }

  function back() {
    if (currentStep === 1) return;
    show(currentStep - 1, "back");
  }

  function goto(step, direction = step < currentStep ? "back" : "forward") {
    show(step, direction);
  }

  // --- bind UI --------------------------------------------------------------

  backBtn.addEventListener("click", back);
  nextBtn.addEventListener("click", next);
  beadEls.forEach((b) => {
    b.addEventListener("click", () => {
      const step = Number(b.dataset.jump);
      if (b.disabled) return;
      goto(step);
    });
  });
  summaryChips.forEach((c) => {
    c.addEventListener("click", () => goto(Number(c.dataset.jump)));
  });

  // Keyboard niceties: Enter = next, Esc = back.
  document.addEventListener("keydown", (e) => {
    const tag = (e.target?.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea") return;
    if (e.key === "Enter") {
      next();
    } else if (e.key === "Escape") {
      back();
    }
  });

  // --- decide starting step -------------------------------------------------

  const progress = readProgress();
  if (progress?.completed && progress.furthestStep >= 4) {
    // Returning user: drop them on the review page with a welcome banner.
    furthestStep = 4;
    show(4, "forward");
    const banner = document.getElementById("review-welcome");
    if (banner) banner.hidden = false;
  } else if (progress?.furthestStep) {
    furthestStep = progress.furthestStep;
    show(progress.furthestStep, "forward");
  } else {
    show(1, "forward");
  }

  return {
    goto,
    next,
    back,
    current: () => currentStep,
    markCompleted() {
      furthestStep = 4;
      writeProgress({ furthestStep: 4, completed: true });
    },
  };
}
