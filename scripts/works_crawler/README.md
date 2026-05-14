# Works Crawler — solo-maintainer tooling

A standalone toolkit for the project maintainer to seed
`backend/app/knowledge/works/` from public photographic sources. Not
shipped in the iOS app, not invoked at request time — these scripts
exist only to populate the curated corpus.

## Why it lives outside the main package

- They depend on third-party CLIs (`gallery-dl`) and APIs (Unsplash
  napi, optional `xhs` library) we don't want as runtime deps.
- They write to local files and run a local Flask review UI — entirely
  out-of-band of the backend service.
- The output JSONs land in `backend/app/knowledge/works/`, which **is**
  shipped. So the entire pipeline produces auditable static data.

## Pipeline

```
[crawler] → raw/<source>/<image>.jpg + raw/<source>/<image>.json
            (image + minimal metadata)
        ↓
[auto_annotate.py] → drafts/<id>.json  (LLM-completed reusable_recipe)
        ↓
[review_ui.py    ] → backend/app/knowledge/works/<id>.json  (approved)
        ↓
[build_index.py  ] → fills `embedding` for approved works (CLIP)
```

## Quick start

```powershell
# 1. Install tools (one-time)
pip install -r scripts/works_crawler/requirements.txt
# Optionally for gallery-dl users:
pip install gallery-dl

# 2. Fetch a batch (env vars carry secrets — never commit)
$env:UNSPLASH_ACCESS_KEY = "<your unsplash dev key>"
python scripts/works_crawler/unsplash_fetch.py --query "urban portrait golden hour" --count 25

# 3. Auto-annotate the freshly-downloaded items
$env:OPENAI_API_KEY = "<llm key>"
python scripts/works_crawler/auto_annotate.py --in scripts/works_crawler/raw --out scripts/works_crawler/drafts

# 4. Review draft → approve → write to corpus
python scripts/works_crawler/review_ui.py
# then open http://localhost:8765/
```

## Safety / legal

- We **only** seed sources that are licensed for editorial / non-
  commercial use (Unsplash is the safest default).
- For xhs (Xiaohongshu) we use `xhs_fetch.py` for *maintainer*
  personal-review use only. We don't surface raw xhs imagery to end
  users; we only let the deconstructed recipe text drive prompt
  retrieval.
- Every approved JSON entry carries `source.url`, `source.author`, and
  `source.license`. The review UI refuses to approve entries missing
  these fields when `source.platform != "manual"`.
- Don't commit `raw/`, `drafts/`, or cookies. The `.gitignore` is set
  up accordingly.

## Files

- `unsplash_fetch.py` — query the Unsplash napi (uses existing dev key)
- `gallery_dl_wrapper.sh` — thin wrapper for 500px / Flickr / Behance /
  Pinterest fetches
- `xhs_fetch.py` — wraps ReaJason/xhs for the user-handle path
- `auto_annotate.py` — runs the deconstruction LLM and writes draft JSON
- `review_ui.py` — Flask app for approve / edit / reject
- `build_index.py` — backfills CLIP embeddings into approved works
- `requirements.txt`
