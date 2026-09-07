"""工作流：Embedding 飞轮地基测试（mock 向量，不调真实 API）"""
import asyncio
import io
import json
import os
import sys

os.environ.setdefault("ARK_API_KEY", "test-dummy-key")
os.environ.setdefault("BJMOMA_API_KEY", "test-dummy-key")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from app import db, embeddings  # noqa: E402
from app.main import app  # noqa: E402

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")


def _fake_vec(text: str) -> list[float]:
    """确定性假向量：26 维字母袋，够测相似度方向。"""
    v = [0.0] * 26
    for ch in text.lower():
        if "a" <= ch <= "z":
            v[ord(ch) - 97] += 1.0
    return v or [0.0] * 26


async def _fake_embed(texts):
    return [_fake_vec(t) for t in texts]


embeddings.embed_texts = _fake_embed  # monkeypatch：全程离线
db.init_db()  # 确保 embeddings 表存在（测试在 TestClient 启动前直接写库）


def run(coro):
    return asyncio.run(coro)


# ── 1. 配置与降级 ──
check("embedding 路由已配置", embeddings.available())

# ── 2. upsert + 相似检索 ──
async def _seed():
    with db._conn() as c:  # 幂等：清掉上次跑的残留（固定样例 + 历史投稿用例）
        c.execute("DELETE FROM embeddings WHERE ref_id IN "
                  "(SELECT id FROM assets WHERE id LIKE 'asset_t0000000%' "
                  "OR meta LIKE '%refraction lab recording%')")
        c.execute("DELETE FROM assets WHERE id LIKE 'asset_t0000000%' "
                  "OR meta LIKE '%refraction lab recording%'")
    for aid, folder, note in [
            ("asset_t00000000001", "光学类/光的折射", "refraction bending light"),
            ("asset_t00000000002", "光学类/波的干涉", "interference wave fringe"),
            ("asset_t00000000003", "基础医学教学类/解剖", "anatomy heart muscle")]:
        db.insert("assets", {"id": aid, "project_id": "", "node_id": "",
                             "kind": "image", "filename": aid + ".png",
                             "meta": json.dumps({"note": note}, ensure_ascii=False),
                             "created_at": db.now(), "folder": folder,
                             "rights": "licensed", "subject_id": "", "library": 1})
        row = db.get("assets", aid)
        await embeddings.upsert("asset", aid, embeddings.asset_text(row))


run(_seed())
hits = run(embeddings.search_text("refraction light bending", kind="asset"))
check("检索返回结果", len(hits) >= 1)
check("最相似的是折射素材", hits and hits[0]["ref_id"] == "asset_t00000000001")
check("相似度有分值", hits and 0 < hits[0]["score"] <= 1)

# ── 3. 建议目录投票 ──
sug = run(embeddings.suggest_folder("wave interference experiment"))
check("建议目录命中干涉", sug["folder"] == "光学类/波的干涉")
sug_none = run(embeddings.suggest_folder("zzz qq q"))
check("无相似时返回空建议", sug_none["folder"] == "")

# ── 4. HTTP 端点 ──
with TestClient(app) as client:
    r = client.post("/api/assets/suggest_folder", json={"text": "refraction light"})
    check("suggest_folder 端点 200", r.status_code == 200)
    check("端点建议折射目录", r.json().get("folder") == "光学类/光的折射")

    # 投稿即向量化
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    r = client.post("/api/assets/upload",
                    files={"file": ("refraction_lab.png", io.BytesIO(png), "image/png")},
                    data={"folder": "光学类/光的折射", "rights": "own",
                          "note": "refraction lab recording"})
    check("投稿成功", r.status_code == 200)
    aid = r.json().get("id", "")
    emb = db.query("embeddings", "kind='asset' AND ref_id=?", (aid,))
    check("投稿自动向量化入库", len(emb) == 1 and "refraction" in emb[0]["text"])

    r = client.post("/api/assets/reindex_embeddings")
    check("全量回补端点可用", r.status_code == 200 and r.json()["fail"] == 0)

print(f"\n通过 {PASS}/{PASS + FAIL}")
sys.exit(1 if FAIL else 0)
