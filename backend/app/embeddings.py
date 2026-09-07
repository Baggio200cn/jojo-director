"""文本 Embedding：自我学习飞轮的地基。

用途：
- 素材/断言/改判记录向量化，入库 embeddings 表
- 余弦相似检索：投稿自动归目录、QC 样例按相似度选取、素材去重

模型走 providers.yaml 的 embedding 路由（火山方舟 doubao-embedding）。
任何一步不可用（未配置/未开通/网络失败）都静默降级返回空，绝不影响主流程。
"""
import json
import math

import httpx

from . import config, db

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def _conf() -> dict | None:
    r = config.ROUTES.get("embedding")
    if not r:
        return None
    try:
        p = config.provider_conf(r["provider"])
    except RuntimeError:
        return None
    return {"base": p["base_url"], "model": r["model"],
            "endpoint": r.get("endpoint", "embeddings"),
            "multimodal": bool(r.get("multimodal")),
            "headers": {"Authorization": f"Bearer {p['api_key']}",
                        "Content-Type": "application/json"}}


def available() -> bool:
    return _conf() is not None


async def embed_texts(texts: list[str]) -> list[list[float]] | None:
    """批量向量化。失败返回 None（降级）。
    multimodal 路由（doubao-embedding-vision）走 /embeddings/multimodal，
    每次调用一个 {type:text} 输入，返回 data.embedding 单向量。"""
    conf = _conf()
    if not conf or not texts:
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            if conf["multimodal"]:
                out = []
                for t in texts:
                    r = await client.post(
                        f"{conf['base']}/{conf['endpoint']}",
                        headers=conf["headers"],
                        json={"model": conf["model"],
                              "input": [{"type": "text", "text": t}]})
                    if r.status_code != 200:
                        return None
                    out.append(r.json()["data"]["embedding"])
                return out
            r = await client.post(f"{conf['base']}/{conf['endpoint']}",
                                  headers=conf["headers"],
                                  json={"model": conf["model"], "input": texts})
            if r.status_code != 200:
                return None
            data = r.json()
        items = sorted(data["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in items]
    except Exception:
        return None


async def upsert(kind: str, ref_id: str, text: str) -> bool:
    """向量化并写入/覆盖 embeddings 表。kind: asset / assertion / correction。"""
    text = (text or "").strip()
    if not text:
        return False
    vecs = await embed_texts([text])
    if not vecs:
        return False
    with db._conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO embeddings (kind, ref_id, text, vector, dims, model, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (kind, ref_id, text, json.dumps(vecs[0]), len(vecs[0]),
             config.ROUTES["embedding"]["model"], db.now()))
    return True


def _cosine(a: list[float], b: list[float]) -> float:
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    return dot / (math.sqrt(na) * math.sqrt(nb)) if na and nb else 0.0


async def search_text(text: str, kind: str | None = None, top_k: int = 5,
                      min_score: float = 0.0) -> list[dict]:
    """以文搜库：返回 [{ref_id, kind, text, score}]，按相似度降序。"""
    vecs = await embed_texts([text])
    if not vecs:
        return []
    q = vecs[0]
    rows = db.query("embeddings", "kind=?" if kind else "1=1",
                    (kind,) if kind else ())
    out = []
    for r in rows:
        try:
            score = _cosine(q, json.loads(r["vector"]))
        except Exception:
            continue
        if score >= min_score:
            out.append({"ref_id": r["ref_id"], "kind": r["kind"],
                        "text": r["text"], "score": round(score, 4)})
    out.sort(key=lambda x: -x["score"])
    return out[:top_k]


def asset_text(row: dict) -> str:
    """素材的向量化文本：目录 + 描述 + 原文件名（权利元数据不参与语义）。"""
    meta = db.jloads(row.get("meta"))
    parts = [row.get("folder") or "", meta.get("note") or "",
             meta.get("orig_name") or row.get("filename") or ""]
    return " ".join(p for p in parts if p).strip()


async def suggest_folder(text: str, top_k: int = 5) -> dict:
    """投稿自动归目录：按相似素材的目录投票。
    返回 {folder, score, matches}；无结果/服务不可用 → folder=''。"""
    hits = await search_text(text, kind="asset", top_k=top_k, min_score=0.45)
    if not hits:
        return {"folder": "", "score": 0, "matches": []}
    # 目录投票：按 score 加权
    votes: dict[str, float] = {}
    asset_ids = [h["ref_id"] for h in hits]
    for h, aid in zip(hits, asset_ids):
        a = db.get("assets", aid)
        if a and a.get("folder"):
            votes[a["folder"]] = votes.get(a["folder"], 0) + h["score"]
    if not votes:
        return {"folder": "", "score": 0, "matches": hits}
    best = max(votes.items(), key=lambda kv: kv[1])
    return {"folder": best[0], "score": round(best[1], 4), "matches": hits}
