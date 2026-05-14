"""Local Flask review UI for the works/ corpus.

Walks ``drafts/``, shows each draft alongside the image with editable
fields, and on Approve writes the result into
``backend/app/knowledge/works/<id>.json``.

Run:
    python scripts/works_crawler/review_ui.py
    # then open http://127.0.0.1:8765/

Design notes:
    * No JS framework — server-rendered HTML + tiny progressive JS, so
      the tool stays maintainable for a solo operator.
    * Approve refuses entries with empty ``source.url`` when source.
      platform != "manual".
    * Reject moves the draft into ``drafts/_rejected/`` (kept for
      auditing rather than truly deleted).
    * Edit is in-form: every text field is a textarea so you can clean
      up the LLM output before approving.
    * "Image not for public serving" toggle is on by default for
      ``xhs`` sources — when checked, ``image_uri`` / ``thumbnail_uri``
      are wiped before saving so the corpus carries only the recipe.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path

from flask import Flask, abort, redirect, request, send_from_directory, url_for

from common import (
    APPROVED_DIR,
    DRAFT_DIR,
    RAW_DIR,
    ROOT,
    ensure_dirs,
    read_json,
    write_json,
)

log = logging.getLogger("review_ui")
app = Flask(__name__)


PAGE_TPL = """<!doctype html>
<html lang=zh-CN><head><meta charset=utf-8>
<title>Works Crawler · Review {idx}/{total}</title>
<style>
  body {{ font-family: -apple-system, "Helvetica Neue", Helvetica, "PingFang SC", Arial; padding: 32px; max-width: 1200px; margin: auto; }}
  .row {{ display: flex; gap: 24px; }}
  .col-img {{ flex: 0 0 480px; }}
  .col-form {{ flex: 1; }}
  textarea {{ width: 100%; box-sizing: border-box; padding: 8px; font-family: inherit; font-size: 14px; }}
  input[type=text] {{ width: 100%; padding: 6px; box-sizing: border-box; font-size: 14px; }}
  label {{ display: block; font-weight: 600; margin: 12px 0 4px; color: #444; }}
  .pill {{ display: inline-block; padding: 2px 8px; border-radius: 12px; background: #eef; margin-right: 6px; font-size: 12px; }}
  .actions {{ position: sticky; bottom: 0; background: #fff; padding: 16px 0; border-top: 1px solid #eee; }}
  .btn {{ padding: 10px 24px; border: none; border-radius: 6px; cursor: pointer; font-size: 15px; margin-right: 8px; }}
  .approve {{ background: #34c759; color: #fff; }}
  .reject  {{ background: #ff3b30; color: #fff; }}
  .save    {{ background: #007aff; color: #fff; }}
  .meta    {{ font-size: 12px; color: #888; }}
  small.muted {{ color: #888; }}
  .field-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
</style></head><body>
<h2>Review {idx}/{total}: <code>{wid}</code></h2>
<div class=meta>source: <b>{platform}</b> · <a href="{src_url}" target=_blank>{src_url}</a> · author: {author} · license: <code>{license}</code></div>
<div class=row style="margin-top: 16px;">
  <div class="col-img">
    {img_block}
    <div class=meta style="margin-top:8px;">image_uri: <code>{image_uri}</code></div>
    {hint_block}
  </div>
  <div class="col-form">
  <form method=post action="{form_action}">
    <label>scene_tags (空格 / 逗号分隔)</label>
    <input type=text name=scene_tags value="{scene_tags}">
    <label>light_tags</label>
    <input type=text name=light_tags value="{light_tags}">
    <label>composition_tags</label>
    <input type=text name=composition_tags value="{composition_tags}">
    <label>person_count</label>
    <input type=text name=person_count value="{person_count}" style="width:120px;">
    <label>why_good (每行一条)</label>
    <textarea name=why_good rows=4>{why_good}</textarea>
    <h3 style="margin-top: 20px;">reusable_recipe</h3>
    <div class=field-grid>
      <div>
        <label>subject_pose</label>
        <textarea name=subject_pose rows=3>{subject_pose}</textarea>
      </div>
      <div>
        <label>camera_position</label>
        <textarea name=camera_position rows=3>{camera_position}</textarea>
      </div>
      <div>
        <label>framing</label>
        <textarea name=framing rows=3>{framing}</textarea>
      </div>
      <div>
        <label>focal_length / aperture</label>
        <input type=text name=focal_length value="{focal_length}">
        <input type=text name=aperture value="{aperture}" style="margin-top: 6px;">
      </div>
      <div style="grid-column: span 2;">
        <label>post_style</label>
        <textarea name=post_style rows=2>{post_style}</textarea>
      </div>
    </div>
    <label>applicable_to.scene_modes (空格分隔)</label>
    <input type=text name=scene_modes value="{scene_modes}">
    <label><input type=checkbox name=needs_stereo {needs_stereo_checked}> needs_stereo</label>
    <label><input type=checkbox name=needs_leading_line {needs_leading_checked}> needs_leading_line</label>
    {private_block}
    <label>reviewer initials</label>
    <input type=text name=reviewed_by value="{reviewed_by}" style="width:200px;">
    <div class=actions>
      <button class="btn save" type=submit name=op value=save>保存草稿</button>
      <button class="btn approve" type=submit name=op value=approve>批准入库</button>
      <button class="btn reject" type=submit name=op value=reject>拒绝</button>
      <a href="{prev_url}" style="margin-left: 12px;">← 上一条</a>
      <a href="{next_url}" style="margin-left: 8px;">下一条 →</a>
    </div>
  </form>
  </div>
</div>
</body></html>"""


def list_drafts() -> list[Path]:
    DRAFT_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(p for p in DRAFT_DIR.glob("*.json"))


def parse_tags(raw: str) -> list[str]:
    return [t for t in re.split(r"[\s,，]+", (raw or "").strip()) if t]


@app.route("/")
def index():
    drafts = list_drafts()
    if not drafts:
        return ("<h2>No drafts. Run auto_annotate.py to generate some.</h2>", 200)
    return redirect(url_for("review", idx=0))


@app.route("/img/<path:rel>")
def img(rel: str):
    """Serve images from raw/ and approved corpus locations."""
    candidates = [
        RAW_DIR.parent / rel,                # works for `raw/xhs/foo.jpg` etc.
        ROOT / rel,
        APPROVED_DIR.parent / rel,
    ]
    for c in candidates:
        c = c.resolve()
        if c.exists() and c.is_file():
            return send_from_directory(c.parent, c.name)
    abort(404)


@app.route("/review/<int:idx>", methods=["GET", "POST"])
def review(idx: int):
    drafts = list_drafts()
    if not drafts:
        return redirect(url_for("index"))
    idx = max(0, min(idx, len(drafts) - 1))
    path = drafts[idx]
    payload = read_json(path) or {}
    if not isinstance(payload, dict):
        abort(500, "malformed draft")

    if request.method == "POST":
        op = request.form.get("op", "save")
        updated = _apply_form(payload, request.form)
        if op == "save":
            write_json(path, updated)
            return redirect(url_for("review", idx=idx))
        if op == "reject":
            reject_dir = DRAFT_DIR / "_rejected"
            reject_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(reject_dir / path.name))
            return redirect(url_for("review", idx=min(idx, len(list_drafts()) - 1)))
        if op == "approve":
            ok, err = _approve(updated, request.form, path)
            if not ok:
                return (f"<h2>Approve failed</h2><pre>{err}</pre>"
                        f"<p><a href='{url_for('review', idx=idx)}'>back</a></p>", 400)
            return redirect(url_for("review", idx=min(idx, len(list_drafts()) - 1)))

    return _render_review(idx, drafts, payload)


def _apply_form(payload: dict, form) -> dict:
    p = dict(payload)
    p["scene_tags"] = parse_tags(form.get("scene_tags", ""))
    p["light_tags"] = parse_tags(form.get("light_tags", ""))
    p["composition_tags"] = parse_tags(form.get("composition_tags", ""))
    pc = (form.get("person_count") or "").strip()
    p["person_count"] = int(pc) if pc.isdigit() else None
    p["why_good"] = [l.strip() for l in (form.get("why_good") or "").splitlines() if l.strip()]
    recipe = dict(p.get("reusable_recipe") or {})
    for k in ("subject_pose", "camera_position", "framing",
              "focal_length", "aperture", "post_style"):
        recipe[k] = (form.get(k) or "").strip()
    app_to = dict(recipe.get("applicable_to") or {})
    app_to["scene_modes"] = parse_tags(form.get("scene_modes", "")) or ["portrait"]
    app_to["needs_stereo"] = bool(form.get("needs_stereo"))
    app_to["needs_leading_line"] = bool(form.get("needs_leading_line"))
    recipe["applicable_to"] = app_to
    p["reusable_recipe"] = recipe
    p["reviewed_by"] = (form.get("reviewed_by") or "").strip() or None
    return p


def _approve(payload: dict, form, draft_path: Path) -> tuple[bool, str]:
    src = payload.get("source") or {}
    platform = (src.get("platform") or "").strip()
    src_url = (src.get("url") or "").strip()
    if platform != "manual" and not src_url:
        return False, "source.url is required for non-manual sources"
    if not payload.get("scene_tags"):
        return False, "scene_tags must not be empty"
    if not payload.get("reusable_recipe", {}).get("subject_pose"):
        return False, "reusable_recipe.subject_pose must not be empty"

    # Privacy: when "image not for public serving" is ticked, strip image
    # paths from the approved record so we ship recipe-only.
    if form.get("private_image"):
        payload["image_uri"] = ""
        payload["thumbnail_uri"] = ""

    payload.setdefault("added_at", time.strftime("%Y-%m-%d"))
    wid = payload.get("id") or draft_path.stem
    out_path = APPROVED_DIR / f"{wid}.json"
    write_json(out_path, payload)

    archive = DRAFT_DIR / "_approved"
    archive.mkdir(parents=True, exist_ok=True)
    shutil.move(str(draft_path), str(archive / draft_path.name))
    log.info("approved %s -> %s", wid, out_path)
    return True, ""


def _render_review(idx: int, drafts: list[Path], payload: dict) -> str:
    total = len(drafts)
    wid = payload.get("id") or drafts[idx].stem
    src = payload.get("source") or {}
    platform = src.get("platform") or "?"
    src_url = src.get("url") or ""
    author = src.get("author") or "(无)"
    license = src.get("license") or "unknown"
    image_uri = payload.get("image_uri") or ""
    img_url = url_for("img", rel=image_uri) if image_uri else ""
    img_block = (f"<img src='{img_url}' style='max-width:480px; max-height:640px; border-radius: 8px;'>"
                 if img_url else "<div style='padding:24px; background:#f2f2f2; border-radius:8px;'>(image already privatised)</div>")
    hint = payload.get("scene_tags_hint") or ""
    hint_block = f"<div class=meta style='margin-top:8px;'><small class=muted>hint:</small> {hint}</div>" if hint else ""
    recipe = payload.get("reusable_recipe") or {}
    app_to = recipe.get("applicable_to") or {}
    private_default = "checked" if platform in ("xhs",) and image_uri else ""
    private_block = (
        f"<label style='margin-top:12px;'><input type=checkbox name=private_image {private_default}>"
        f" 仅入库 recipe,不公开图像 (xhs 默认勾上)</label>"
    )
    prev_idx = max(0, idx - 1)
    next_idx = min(total - 1, idx + 1)
    return PAGE_TPL.format(
        idx=idx + 1, total=total, wid=wid,
        platform=platform, src_url=src_url, author=author, license=license,
        img_block=img_block, image_uri=image_uri, hint_block=hint_block,
        scene_tags=" ".join(payload.get("scene_tags") or []),
        light_tags=" ".join(payload.get("light_tags") or []),
        composition_tags=" ".join(payload.get("composition_tags") or []),
        person_count=(payload.get("person_count") if payload.get("person_count") is not None else ""),
        why_good="\n".join(payload.get("why_good") or []),
        subject_pose=recipe.get("subject_pose") or "",
        camera_position=recipe.get("camera_position") or "",
        framing=recipe.get("framing") or "",
        focal_length=recipe.get("focal_length") or "",
        aperture=recipe.get("aperture") or "",
        post_style=recipe.get("post_style") or "",
        scene_modes=" ".join(app_to.get("scene_modes") or []),
        needs_stereo_checked=("checked" if app_to.get("needs_stereo") else ""),
        needs_leading_checked=("checked" if app_to.get("needs_leading_line") else ""),
        private_block=private_block,
        reviewed_by=payload.get("reviewed_by") or "",
        form_action=url_for("review", idx=idx),
        prev_url=url_for("review", idx=prev_idx),
        next_url=url_for("review", idx=next_idx),
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ensure_dirs()
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    sys.exit(main() or 0)
