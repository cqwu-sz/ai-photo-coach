# 机位探索：三源融合（POI + ARKit/WebXR 漫游 + LLM 相对机位）

本文档配套实现：[`backend/app/services/poi_lookup.py`](../backend/app/services/poi_lookup.py)、
[`backend/app/services/walk_geometry.py`](../backend/app/services/walk_geometry.py)、
[`backend/app/services/shot_fusion.py`](../backend/app/services/shot_fusion.py)。

## 一句话总结

把机位从「以用户脚下为原点的相对极坐标，最大 20 m」升级为「世界坐标系下的候选池」，
三个来源汇入同一池子：

1. **LLM relative**（兜底，永远有）—— 现行 `Angle.azimuth/distance/pitch`，挂在 `ShotPosition(kind=relative)`
2. **POI**（`poi_kb` / `poi_online`）—— 来自本地 sqlite 或在线 AMap/OSM
3. **SfM/VIO**（`sfm_ios` / `sfm_web`）—— 来自用户可选的 10–20 s 漫游段

## 数据流

```
client meta + walk_segment? + geo?
        │
        ▼
analyze_service.run
        ├─ poi_lookup.search_nearby (kb → AMap → OSM)
        ├─ walk_geometry.derive_candidates  (从 walk_segment 推导)
        ├─ provider.analyze (LLM)
        └─ shot_fusion.fuse(llm_shots, pois, sfm, env, geo)
                  │
                  ▼
        AnalyzeResponse.shots[i].position : ShotPosition
```

## 配置

`backend/app/config.py`（也可走 env 变量）：

| key | 默认 | 说明 |
|---|---|---|
| `AMAP_KEY` | `""` | AMap Place Search v3 key。空则跳过 AMap，直接用 OSM |
| `enable_poi_lookup` | `True` | 关掉则回退老链路（仅 relative） |
| `enable_walk_segment` | `True` | 关掉则忽略客户端上传的 `walk_segment` |
| `poi_lookup_timeout_sec` | `1.5` | 单次 POI 检索总预算 |
| `poi_lookup_radius_m` | `300` | 围绕 GeoFix 检索半径 |

`AMAP_KEY` 任意一种获取方式：[AMap 控制台](https://lbs.amap.com/)，免费配额足够个人/小型测试。

## 客户端

- iOS：[`ios/AIPhotoCoach/Features/EnvCapture/WalkSegmentRecorder.swift`](../ios/AIPhotoCoach/Features/EnvCapture/WalkSegmentRecorder.swift) +
  [`WalkSegmentSheet.swift`](../ios/AIPhotoCoach/Features/EnvCapture/WalkSegmentSheet.swift)。把
  捕获到的 `WalkSegment` 设到 `EnvCaptureViewModel.pendingWalkSegment`，
  下一次 `stopAndAnalyze` 自动一并上传。
- Web：[`web/js/walk_segment.js`](../web/js/walk_segment.js)。`startWalk()` 优先 WebXR，
  退回 DeviceMotion；`stop()` 返回 `WalkSegment` JSON，赋给
  `window.__pendingWalkSegment`，[`web/js/capture.js`](../web/js/capture.js) 自动拾取。

## 渲染

- iOS：[`ShotPositionCard.swift`](../ios/AIPhotoCoach/Features/Recommendation/ShotPositionCard.swift)
  `relative` → 罗盘箭头；`absolute` → MapKit 双 pin + 步行距离。
- Web：[`shot_position_card.js`](../web/js/shot_position_card.js)
  `relative` → SVG 罗盘；`absolute` → 懒加载 Leaflet 双 pin。

## 兼容性矩阵

| GeoFix | walk_segment | POI 命中 | 输出 |
|---|---|---|---|
| ✓ | ✓ | ✓ | 三源融合，最完整 |
| ✓ | ✗ | ✓ | LLM relative + POI absolute |
| ✓ | ✓ | ✗ | LLM relative + SfM absolute |
| ✓ | ✗ | ✗ | LLM relative + 在线 POI 兜底（命中即缓存） |
| ✗ | — | — | 退化为现行链路（仅 relative） |

任何上游失败都不会阻塞 `/analyze`，只是候选池变小。`shot_fusion.fuse`
保证返回的 `shots` 列表里**至少有一条 `relative` 兜底机位**，方便
没有地图组件的旧客户端继续工作。

## 测试

```bash
cd backend
python -m pytest tests/test_poi_lookup_smoke.py \
                 tests/test_walk_geometry_smoke.py \
                 tests/test_shot_fusion_smoke.py -v
```

外滩端到端冒烟（mock 模式）：

```python
from app.config import get_settings
from app.models import CaptureMeta, FrameMeta, GeoFix, WalkSegment, WalkPose
from app.services.analyze_service import AnalyzeService
import asyncio

walk = WalkSegment(source='arkit', initial_heading_deg=0.0,
                   poses=[WalkPose(t_ms=i*500, x=0, y=i*3.0, z=0) for i in range(8)])
meta = CaptureMeta(person_count=1,
                   frame_meta=[FrameMeta(index=i, azimuth_deg=i*45) for i in range(4)],
                   geo=GeoFix(lat=31.2389, lon=121.4905), walk_segment=walk)
resp = asyncio.run(AnalyzeService(get_settings()).run(meta, [b'']*4, []))
for s in resp.shots:
    print(s.id, s.position.kind, s.position.source, s.position.name_zh)
```

期望输出包括至少 1 条 `relative` 兜底 + 1 条 `sfm_ios` / `poi_*` 的 absolute 机位。

## v14 Updates (W1-W7)

### UGC tier (W2)

Feedback now accepts `chosen_position` + `rating`. When rating >= 4 and the chosen position is absolute with no nearby POI hit (within 50 m), the spot is promoted into `user_spots` (sqlite). Once a spot has 3+ upvotes it is returned by `poi_lookup.search_nearby` with `source='poi_ugc'`.

### Indoor POI (W1.2)

New kind `ShotPosition.indoor` plus the `IndoorContext` record. Backed by `backend/data/indoor_buildings.json` registry (extensible without code changes). When the user GPS lands within 80 m of a known building, every hotspot becomes a candidate.

### Walk routes (W3)

`ShotPosition.walk_route` carries an `WalkRoute` (distance / duration / polyline / steps). `analyze_service` fans out concurrent route lookups for every absolute candidate beyond `route_planner_distance_threshold_m` (default 50 m). AMap is the primary provider with a straight-line crow's-flight fallback.

### Triangulation upgrade (W4)

When the LLM relative shot azimuth lines up with a triangulated FarPoint (within 8 deg), the shot is upgraded to absolute with `source='triangulated'`.

### Time-optimal (W7)

Response now includes `time_recommendation` derived from historical `shot_results` ratings within 50 m, when n >= 5.

