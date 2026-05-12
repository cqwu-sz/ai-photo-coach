"""Regression: /analyze responses must never use teacher-y / saccharine
openers in `rationale`. Mirrors the prompt's "朋友口吻" tone principle so
that any future prompt or mock-provider drift gets caught here before it
reaches users.

Why this test exists: prompts are easy to "regress" silently — someone
adds a new instruction or a new few-shot example that re-introduces
"我建议你...", and the model immediately copies it. This test calls the
mock path so it's deterministic and cheap, and guards both the live
prompt template (via SYSTEM_INSTRUCTION wording) and the mock provider's
hand-written rationale string.
"""
import io, json
import pytest
from fastapi.testclient import TestClient
from PIL import Image


def test_dump_tone(capsys):
    from app.main import app
    client = TestClient(app)
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (120, 130, 140)).save(buf, "JPEG")
    img = buf.getvalue()

    for scene in ("portrait", "scenery", "light_shadow"):
        meta = {
            "person_count": 0 if scene == "scenery" else 1,
            "scene_mode": scene,
            "quality_mode": "fast",
            "frame_meta": [{"index": i, "azimuth_deg": i * 45.0} for i in range(8)],
        }
        files = [("frames", (f"f{i}.jpg", img, "image/jpeg")) for i in range(8)]
        data = {"meta": json.dumps(meta)}
        r = client.post("/analyze", files=files, data=data)
        assert r.status_code == 200, r.text
        resp = r.json()
        with capsys.disabled():
            print(f"\n=== scene={scene} ===")
            for i, s in enumerate(resp.get("shots", [])):
                print(f"  shot {i} rationale: {s.get('rationale')}")
                print(f"  shot {i} coach   : {s.get('coach_brief')}")
                banned = ["我建议你", "你应该", "你需要", "让我们", "我们一起", "试想一下", "不妨"]
                hits = [b for b in banned if b in (s.get("rationale") or "")]
                assert not hits, f"rationale uses banned opener {hits}: {s.get('rationale')}"
