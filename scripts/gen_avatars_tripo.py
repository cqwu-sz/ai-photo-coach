"""v7 Phase B+ — generate 8 photorealistic, Mixamo-rigged avatars via Tripo3D.

Why this script exists
----------------------
Phase B initially shipped procedural placeholder glb (geometry-cobble
"block men"). Visual quality was unacceptable. The real RPM creator
path is unreachable from China-network deployments (DNS for
readyplayer.me is blocked here). Tripo3D is the only viable
fully-automatic high-quality pipeline:

  1. text_to_model with a curated prompt per preset → static glb
  2. check_riggable → boolean
  3. rig_model with spec=MIXAMO → glb with mixamorig:* skeleton
     (drop-in compatible with the 30 Mixamo animation glbs we already
     ship in web/avatars/animations/, AND with anything you download
     from Mixamo later — same bone names)
  4. download glb → web/avatars/preset/<preset_id>.glb
  5. (optional) convert_model format=USDZ → ios/AIPhotoCoach/
     Resources/Avatars/<preset_id>.usdz

How to run
----------
1. Sign up at https://platform.tripo3d.com/ (free Basic plan = 300
   credits/month, enough for 6–8 rigged avatars).
2. Settings → API Keys → create a key (`tsk_...`).
3. Run:

       python scripts/gen_avatars_tripo.py --api-key tsk_xxx

   Or:

       set TRIPO_API_KEY=tsk_xxx
       python scripts/gen_avatars_tripo.py

Optional flags
--------------
    --only male_casual_25,female_casual_22   # generate just these
    --skip-rig                               # skip Mixamo rigging
    --skip-usdz                              # skip iOS USDZ export
    --quality detailed                       # texture quality
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from tripo3d import RigSpec, RigType, TaskStatus, TripoClient


REPO_ROOT = Path(__file__).resolve().parent.parent
PRESET_DIR = REPO_ROOT / "web" / "avatars" / "preset"
USDZ_DIR = REPO_ROOT / "ios" / "AIPhotoCoach" / "Resources" / "Avatars"
LOG_DIR = REPO_ROOT / "scripts" / "_tripo_logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


# Each preset gets a carefully tuned prompt + negative_prompt to keep
# the output consistent with the spec ("photorealistic stylized human,
# T-pose, full body, no accessories"). Tone the prompts are designed to
# elicit *cohesive* characters (not random poses); rigging requires a
# clean T-pose anyway.
@dataclass
class PresetSpec:
    id: str
    name_zh: str
    prompt: str
    negative_prompt: str = (
        "low quality, deformed, blurry, multiple people, weapons, animals, "
        "extra limbs, bad anatomy, cropped head, cropped feet, accessories, "
        "fantasy armor, robes, costume, helmets"
    )


PRESETS: list[PresetSpec] = [
    PresetSpec(
        id="male_casual_25",
        name_zh="休闲男 · 25",
        prompt=(
            "Photorealistic stylized 3D character, full body, T-pose, "
            "Asian young man around 25 years old, short black hair, "
            "wearing a dark-blue cotton T-shirt and slim jeans, white sneakers, "
            "neutral facial expression, looking forward, balanced proportions, "
            "studio lighting, plain background, game-ready PBR textures"
        ),
    ),
    PresetSpec(
        id="male_business_35",
        name_zh="商务男 · 35",
        prompt=(
            "Photorealistic stylized 3D character, full body, T-pose, "
            "Asian businessman around 35 years old, short clean haircut, "
            "wearing a charcoal-grey two-piece suit, white shirt with tie, "
            "polished black leather shoes, confident neutral expression, "
            "looking forward, studio lighting, plain background, "
            "game-ready PBR textures"
        ),
    ),
    PresetSpec(
        id="male_athletic_28",
        name_zh="运动男 · 28",
        prompt=(
            "Photorealistic stylized 3D character, full body, T-pose, "
            "Asian athletic young man around 28 years old, short brown hair, "
            "wearing a red short-sleeve sport top and black running shorts, "
            "white running shoes, fit muscular build, neutral expression, "
            "looking forward, studio lighting, plain background, "
            "game-ready PBR textures"
        ),
    ),
    PresetSpec(
        id="female_casual_22",
        name_zh="休闲女 · 22",
        prompt=(
            "Photorealistic stylized 3D character, full body, T-pose, "
            "Asian young woman around 22 years old, long brown hair, "
            "wearing a soft pink hoodie and blue denim jeans, white sneakers, "
            "natural neutral expression, looking forward, slender build, "
            "studio lighting, plain background, game-ready PBR textures"
        ),
    ),
    PresetSpec(
        id="female_elegant_30",
        name_zh="优雅女 · 30",
        prompt=(
            "Photorealistic stylized 3D character, full body, T-pose, "
            "Asian elegant woman around 30 years old, shoulder-length black hair, "
            "wearing a wine-red knee-length dress, black low heels, "
            "graceful neutral expression, looking forward, fashion-illustration "
            "proportions, studio lighting, plain background, "
            "game-ready PBR textures"
        ),
    ),
    PresetSpec(
        id="female_artsy_25",
        name_zh="文艺女 · 25",
        prompt=(
            "Photorealistic stylized 3D character, full body, T-pose, "
            "Asian artsy young woman around 25 years old, wavy auburn hair, "
            "wearing a beige linen sweater and cream long skirt, brown ankle boots, "
            "calm thoughtful expression, looking forward, slender bohemian style, "
            "studio lighting, plain background, game-ready PBR textures"
        ),
    ),
    PresetSpec(
        id="child_boy_8",
        name_zh="男孩 · 8",
        prompt=(
            "Photorealistic stylized 3D character, full body, T-pose, "
            "Asian boy about 8 years old, short black hair, "
            "wearing a sky-blue T-shirt and dark-blue shorts, white sneakers, "
            "happy neutral expression, looking forward, child proportions "
            "(larger head, shorter limbs), studio lighting, plain background, "
            "game-ready PBR textures"
        ),
    ),
    PresetSpec(
        id="child_girl_8",
        name_zh="女孩 · 8",
        prompt=(
            "Photorealistic stylized 3D character, full body, T-pose, "
            "Asian girl about 8 years old, twin braids brown hair, "
            "wearing a rose-pink summer dress, white sneakers, "
            "cheerful neutral expression, looking forward, child proportions, "
            "studio lighting, plain background, game-ready PBR textures"
        ),
    ),
    PresetSpec(
        id="female_youth_18",
        name_zh="少女 · 18",
        prompt=(
            "Photorealistic stylized 3D character, full body, T-pose, "
            "Asian young woman around 18 years old, fresh and pretty, "
            "black hair tied in a single high bun (odango / top knot) with "
            "a few loose face-framing strands, wearing a short white "
            "summer dress with cap sleeves and a soft pleated skirt above "
            "the knee, white low sneakers, bright cheerful neutral expression, "
            "looking forward, slender youthful build, studio lighting, "
            "plain background, game-ready PBR textures"
        ),
    ),
]


def log(msg: str) -> None:
    # Force ASCII-safe output on Windows GBK consoles. Replace common
    # non-ASCII status glyphs the script uses, and fall back to encoding
    # via stdout.encoding with replacement for any other char.
    safe = (msg.replace("\u2713", "[OK]")
                .replace("\u2717", "[X]")
                .replace("\u2022", "*")
                .replace("\u2192", "->")
                .replace("\u26a0", "[!]"))
    enc = (getattr(sys.stdout, "encoding", None) or "utf-8")
    try:
        safe.encode(enc)
    except (UnicodeEncodeError, LookupError):
        safe = safe.encode(enc, errors="replace").decode(enc, errors="replace")
    print(f"[gen] {safe}", flush=True)


async def generate_one(client: TripoClient, spec: PresetSpec, opts) -> dict:
    log(f"=== {spec.id} ({spec.name_zh}) ===")
    summary: dict[str, object] = {"id": spec.id}
    workdir = LOG_DIR / spec.id
    workdir.mkdir(parents=True, exist_ok=True)

    # 1) text_to_model
    log(f"  1/4  text_to_model — submitting prompt …")
    t2m_id = await client.text_to_model(
        prompt=spec.prompt,
        negative_prompt=spec.negative_prompt,
        texture=True,
        pbr=True,
        texture_quality=opts.quality,
        auto_size=False,
        compress=False,
    )
    summary["text_task_id"] = t2m_id
    log(f"       task_id={t2m_id} — waiting (this can take 1-3 minutes)…")
    t2m_task = await client.wait_for_task(t2m_id, polling_interval=5.0,
                                          timeout=600, verbose=False)
    if t2m_task.status != TaskStatus.SUCCESS:
        log(f"  ✗ text_to_model failed: {t2m_task.status}")
        summary["error"] = f"text_to_model {t2m_task.status}"
        return summary
    log(f"       OK (status=SUCCESS)")

    final_task_id = t2m_id  # we'll either rig this or keep it

    # 2) check_riggable + rig_model (skipped if --skip-rig)
    if not opts.skip_rig:
        log("  2/4  check_riggable …")
        chk_id = await client.check_riggable(t2m_id)
        chk_task = await client.wait_for_task(chk_id, polling_interval=4.0,
                                              timeout=600)
        if (chk_task.status != TaskStatus.SUCCESS
                or not getattr(chk_task.output, "riggable", False)):
            log("  ⚠ Tripo says model is NOT riggable — keeping static mesh")
            summary["riggable"] = False
        else:
            log("       riggable=True — submitting rig_model (Mixamo spec)")
            rig_id = await client.rig_model(
                original_model_task_id=t2m_id,
                out_format="glb",
                rig_type=RigType.BIPED,
                spec=RigSpec.MIXAMO,
            )
            summary["rig_task_id"] = rig_id
            rig_task = await client.wait_for_task(rig_id,
                                                  polling_interval=5.0,
                                                  timeout=600,
                                                  verbose=False)
            if rig_task.status == TaskStatus.SUCCESS:
                final_task_id = rig_id
                summary["riggable"] = True
                log("       OK (Mixamo skeleton attached)")
            else:
                log(f"  ⚠ rig_model failed: {rig_task.status} — using static mesh")
                summary["riggable"] = False

    # 3) download glb → web/avatars/preset/<id>.glb
    log("  3/4  download glb …")
    final_task = await client.get_task(final_task_id)
    files = await client.download_task_models(final_task, str(workdir))
    glb_src = files.get("pbr_model") or files.get("model") or files.get("base_model")
    if not glb_src:
        log("  ✗ no glb in download_task_models output")
        summary["error"] = "no glb downloaded"
        return summary
    glb_dst = PRESET_DIR / f"{spec.id}.glb"
    PRESET_DIR.mkdir(parents=True, exist_ok=True)
    backup = PRESET_DIR / f"{spec.id}.glb.placeholder"
    if glb_dst.exists() and not backup.exists():
        shutil.copy2(glb_dst, backup)
    shutil.copy2(glb_src, glb_dst)
    summary["glb"] = str(glb_dst.relative_to(REPO_ROOT))
    log(f"       OK → {glb_dst.relative_to(REPO_ROOT)} "
        f"({glb_dst.stat().st_size / 1024:.0f} KB)")

    # 4) convert to USDZ for iOS (skipped if --skip-usdz)
    if not opts.skip_usdz:
        log("  4/4  convert_model → USDZ …")
        try:
            usdz_id = await client.convert_model(
                original_model_task_id=final_task_id,
                format="USDZ",
                texture_size=2048,
                texture_format="PNG",
                with_animation=summary.get("riggable", False),
            )
            usdz_task = await client.wait_for_task(usdz_id,
                                                   polling_interval=5.0,
                                                   timeout=600)
            if usdz_task.status == TaskStatus.SUCCESS:
                usdz_files = await client.download_task_models(
                    usdz_task, str(workdir))
                usdz_src = (usdz_files.get("pbr_model")
                            or usdz_files.get("model"))
                if usdz_src and usdz_src.endswith(".usdz"):
                    USDZ_DIR.mkdir(parents=True, exist_ok=True)
                    usdz_dst = USDZ_DIR / f"{spec.id}.usdz"
                    shutil.copy2(usdz_src, usdz_dst)
                    summary["usdz"] = str(usdz_dst.relative_to(REPO_ROOT))
                    log(f"       OK → {usdz_dst.relative_to(REPO_ROOT)}")
                else:
                    log("  ⚠ convert returned no .usdz file")
            else:
                log(f"  ⚠ convert_model failed: {usdz_task.status}")
        except Exception as e:
            log(f"  ⚠ USDZ skipped: {e}")
    else:
        log("  4/4  USDZ export skipped (--skip-usdz)")

    return summary


async def main_async(opts) -> int:
    api_key = opts.api_key or os.environ.get("TRIPO_API_KEY")
    if not api_key:
        print("ERROR: no TRIPO_API_KEY set.\n"
              "  pass --api-key tsk_xxx OR set TRIPO_API_KEY env var.\n"
              "  Get one at https://platform.tripo3d.com/", file=sys.stderr)
        return 2

    selected = PRESETS
    if opts.only:
        wanted = {x.strip() for x in opts.only.split(",") if x.strip()}
        selected = [p for p in PRESETS if p.id in wanted]
        if not selected:
            print(f"ERROR: --only filter {wanted} matched no preset", file=sys.stderr)
            return 2

    # Tripo3D ships TWO independent regions, with separate accounts +
    # separate keys + separate domains:
    #   - GLOBAL (.ai)  → IS_GLOBAL=True  (default)
    #   - CHINA  (.com) → IS_GLOBAL=False  ← keys from platform.tripo3d.com
    # Keys and balances do not transfer between them. Auto-detect by
    # probing the global endpoint first, fall back to China.
    is_global = not opts.china
    async with TripoClient(api_key=api_key, IS_GLOBAL=is_global) as client:
        try:
            balance = await client.get_balance()
            log(f"Tripo balance: {balance.balance} (frozen={balance.frozen})")
        except Exception as e:
            log(f"could not check balance: {e}")

        results: list[dict] = []
        for spec in selected:
            try:
                r = await generate_one(client, spec, opts)
                results.append(r)
            except Exception as e:
                log(f"  ✗ {spec.id}: unhandled error: {e}")
                results.append({"id": spec.id, "error": str(e)})

        log("=" * 50)
        ok = [r for r in results if "error" not in r and r.get("glb")]
        bad = [r for r in results if "error" in r or not r.get("glb")]
        log(f"DONE.  {len(ok)} succeeded · {len(bad)} failed")
        for r in results:
            tag = "✓" if r.get("glb") and "error" not in r else "✗"
            extra = []
            if r.get("riggable"):
                extra.append("rigged")
            if r.get("usdz"):
                extra.append("usdz")
            if r.get("error"):
                extra.append(f"err={r['error']}")
            log(f"  {tag} {r['id']:<22}  " + ("  ".join(extra) or "-"))
        return 0 if not bad else 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--api-key", help="Tripo3D API key (or set TRIPO_API_KEY)")
    p.add_argument("--only", help="comma-separated preset ids to generate")
    p.add_argument("--skip-rig", action="store_true",
                   help="skip Mixamo rigging (saves credits but no animations)")
    p.add_argument("--skip-usdz", action="store_true",
                   help="skip iOS USDZ export (saves credits)")
    p.add_argument("--quality", choices=["standard", "detailed"], default="standard",
                   help="texture quality (default: standard)")
    p.add_argument("--china", action="store_true",
                   help="use China endpoint (api.tripo3d.com); default is "
                   "global (api.tripo3d.ai). Keys from platform.tripo3d.com "
                   "are China-only.")
    opts = p.parse_args()

    return asyncio.run(main_async(opts))


if __name__ == "__main__":
    sys.exit(main())
