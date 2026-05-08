"""One-shot script — enrich every pose KB JSON with Chinese search aliases.

Adds two fields when missing:
  * summary_zh : a short Chinese description matching the LLM's typical output
  * tags_zh   : 4-6 Chinese keywords the user / LLM might use

Run from backend/:
    python scripts/enrich_pose_kb_zh.py

Idempotent — re-runs are no-ops if the fields are already populated.
"""
from __future__ import annotations

import json
from pathlib import Path

# id -> (summary_zh, [tags_zh])
ALIASES: dict[str, tuple[str, list[str]]] = {
    # 1-person
    "pose_single_relaxed_001": (
        "放松站立 一手插袋 微微转体 街头随性",
        ["放松", "站立", "插袋", "转体", "街头", "随性", "casual"],
    ),
    "pose_single_hand_in_hair_001": (
        "一只手撩头发 头略侧 文艺感",
        ["撩头发", "头侧", "文艺", "氛围", "女性"],
    ),
    "pose_single_leaning_wall_001": (
        "斜靠在墙边 单脚抵墙 街头",
        ["斜靠", "墙边", "靠墙", "抵墙", "街头", "随性"],
    ),
    "pose_single_walking_001": (
        "自然走动 步幅自然 手臂前后摆动",
        ["走动", "走路", "步幅", "手臂", "动态", "街拍"],
    ),
    "pose_single_back_view_001": (
        "背对镜头 仰头看天空 远眺",
        ["背影", "背对", "仰头", "天空", "远眺", "意境"],
    ),
    "pose_single_seated_wall_001": (
        "坐在矮墙上 双手放膝盖 放松",
        ["坐", "矮墙", "膝盖", "放松", "坐姿"],
    ),
    "pose_single_jumping_001": (
        "跳跃中 张开双臂 表情兴奋",
        ["跳", "跳跃", "腾空", "张开双臂", "兴奋", "动感"],
    ),
    "pose_single_lying_grass_001": (
        "躺在草地 仰望天空 放松",
        ["躺", "草地", "仰望", "天空", "放松", "户外"],
    ),
    "pose_single_holding_object_001": (
        "手中拿着咖啡杯 视线看向远方 文艺",
        ["拿杯子", "咖啡杯", "拿物", "看远方", "视线", "文艺"],
    ),

    # 2-person
    "pose_two_high_low_001": (
        "两人高低错位 互相注视 一站一蹲 亲密互动",
        ["高低错位", "高低", "互相注视", "蹲", "亲密", "互动", "情侣"],
    ),
    "pose_two_forehead_touch_001": (
        "两人额头相贴 闭眼 温馨 情侣",
        ["额头", "贴额", "闭眼", "温馨", "情侣", "亲密"],
    ),
    "pose_two_side_by_side_001": (
        "两人并肩站立 朝镜头微笑",
        ["并肩", "并排", "微笑", "朋友", "side_by_side"],
    ),
    "pose_two_back_to_back_001": (
        "两人背靠背 双手交叉",
        ["背靠背", "背对背", "交叉", "酷"],
    ),
    "pose_two_walking_handhold_001": (
        "两人手拉手散步 街头 情侣",
        ["手拉手", "牵手", "散步", "走", "情侣", "街头"],
    ),
    "pose_two_running_001": (
        "两人奔跑 笑得开心 动感",
        ["奔跑", "跑", "动感", "笑", "开心"],
    ),
    "pose_two_dancing_001": (
        "两人共舞 旋转中 浪漫",
        ["共舞", "跳舞", "旋转", "浪漫", "情侣", "互动"],
    ),
    "pose_two_seated_steps_001": (
        "两人坐在台阶上 闲聊 朋友",
        ["坐", "台阶", "闲聊", "朋友", "坐姿", "街头"],
    ),
    "pose_two_holding_each_other_001": (
        "两人拥抱 紧紧相依 温馨",
        ["拥抱", "相依", "紧紧", "温馨", "亲密", "情侣"],
    ),
    "pose_two_kids_lift_001": (
        "孩子被举高 向上欢笑 亲子",
        ["举高", "举", "孩子", "亲子", "欢笑", "举起"],
    ),
    "pose_two_piggyback_001": (
        "背着对方 嬉戏奔跑",
        ["背", "背着", "嬉戏", "奔跑", "情侣", "好友", "piggyback"],
    ),

    # 3-person
    "pose_three_triangle_001": (
        "三角构图 主体居中 两侧对称 三人",
        ["三角", "三角形", "居中", "对称", "构图", "triangle"],
    ),
    "pose_three_circle_jumping_001": (
        "三人围圈 一人跳起 欢乐 动感",
        ["围圈", "圈", "跳起", "欢乐", "动感", "circle"],
    ),
    "pose_three_diagonal_001": (
        "三人对角分布 错落有致 时尚",
        ["对角", "对角线", "错落", "diagonal", "时尚"],
    ),
    "pose_three_walking_line_001": (
        "三人一字排开 走在路上 朋友",
        ["一字排开", "一字", "排开", "走", "朋友", "line", "排队"],
    ),
    "pose_three_huddle_001": (
        "三人围拢 紧密互动 闺蜜",
        ["围拢", "紧密", "互动", "闺蜜", "huddle"],
    ),
    "pose_three_family_seated_001": (
        "三人坐在沙发或长椅 家庭",
        ["坐", "沙发", "长椅", "家庭", "亲子"],
    ),

    # 4-person
    "pose_four_diamond_001": (
        "四人簇拥 主体居中 V 角排列 钻石形",
        ["簇拥", "居中", "v 角", "钻石", "四人", "diamond"],
    ),
    "pose_four_seated_couch_001": (
        "四人坐在沙发上 紧密 家庭",
        ["坐", "沙发", "家庭", "couch", "紧密"],
    ),
}


def main() -> int:
    kb_dir = Path(__file__).resolve().parents[1] / "app" / "knowledge" / "poses"
    files = sorted(kb_dir.glob("pose_*.json"))
    updated = 0
    skipped = 0
    missing: list[str] = []
    for f in files:
        with f.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
        pid = data.get("id") or f.stem
        if pid not in ALIASES:
            missing.append(pid)
            continue
        summary_zh, tags_zh = ALIASES[pid]
        changed = False
        if data.get("summary_zh") != summary_zh:
            data["summary_zh"] = summary_zh
            changed = True
        if data.get("tags_zh") != tags_zh:
            data["tags_zh"] = tags_zh
            changed = True
        if changed:
            with f.open("w", encoding="utf-8") as fp:
                json.dump(data, fp, ensure_ascii=False, indent=2)
                fp.write("\n")
            updated += 1
        else:
            skipped += 1
    print(f"updated={updated} skipped={skipped} missing_alias={len(missing)}")
    if missing:
        print("missing:", missing)
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
