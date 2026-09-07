"""QC 改判沉淀测试：人工终裁与机器原判不一致时结构化落库并向量化"""
import asyncio
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


async def _fake_embed(texts):
    return [[float(len(t) % 7 + 1)] * 4 for t in texts]


embeddings.embed_texts = _fake_embed  # 离线 mock
db.init_db()

# 幂等清理
with db._conn() as c:
    c.execute("DELETE FROM canvas_nodes WHERE project_id='proj_corr_test'")
    c.execute("DELETE FROM projects WHERE id='proj_corr_test'")
    c.execute("DELETE FROM qc_corrections WHERE project_id='proj_corr_test'")

db.insert("projects", {"id": "proj_corr_test", "title": "改判测试",
                       "status": "draft", "created_at": db.now(), "updated_at": db.now()})


def mk_qc(nid, verdict, tgt):
    db.insert("canvas_nodes", {
        "id": tgt, "project_id": "proj_corr_test", "type": "video",
        "title": "被检", "inputs": "{}", "outputs": "{}",
        "created_at": db.now(), "updated_at": db.now()})
    db.insert("canvas_nodes", {
        "id": nid, "project_id": "proj_corr_test", "type": "qc", "title": "QC",
        "inputs": "{}",
        "outputs": json.dumps({"verdict": verdict, "target_node_id": tgt,
                               "summary": "条纹偏色"}, ensure_ascii=False),
        "created_at": db.now(), "updated_at": db.now()})


mk_qc("qc_t01", "reject", "tgt_t01")     # 机器判不合格 → 人放行 = 误判
mk_qc("qc_t02", "needs_human", "tgt_t02")  # 机器不确定 → 人放行 = 标准不清
mk_qc("qc_t03", "reject", "tgt_t03")     # 机器判不合格 → 人也判不合格 = 确认，非改判

with TestClient(app) as client:
    r = client.post("/api/nodes/qc_t01/qc_override",
                    json={"verdict": "pass_human", "fail_type": "误判",
                          "reason": "条纹偏色在教学上可接受"})
    check("改判端点 200", r.status_code == 200)
    check("节点 verdict 已改 pass", r.json()["outputs"]["verdict"] == "pass")

    rows = db.query("qc_corrections", "qc_node_id='qc_t01'")
    check("改判记录已落库", len(rows) == 1)
    check("原判/终裁/类型正确", rows and rows[0]["orig_verdict"] == "reject"
          and rows[0]["human_verdict"] == "pass_human" and rows[0]["fail_type"] == "误判")
    emb = db.query("embeddings", "kind='correction' AND ref_id=?",
                   (rows[0]["id"],)) if rows else []
    check("改判自动向量化", len(emb) == 1 and "误判" in emb[0]["text"])

    # 被检节点同步
    tgt = db.get("canvas_nodes", "tgt_t01")
    check("被检节点同步放行", db.jloads(tgt["outputs"])["qc"]["verdict"] == "pass")

    # needs_human → 默认类型"标准不清"
    client.post("/api/nodes/qc_t02/qc_override", json={"verdict": "pass_human"})
    rows2 = db.query("qc_corrections", "qc_node_id='qc_t02'")
    check("needs_human默认标准不清", len(rows2) == 1 and rows2[0]["fail_type"] == "标准不清")

    # 同向终裁（确认机器）不产改判记录
    client.post("/api/nodes/qc_t03/qc_override", json={"verdict": "reject_human"})
    rows3 = db.query("qc_corrections", "qc_node_id='qc_t03'")
    check("同向终裁不记改判", len(rows3) == 0)

# 归纳原料包含改判记录
from tools.induce_rules import _collect  # noqa: E402

pos, neg = _collect("optics")
check("归纳原料含改判记录", any("条纹偏色" in (n.get("text") or "") for n in neg))

print(f"\n通过 {PASS}/{PASS + FAIL}")
sys.exit(1 if FAIL else 0)
