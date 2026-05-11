// v7 demo fixture — used by ?demo=v7 on result.html so reviewers can
// land on the 3D shot preview without walking the full wizard.
//
// Mirrors the fakeResponse used by scripts/snap_wizard_v7_only.mjs so
// the visual matches the shipped screenshot in docs/preview/.

export const DEMO_RESPONSE_V7 = {
  scene: {
    type: "outdoor_park",
    lighting: "golden_hour",
    background_summary: "西侧低角度阳光透过桦林，背景有色彩对比但不杂乱。",
    cautions: [],
  },
  shots: [
    {
      id: "shot_1",
      title: "主机位",
      angle: { azimuth_deg: 245, pitch_deg: -3, distance_m: 2.2 },
      composition: {
        primary: "rule_of_thirds",
        secondary: ["leading_lines"],
        notes: "三分线下交点放眼睛，让左前方光线落到面部",
      },
      camera: {
        focal_length_mm: 50,
        aperture: "f/2.0",
        shutter: "1/320",
        iso: 200,
        white_balance_k: 5500,
        ev_compensation: -0.3,
        rationale: "黄金光下 50/2 焦虚化，背景压成柔和暖斑",
        device_hints: null,
      },
      poses: [
        {
          id: "pose_single_relaxed_001",
          layout: "single",
          persons: [
            {
              role: "subject",
              description: "自然站姿，目光略偏左前方，肩部放松",
            },
          ],
        },
      ],
      rationale: "暖光从左前方铺开，主体放在三分线下交点，背景压暗。",
      coach_brief: "肩部放松，下巴略收，眼睛看左前方光源",
      confidence: 0.84,
      overall_score: 4.32,
      criteria_score: {
        composition: 5,
        light: 5,
        color: 4,
        depth: 4,
        subject_fit: 5,
        background: 4,
        theme: 5,
      },
      criteria_notes: {
        composition: "[comp_rule_of_thirds] 主体落在三分交点",
        light: "[light_golden_hour] 黄金时段侧前光，肤色暖且立体",
        theme: "[theme_solitude_vs_group] 单人留白突出主题",
      },
      strongest_axis: "theme",
      weakest_axis: "depth",
    },
    {
      id: "shot_2",
      title: "侧逆光剪影",
      angle: { azimuth_deg: 200, pitch_deg: 0, distance_m: 4.0 },
      composition: { primary: "centered", secondary: [], notes: "" },
      camera: {
        focal_length_mm: 85,
        aperture: "f/4.0",
        shutter: "1/500",
        iso: 100,
        white_balance_k: 4500,
        ev_compensation: -1.0,
        rationale: "压暗背景突出剪影，长焦压缩空间",
      },
      poses: [],
      rationale: "黄昏剪影构图，让人物在金边光中只留轮廓。",
      confidence: 0.72,
      overall_score: 3.78,
      criteria_score: {
        composition: 3,
        light: 5,
        color: 3,
        depth: 4,
        subject_fit: 4,
        background: 4,
        theme: 4,
      },
      criteria_notes: {
        light: "[light_backlight_rim] 逆光勾边",
        theme: "[theme_solitude_vs_group] 黄昏剪影传递孤独",
      },
      strongest_axis: "light",
      weakest_axis: "composition",
    },
    {
      id: "shot_3",
      title: "环境对话",
      angle: { azimuth_deg: 110, pitch_deg: 5, distance_m: 5.5 },
      composition: { primary: "leading_lines", secondary: [], notes: "" },
      camera: {
        focal_length_mm: 35,
        aperture: "f/5.6",
        shutter: "1/250",
        iso: 200,
        white_balance_k: 5200,
        ev_compensation: 0.0,
        rationale: "广角带出环境，让人与场景对话",
      },
      poses: [],
      rationale: "用透视线引向主体，前中远三层关系清晰。",
      confidence: 0.68,
      overall_score: 3.5,
      criteria_score: {
        composition: 4,
        light: 4,
        color: 4,
        depth: 5,
        subject_fit: 3,
        background: 4,
        theme: 3,
      },
      criteria_notes: {
        depth: "[depth_three_layers_explicit] 前中远三层",
        composition: "[comp_leading_lines] 透视线引导",
      },
      strongest_axis: "depth",
      weakest_axis: "subject_fit",
    },
  ],
  style_inspiration: null,
  environment: {
    sun: { azimuth_deg: 245, altitude_deg: 18, time_of_day: "golden_hour" },
  },
  generated_at: new Date().toISOString(),
  model: "demo-v7",
};
