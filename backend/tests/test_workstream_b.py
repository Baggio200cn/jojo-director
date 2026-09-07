# -*- coding: utf-8 -*-
"""工作流 B 冒烟测试：光学样板（结构化动作序列 + 课题层规则包 + 断言样例库）。零外部 API。
用法：cd backend && python tests/test_workstream_b.py
"""
import io
import json
import os
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("ARK_API_KEY", "test-dummy-key")
os.environ.setdefault("BJMOMA_API_KEY", "test-dummy-key")

from fastapi.testclient import TestClient  # noqa: E402

from app import db, executors  # noqa: E402
from app.main import app  # noqa: E402

db.init_db()
client = TestClient(app)

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f": {detail}" if detail and not ok else ""))


def mk_ref_node(pid: str, card: dict) -> str:
    nid = db.new_id("node")
    segs = [{"index": 1, "start": 0.0, "end": 5.0, "seconds": 5.0,
             "clip_url": "", "keyframes": ["/assets/k1.jpg", "/assets/k2.jpg"],
             "card": card}]
    db.insert("canvas_nodes", {
        "id": nid, "project_id": pid, "type": "ref_video", "title": "参考",
        "position_x": 0, "position_y": 0, "inputs": json.dumps({"domain": "optics"}),
        "outputs": json.dumps({"segments": segs}, ensure_ascii=False),
        "status": "succeeded", "created_at": db.now(), "updated_at": db.now()})
    return nid


def mk_project(tag: str) -> str:
    pid = db.new_id("proj")
    db.insert("projects", {"id": pid, "title": f"B组测试-{tag}", "status": "draft",
                           "created_at": db.now(), "updated_at": db.now()})
    return pid


def cleanup(pid: str) -> None:
    import time as _t
    for _ in range(5):
        try:
            with db._conn() as c:
                for tbl in ("canvas_nodes", "canvas_edges", "model_tasks", "assets"):
                    c.execute(f"DELETE FROM {tbl} WHERE project_id=?", (pid,))
                c.execute("DELETE FROM projects WHERE id=?", (pid,))
            return
        except Exception:
            _t.sleep(0.5)


# ── B1：结构化动作序列 ─────────────────────────────────────
pid1 = mk_project("动作序列")
try:
    card_new = {
        "scene": "调节可移动镜", "subjects": ["干涉仪", "旋钮"],
        "actions": [
            {"t": 0.5, "action": "手旋转微调旋钮", "objects": ["可移动镜", "旋钮"]},
            {"t": 2.0, "action": "条纹从中心涌出", "objects": ["干涉条纹"]},
            {"t": 4.0, "action": "条纹趋于稳定", "objects": ["干涉条纹"]},
        ],
        "action_timeline": "调旋钮→条纹涌出→稳定",
        "camera": "固定机位", "science_facts": ["条纹为同心圆环"],
        "first_frame_desc": "...", "last_frame_desc": "...", "has_faces": False,
    }
    nid = mk_ref_node(pid1, card_new)
    r = client.post(f"/api/nodes/{nid}/storyboard_from_ref")
    check("B1 结构化分镜生成", r.status_code == 200, r.text[:100])
    sb_id = r.json()["storyboard_node"]
    shots = db.jloads(db.get("canvas_nodes", sb_id)["outputs"])["storyboard"]["shots"]
    sh = shots[0]
    check("B1 action_seq 写入分镜", len(sh.get("action_seq") or []) == 3)
    check("B1 motion 来自结构化序列", "2.0s 条纹从中心涌出" in sh["motion"]
          and "干涉条纹" in sh["motion"], sh["motion"][:80])
    seq_asserts = [a for a in sh["assertions"] if a.get("phase") == "video"]
    check("B1 时序断言生成", len(seq_asserts) == 1
          and "次序不得颠倒" in seq_asserts[0]["text"]
          and "手旋转微调旋钮" in seq_asserts[0]["text"], str(seq_asserts)[:100])

    # 旧卡兼容：无 actions 字段回退 action_timeline
    card_old = {"scene": "旧卡", "action_timeline": "自由文本动作描述",
                "science_facts": [], "has_faces": False}
    nid2 = mk_ref_node(pid1, card_old)
    r2 = client.post(f"/api/nodes/{nid2}/storyboard_from_ref")
    sh2 = db.jloads(db.get("canvas_nodes", r2.json()["storyboard_node"])["outputs"])["storyboard"]["shots"][0]
    check("B1 旧卡回退兼容", "自由文本动作描述" in sh2["motion"]
          and sh2.get("action_seq") == [], sh2["motion"][:60])
finally:
    cleanup(pid1)

# ── B2：课题层规则包 ──────────────────────────────────────
rules_opt = executors._load_rules("optics")
mic_rules = [r for r in rules_opt if str(r.get("id", "")).startswith("MIC-")]
check("B2 课题层并入optics", len(mic_rules) == 5, f"MIC规则数={len(mic_rules)}")

rules_mech = executors._load_rules("mechanics")
check("B2 课题层不串学科", not any(str(r.get("id", "")).startswith("MIC-")
                                   for r in rules_mech))

scene_hit = "迈克尔逊干涉仪的同心圆环条纹从中心涌出"
scene_miss = "一个苹果放在桌子上"
hit = [r for r in rules_opt if executors._rule_applies(r, scene_hit)]
miss = [r for r in rules_opt if executors._rule_applies(r, scene_miss)]
check("B2 applies_when命中装载", any(r["id"] == "MIC-01" for r in hit))
check("B2 不命中不装载(防稀释)", not any(str(r.get("id", "")).startswith("MIC-")
                                         for r in miss))
check("B2 通用层始终装载", any(str(r.get("id", "")).startswith("GEN-")
                               for r in miss))

# ── B3：断言样例库 ────────────────────────────────────────
samples = executors._load_assertion_samples("optics")
pos = [s for s in samples if s.get("kind") == "positive"]
neg = [s for s in samples if s.get("kind") == "negative"]
check("B3 正例按学科过滤", len(pos) >= 5
      and all(s.get("domain") == "optics" or s.get("topic_of") == "optics" for s in pos)
      and any("迈克尔逊" in s["text"] or "条纹" in s["text"] or "公式" in s["text"]
              for s in pos))
check("B3 反例含误判沉淀", len(neg) >= 2
      and any("疏密" in s["text"] for s in neg)
      and any("状态误读" in s["text"] for s in neg))
check("B3 课题pitfalls并入", any("条纹疏密" in s.get("text", "") or "MIC-P" in s.get("id", "")
                                 for s in samples))
check("B3 总量封顶", len(samples) <= 8)
samples_mech = executors._load_assertion_samples("mechanics")
check("B3 其他学科只见general反例", all(s.get("domain") == "general" or s.get("kind") == "negative"
                                        for s in samples_mech)
      and not any("迈克尔逊" in s.get("text", "") for s in samples_mech))

print(f"\n通过 {len(PASS)}/{len(PASS) + len(FAIL)}")
sys.exit(1 if FAIL else 0)
