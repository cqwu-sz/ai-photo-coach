# Photographic Works Corpus

Curated, deconstructed photographic works that the analyze pipeline
calls upon as **few-shot reference recipes** when picking shots in a
similar environment. This is the "I've seen 10000 great photos and can
recognise the moment" half of expertise — the theory half lives in
`../composition/`.

## Why this directory exists

The composition/ seeds answer *"what makes a good photo"* (Suler,
Barthes, HCB, anti-patterns). They are theoretical and abstract.
Works/ answers *"how did a real photographer execute that theory in a
scene like the one I'm standing in right now"*. The retrieval pipeline
matches the user's current environment fingerprint (landmark graph +
light_pro features) against this corpus and surfaces the best 5-10
recipes into the prompt as concrete few-shot examples.

## Schema

Each `*.json` file is **either** a single work dict **or** a list of
work dicts (mirrors how composition/ seeds are organised). One work
entry looks like:

```jsonc
{
  "id": "work_unsplash_abc123",
  "source": {
    "platform": "unsplash | xhs | flickr | 500px | pinterest | manual",
    "url":      "https://...",          // original page URL (required when public)
    "author":   "@photographer_handle",  // attribution
    "license":  "unsplash | cc-by | editorial | unknown"
  },
  "image_uri": "works/unsplash/abc123.jpg",  // path inside web/img/works/
  "thumbnail_uri": "works/unsplash/abc123_thumb.jpg",
  "scene_tags":      ["urban", "street", "alleyway"],
  "light_tags":      ["golden_hour", "side_light", "rim"],
  "composition_tags": ["leading_line", "rule_of_thirds", "frame_within_frame"],
  "person_count":    1,
  "why_good": [
    "光线从右后方贴墙打来,把人物轮廓勾出一圈金边",
    "墙缝引导线把视线从右下推到模特位置"
  ],
  "reusable_recipe": {
    "subject_pose":   "侧身45度,头微微抬起朝光",
    "camera_position": "蹲低到主体腰部高度,距离2.5米",
    "framing":        "主体占画面右1/3,左2/3留给引导线",
    "focal_length":   "50mm equivalent (tele_2x on iPhone)",
    "aperture":       "f/1.8 大光圈虚化背景",
    "post_style":     "胶片暖色 + 中等褪色 + 微提阴影",
    "applicable_to": {
      "min_landmarks":    2,
      "needs_stereo":     false,
      "needs_leading_line": true,
      "scene_modes":       ["portrait", "documentary"]
    }
  },
  "embedding": null,           // backfilled by CLIP indexing script
  "added_at":  "2026-05-13",
  "reviewed_by": "team"        // human reviewer initials
}
```

### Required fields
- `id`, `source.platform`, `source.url` (when not manual), `image_uri`
- `scene_tags`, `light_tags`, `composition_tags`
- `reusable_recipe.subject_pose`, `.camera_position`, `.framing`
- `applicable_to.scene_modes` (so the retrieval can filter)

### Loader

```python
from app.services.knowledge import load_works
works = load_works("backend/app/knowledge/works")
```

## Curation flow

1. Source images via `scripts/works_crawler/` (Unsplash / xhs / etc.)
2. Auto-annotate with `scripts/works_crawler/auto_annotate.py` (LLM
   produces a draft JSON conforming to the schema above)
3. Human review via `scripts/works_crawler/review_ui.py` Flask app —
   approve / edit / reject. Approved entries are written here.
4. Run `scripts/works_crawler/build_index.py` to backfill CLIP
   embeddings into the saved JSONs (so retrieval doesn't need a model
   at request time).

## Provenance & licensing

- We never ship raw photos for which we don't have explicit licence or
  fair-use rationale. Unsplash is the safest default. xhs items live
  only in the **maintainer's local review queue** and are surfaced to
  end users only via similarity-based recipe text (no thumbnail
  serving) unless the source explicitly permits it.
- Every entry must have a `source.url`. Empty source → reject in
  review.
- `source.license == "unknown"` entries are kept private (not served
  thumbnails) and only used for embedding similarity.
