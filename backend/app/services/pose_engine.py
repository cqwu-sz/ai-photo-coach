"""Pose engine: post-process the LLM's pose suggestions against the local
pose library so the iOS app can show a real reference thumbnail.

Right now this is a simple lookup-by-id and layout-fallback. Phase 2 will add
embedding similarity once the iOS side starts uploading reference vectors.
"""
from __future__ import annotations

from typing import Any, Optional

from ..models import Layout, PersonPose, PoseSuggestion


def fallback_pose(person_count: int) -> PoseSuggestion:
    if person_count <= 1:
        return PoseSuggestion(
            person_count=1,
            layout=Layout.single,
            persons=[
                PersonPose(
                    role="person_a",
                    stance="放松站立，重心放在后脚，前脚自然外开 30 度",
                    upper_body="略微转体 15 度避免完全正面",
                    hands="一只手插裤兜，另一只手自然下垂",
                    gaze="目视镜头外侧，模拟自然抓拍",
                    expression="轻松微笑",
                    position_hint="左三分线位置",
                )
            ],
            interaction=None,
            reference_thumbnail_id="pose_single_relaxed_001",
            difficulty="easy",
        )
    if person_count == 2:
        return PoseSuggestion(
            person_count=2,
            layout=Layout.high_low_offset,
            persons=[
                PersonPose(
                    role="person_a",
                    stance="站立略侧身，左脚向前迈半步",
                    upper_body="微前倾",
                    hands="一手自然下垂，一手轻搭对方肩",
                    gaze="看向 person_b",
                    expression="微笑",
                    position_hint="左三分线",
                ),
                PersonPose(
                    role="person_b",
                    stance="半蹲或坐下，重心稳定",
                    upper_body="侧身向 person_a",
                    hands="双手交握于膝前",
                    gaze="回望 person_a",
                    expression="轻笑",
                    position_hint="紧邻 person_a 右下方 0.5m",
                ),
            ],
            interaction="高低错位互动注视",
            reference_thumbnail_id="pose_two_high_low_001",
            difficulty="easy",
        )
    if person_count == 3:
        return PoseSuggestion(
            person_count=3,
            layout=Layout.triangle,
            persons=[
                PersonPose(
                    role="person_a",
                    stance="居中略前，正面",
                    upper_body="自然挺立",
                    hands="双手自然下垂或环抱身侧人物",
                    gaze="看向镜头",
                    expression="自然笑",
                    position_hint="画面中线偏前 0.3m",
                ),
                PersonPose(
                    role="person_b",
                    stance="左侧略后，侧身向 person_a",
                    upper_body="放松",
                    hands="一手搭 person_a 肩",
                    gaze="看向 person_a",
                    expression="自然笑",
                    position_hint="左侧后排",
                ),
                PersonPose(
                    role="person_c",
                    stance="右侧略后，对称呼应 person_b",
                    upper_body="放松",
                    hands="一手搭 person_a 背后",
                    gaze="看向镜头",
                    expression="自然笑",
                    position_hint="右侧后排",
                ),
            ],
            interaction="三角形构图，主体居前形成视觉锚点",
            reference_thumbnail_id="pose_three_triangle_001",
            difficulty="medium",
        )
    return PoseSuggestion(
        person_count=4,
        layout=Layout.cluster,
        persons=[
            PersonPose(
                role="person_a",
                stance="居中略前，正面站立",
                upper_body="自然挺立",
                hands="一手搭在身侧 person_b 肩，一手轻扶 person_c",
                gaze="看向镜头",
                expression="自然笑",
                position_hint="画面中线偏左 0.2m",
            ),
            PersonPose(
                role="person_b",
                stance="左侧略后并身朝向 person_a",
                upper_body="放松，肩膀朝向中心",
                hands="搭在 person_a 背后",
                gaze="斜视镜头",
                expression="自然笑",
                position_hint="左后排，与 person_a 错半步",
            ),
            PersonPose(
                role="person_c",
                stance="右侧略前并半蹲",
                upper_body="侧身向中心",
                hands="双手交叠放在大腿前",
                gaze="看向镜头",
                expression="轻笑",
                position_hint="右前排，与 person_a 形成 V 角",
            ),
            PersonPose(
                role="person_d",
                stance="最右后方站立",
                upper_body="放松，肩膀朝向中心",
                hands="一手搭 person_c 肩，一手自然下垂",
                gaze="看向镜头",
                expression="自然笑",
                position_hint="最右后排",
            ),
        ],
        interaction="簇拥布局，主体居中两侧前后错落，避免一字排开",
        reference_thumbnail_id="pose_four_diamond_001",
        difficulty="medium",
    )


def map_to_library(
    pose: PoseSuggestion,
    library: list[dict[str, Any]],
) -> PoseSuggestion:
    """If the LLM gave a reference_thumbnail_id, verify it exists in the
    library; otherwise try to find one with matching layout + person_count."""
    if pose.reference_thumbnail_id and any(
        p.get("id") == pose.reference_thumbnail_id for p in library
    ):
        return pose

    candidate = _best_match(library, pose.layout.value, pose.person_count)
    if candidate is not None:
        pose.reference_thumbnail_id = candidate
    return pose


def _best_match(
    library: list[dict[str, Any]], layout: str, person_count: int
) -> Optional[str]:
    same_count = [p for p in library if p.get("person_count") == person_count]
    if not same_count:
        return None
    exact = [p for p in same_count if p.get("layout") == layout]
    pool = exact or same_count
    return pool[0].get("id")
