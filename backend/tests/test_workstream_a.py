# -*- coding: utf-8 -*-
"""工作流 A 冒烟测试：资产库后端（A1 迁移 + A1b 素材库 API + 版本切换）。零外部 API。
用法：cd backend && python tests/test_workstream_a.py
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

from app import db  # noqa: E402
from app.main import _migrate, app  # noqa: E402

db.init_db()
_migrate()  # A1：补列迁移（幂等）
client = TestClient(app)

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f": {detail}" if detail and not ok else ""))


def mk_asset(pid: str, nid: str, kind: str = "image", folder: str = "",
             library: int = 0, rights: str = "") -> str:
    aid = db.new_id("asset")
    db.insert("assets", {
        "id": aid, "project_id": pid, "node_id": nid, "kind": kind,
        "filename": f"{aid}.png", "meta": "{}", "created_at": db.now(),
        "folder": folder, "rights": rights, "library": library})
    return aid


# ── 数据准备 ────────────────────────────────────────────────
pid = db.new_id("proj")
db.insert("projects", {"id": pid, "title": "A组测试", "status": "draft",
                       "created_at": db.now(), "updated_at": db.now()})
nid = db.new_id("node")
db.insert("canvas_nodes", {
    "id": nid, "project_id": pid, "type": "image", "title": "",
    "position_x": 0, "position_y": 0, "inputs": "{}", "outputs": "{}",
    "status": "succeeded", "created_at": db.now(), "updated_at": db.now()})


def cleanup() -> None:
    import time as _t
    for attempt in range(5):  # WAL 单写者：TestClient 连接可能短暂持锁，重试等待
        try:
            with db._conn() as c:
                for tbl in ("canvas_nodes", "canvas_edges", "model_tasks", "assets"):
                    c.execute(f"DELETE FROM {tbl} WHERE project_id=?", (pid,))
                c.execute("DELETE FROM projects WHERE id=?", (pid,))
            return
        except Exception:
            if attempt == 4:
                raise
            _t.sleep(0.5)


try:
    # ── A1：迁移列可用 ──
    a1 = mk_asset(pid, nid, folder="光学/迈克尔逊干涉", library=1, rights="own")
    row = db.get("assets", a1)
    check("A1 迁移列读写", row["folder"] == "光学/迈克尔逊干涉"
          and row["library"] == 1 and row["rights"] == "own")

    # ── A1b：入库/移文件夹/权利声明 PATCH ──
    a2 = mk_asset(pid, nid, kind="video")
    r = client.patch(f"/api/assets/{a2}", json={
        "folder": "光学/迈克尔逊干涉", "rights": "licensed", "library": True})
    check("A1b PATCH 入库+文件夹+权利", r.status_code == 200
          and r.json()["library"] is True
          and r.json()["folder"] == "光学/迈克尔逊干涉"
          and r.json()["rights"] == "licensed", r.text[:100])

    r = client.patch(f"/api/assets/{a2}", json={"rights": "stolen"})
    check("A1b 非法 rights 拒收", r.status_code == 400)

    # ── A1b：文件夹树 ──
    r = client.get("/api/assets/folders")
    check("A1b 文件夹树", r.status_code == 200 and "光学/迈克尔逊干涉" in r.json())

    # ── A1b：素材库过滤与搜索 ──
    r = client.get("/api/assets/library?folder=光学/迈克尔逊干涉&kind=video")
    got = [x["id"] for x in r.json()]
    check("A1b 库过滤(folder+kind)", got == [a2], str(got))
    r = client.get(f"/api/assets/library?q={a1[-6:]}")
    check("A1b 库搜索(filename)", any(x["id"] == a1 for x in r.json()))
    r = client.get("/api/assets/library?folder=不存在的文件夹")
    check("A1b 空文件夹返回空", r.json() == [])

    # ── A1b：未入库素材不进素材库 ──
    a3 = mk_asset(pid, nid)
    r = client.get("/api/assets/library")
    check("A1b 落盘≠入库", all(x["id"] != a3 for x in r.json()))

    # ── A4 后端：历史版本列表与切换 ──
    r = client.get(f"/api/nodes/{nid}/versions")
    ids = [x["id"] for x in r.json()]
    check("A4 版本列表(同节点素材新→旧)", ids == [a3, a2, a1], str(ids))
    r = client.post(f"/api/nodes/{nid}/use_version/{a1}")
    out = json.loads(db.get("canvas_nodes", nid)["outputs"])
    check("A4 版本切换", r.status_code == 200
          and out.get("asset_url") == f"/assets/{a1}.png"
          and out.get("asset_id") == a1, r.text[:100])
    r = client.post(f"/api/nodes/{nid}/use_version/asset_不存在")
    check("A4 切换不存在版本404", r.status_code == 404)

    # ── 教师投稿（2026-09-07）：预建目录 + 直接入库上传 ──
    r = client.get("/api/assets/folders")
    check("投稿 预建目录存在", "光学类" in r.json() and "基础医学教学类" in r.json(),
          str(r.json()))

    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    r = client.post("/api/assets/upload",
                    files={"file": ("光的折射.png", png, "image/png")},
                    data={"folder": "光学类/测试课题", "rights": "own", "note": "测试投稿"})
    up_id = ""
    try:
        j = r.json()
        up_id = j.get("id", "")
        check("投稿 图片直接入库", r.status_code == 200 and j.get("library") is True
              and j.get("folder") == "光学类/测试课题" and j.get("kind") == "image"
              and j.get("rights") == "own", r.text[:120])
        r2 = client.get("/api/assets/library?folder=光学类/测试课题")
        check("投稿 入库后可在素材库检索", any(x["id"] == up_id for x in r2.json()))
        r3 = client.post("/api/assets/upload",
                         files={"file": ("x.exe", b"MZ", "application/octet-stream")})
        check("投稿 非法类型拒收", r3.status_code == 400)
        r4 = client.post("/api/assets/upload",
                         files={"file": ("教案.pdf", b"%PDF-1.4 fake", "application/pdf")},
                         data={"folder": "基础医学教学类", "rights": "own"})
        check("投稿 pdf教案收为doc", r4.status_code == 200 and r4.json().get("kind") == "doc")
        # 清理 pdf 测试件
        from app.config import ASSETS_DIR as _AD
        (_AD / r4.json()["filename"]).unlink(missing_ok=True)
        with db._conn() as c:
            c.execute("DELETE FROM assets WHERE id=?", (r4.json()["id"],))
    finally:
        if up_id:
            from app.config import ASSETS_DIR as _AD
            (_AD / f"{up_id}.png").unlink(missing_ok=True)
            with db._conn() as c:
                c.execute("DELETE FROM assets WHERE id=?", (up_id,))
                c.execute("DELETE FROM library_folders WHERE path=?", ("光学类/测试课题",))
finally:
    cleanup()

print(f"\n通过 {len(PASS)}/{len(PASS) + len(FAIL)}")
sys.exit(1 if FAIL else 0)
