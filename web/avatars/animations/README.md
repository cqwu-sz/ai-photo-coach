# Mixamo Animation Pack — v7

This directory holds the 30 Mixamo `.glb` animations the avatar loader
plays on the preset characters. Animations are tied to LLM pose KB ids
via [`backend/app/knowledge/animations/pose_to_mixamo.json`](../../../backend/app/knowledge/animations/pose_to_mixamo.json).

## Asset naming

`<animation-id>.glb` — single rigged glb with embedded animation track,
no mesh (the animation re-targets at runtime onto the preset avatar).

## Animation list

### Single person (9)
- `idle_relaxed` — default standing idle, 30s loop
- `idle_lean_wall` — leans on imaginary wall, hand by hip
- `idle_sit_low_wall` — sitting on low edge, hands on knees
- `pose_hand_in_hair` — left hand running through hair
- `pose_back_view` — looking up, back to camera
- `pose_jumping` — jump up with arms wide (one-shot)
- `pose_lying_grass` — lying on back, hands behind head
- `pose_holding_object` — holding cup at chest height
- `walk_natural` — looped natural walk cycle

### Two-person (11)
- `couple_high_low` — A standing, B half-squat, mutual gaze
- `couple_forehead_touch` — foreheads touching, arms relaxed
- `couple_side_by_side` — both standing facing camera
- `couple_back_to_back` — backs touching, arms folded
- `couple_walk_handhold` — walking together, hands held
- `couple_running` — running side-by-side, laughing
- `couple_dancing` — slow waltz spin
- `couple_seated_steps` — sitting on steps, casual chat
- `couple_embrace` — full hug
- `couple_piggyback` — A piggybacking B
- `family_lift_child` — adult lifting child up

### Three-person (6)
- `group_triangle_pose` — A center, B/C flanking
- `group_circle_jump` — three in a circle, one jumping
- `group_diagonal_walk` — diagonal staggered walk
- `group_walking_line` — abreast walking line
- `group_huddle` — tight huddle, leaning in
- `family_three_seated` — sitting on a couch / bench

### Four-person (2)
- `group_diamond_pose` — diamond formation, A center-front
- `group_four_couch` — four sitting on couch, relaxed

## Generating the assets

1. Sign in to [Mixamo](https://www.mixamo.com) (free Adobe account)
2. Search for the action by name (most are direct matches; for couple
   variants you'll combine two single-actor anims and align in DCC tool)
3. Configure: 30 fps, in-place, no skin, full-bone export
4. Download as `.fbx`
5. Convert to glb with [fbx2gltf](https://github.com/godotengine/fbx2gltf):
   ```bash
   fbx2gltf -i input.fbx -o output.glb --keep-attribute=auto --no-flip-v
   ```
6. Rename to `<animation-id>.glb` and drop here.

## iOS conversion

The same `bash scripts/glb_to_usdz.sh` script handles both presets and
animations. Animations land in `ios/AIPhotoCoach/Resources/Animations/`
as `.usdz` files.

## License

Mixamo animations are royalty-free for any project (Adobe Terms of Use).
ReadyPlayerMe presets are under CC-BY 4.0 — attribution is auto-rendered
in the app's Settings → Credits screen.
