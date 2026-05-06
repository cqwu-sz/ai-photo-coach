# Pose Library Schema

Each pose lives at
`backend/app/knowledge/poses/<id>.json` with a sibling PNG thumbnail.

## JSON shape

```json
{
  "id": "pose_two_high_low_001",
  "person_count": 2,
  "layout": "high_low_offset",
  "summary": "two people, A standing, B half-squat, mutual gaze",
  "tags": ["couple", "friends", "interactive"],
  "best_for": ["golden_hour", "outdoor_park"],
  "thumbnail": "pose_two_high_low_001.png"
}
```

## Required fields

- `id` - unique slug. Convention: `pose_<count>_<layout>_<seq>`.
- `person_count` - integer.
- `layout` - one of the `Layout` enum values in `shared/schema/analyze.openapi.yaml`.
- `summary` - short English description for the LLM digest.
- `thumbnail` - filename of the matching PNG (must live next to the JSON).

## Optional fields

- `tags` - free-form tags surfaced in the iOS UI.
- `best_for` - context tags (lighting, scene, composition).

## Adding a new pose

1. Drop a 512x512 PNG (`pose_<id>.png`) in `backend/app/knowledge/poses/`.
2. Create the matching JSON file.
3. Restart the backend (the loader caches per-process).
4. iOS will pick it up on the next `/pose-library/manifest` fetch.
