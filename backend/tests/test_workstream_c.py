# -*- coding: utf-8 -*-
"""工作流 C 冒烟测试：不调用任何外部 API，零成本。
用法：cd backend && python tests/test_workstream_c.py

覆盖：
  C1  compose 排序契约 clip_order_key（shot_index → 上游帧 shot_index → 标题镜头N → 画布行序）
  C2  执行器注册表（10 类型齐全 + sim_import 占位报错 + 未知类型报错）
  C3  lifespan 启动（import 期无 DB 副作用；TestClient 启动后 health 通过 + 卡死节点复位）
"""
import asyncio
import io
import json
import os
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 冒烟测试不调外部 API：用占位 Key 让适配器通过初始化（真实 Key 在部署机 .env，不入库）
os.environ.setdefault("ARK_API_KEY", "test-dummy-key")
os.environ.setdefault("BJMOMA_API_KEY", "test-dummy-key")

from app import db, executors  # noqa: E402

db.init_db()  # 测试用库为克隆仓库内的全新空库（生产库在部署机，不受影响）

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f": {detail}" if detail and not ok else ""))


def mk_project(tag: str) -> str:
    pid = db.new_id("proj")
    db.insert("projects", {"id": pid, "title": f"C组测试-{tag}", "status": "draft",
                           "created_at": db.now(), "updated_at": db.now()})
    return pid


def mk_node(pid: str, type_: str, title: str = "", inputs: dict | None = None,
            x: float = 0, y: float = 0, status: str = "idle") -> str:
    nid = db.new_id("node")
    db.insert("canvas_nodes", {
        "id": nid, "project_id": pid, "type": type_, "title": title,
        "position_x": x, "position_y": y,
        "inputs": json.dumps(inputs or {}, ensure_ascii=False),
        "outputs": "{}", "status": status,
        "created_at": db.now(), "updated_at": db.now()})
    return nid


def mk_edge(pid: str, src: str, tgt: str) -> None:
    db.insert("canvas_edges", {"id": db.new_id("edge"), "project_id": pid,
                               "source_node_id": src, "target_node_id": tgt,
                               "source_handle": "output", "target_handle": "input"})


def cleanup(pid: str) -> None:
    with db._conn() as c:
        for tbl in ("canvas_nodes", "canvas_edges", "model_tasks", "assets"):
            c.execute(f"DELETE FROM {tbl} WHERE project_id=?", (pid,))
        c.execute("DELETE FROM projects WHERE id=?", (pid,))


# ── C1：clip_order_key 四级契约 ─────────────────────────────
def test_clip_order() -> None:
    pid = mk_project("排序")
    try:
        # ① 自身带 shot_index（最高优先，哪怕标题写着镜头9）
        n1 = mk_node(pid, "video", "镜头9", {"shot_index": 1}, x=999, y=999)
        # ③ 只有标题"镜头N"
        n3 = mk_node(pid, "video", "镜头2", {}, x=10, y=0)
        # ② 自身不带，上游图像节点带 shot_index=3
        img3 = mk_node(pid, "image", "", {"shot_index": 3})
        n2 = mk_node(pid, "video", "", {}, x=5, y=0)
        mk_edge(pid, img3, n2)
        # ④ 全裸节点按画布行序(y,x)
        n4 = mk_node(pid, "video", "", {}, x=0, y=7)
        ordered = sorted([n4, n3, n2, n1],
                         key=lambda i: executors.clip_order_key(db.get("canvas_nodes", i)))
        titles = [db.get("canvas_nodes", i)["id"] for i in ordered]
        check("C1 四级排序契约", titles == [n1, n3, n2, n4],
              f"实际顺序={titles}，期望={[n1, n3, n2, n4]}")
    finally:
        cleanup(pid)


# ── C2：执行器注册表 ────────────────────────────────────────
def test_registry() -> None:
    expect = {"script", "storyboard", "image", "video", "code_render",
              "compose", "qc", "ref_video", "enhance", "tts", "sim_import"}
    check("C2 注册表类型齐全", expect <= set(executors.EXECUTORS),
          f"缺 {expect - set(executors.EXECUTORS)}")

    pid = mk_project("注册表")
    try:
        # sim_import 占位：执行应失败并带"占位"说明，状态回 failed
        nid = mk_node(pid, "sim_import")
        try:
            asyncio.run(executors.execute_node(nid))
            check("C2 sim_import 占位报错", False, "未抛异常")
        except Exception as e:
            out = db.jloads(db.get("canvas_nodes", nid)["outputs"])
            check("C2 sim_import 占位报错", "占位" in str(e) and "ref_video" in str(e)
                  and db.get("canvas_nodes", nid)["status"] == "failed"
                  and "error" in out, str(e)[:80])
        # 未知类型：明确报错而非静默
        nid2 = mk_node(pid, "not_a_type")
        try:
            asyncio.run(executors.execute_node(nid2))
            check("C2 未知类型报错", False, "未抛异常")
        except Exception as e:
            check("C2 未知类型报错", "暂不支持" in str(e), str(e)[:80])
    finally:
        cleanup(pid)


# ── C3：lifespan 启动 ───────────────────────────────────────
def test_lifespan() -> None:
    from fastapi.testclient import TestClient
    from app.main import app
    # 埋一个卡死节点，验证 lifespan 启动自愈把它复位
    pid = mk_project("lifespan")
    nid = mk_node(pid, "script", status="running")
    try:
        with TestClient(app) as client:
            r = client.get("/api/health")
            check("C3 lifespan 启动 + health", r.status_code == 200 and r.json().get("ok"))
        n = db.get("canvas_nodes", nid)
        out = db.jloads(n["outputs"])
        check("C3 卡死节点启动复位", n["status"] == "failed" and "中断" in out.get("error", ""))
    finally:
        cleanup(pid)


if __name__ == "__main__":
    print("═══ 工作流 C 冒烟测试 ═══")
    test_clip_order()
    test_registry()
    test_lifespan()
    print(f"\n通过 {len(PASS)}/{len(PASS) + len(FAIL)}")
    sys.exit(1 if FAIL else 0)
