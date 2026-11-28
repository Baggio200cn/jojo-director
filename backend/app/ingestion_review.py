"""文件夹批次的持久化状态、教师归档与管理员对齐。"""
import json

from . import db


def create_batch(batch_id: str, results: list[dict], folder: str, rights: str,
                 note: str, uploader_role: str) -> None:
    summary = {state: sum(1 for x in results if x["status"] == state)
               for state in ("accepted", "rejected", "duplicate")}
    now = db.now()
    db.insert("ingestion_batches", {
        "id": batch_id, "status": "analyzed_pending_teacher",
        "summary": json.dumps(summary, ensure_ascii=False),
        "results": json.dumps(results, ensure_ascii=False),
        "folder": folder, "rights": rights, "note": note,
        "uploader_role": uploader_role, "created_at": now, "updated_at": now,
    })


def backfill_legacy_batches() -> None:
    with db._conn() as conn:
        batch_ids = [r[0] for r in conn.execute(
            "SELECT DISTINCT json_extract(meta,'$.uploadBatchId') FROM assets "
            "WHERE json_extract(meta,'$.uploadBatchId') IS NOT NULL").fetchall()]
        for batch_id in batch_ids:
            exists = db.get("ingestion_batches", batch_id)
            if exists:
                continue
            rows = db.query("assets", "json_extract(meta,'$.uploadBatchId')=?", (batch_id,))
            results = [{"status": "accepted", "path": db.jloads(row["meta"]).get("relativePath", ""),
                        "folder": row["folder"], "kind": row["kind"],
                        "analysis": db.jloads(row["meta"]).get("analysis", {})} for row in rows]
            now = db.now()
            db.insert("ingestion_batches", {
                "id": batch_id, "status": "analyzed_pending_teacher",
                "summary": json.dumps({"accepted": len(rows), "rejected": None,
                                       "duplicate": None, "receiptIncomplete": True}, ensure_ascii=False),
                "results": json.dumps(results, ensure_ascii=False),
                "folder": rows[0]["folder"] if rows else "",
                "rights": rows[0]["rights"] if rows else "own",
                "note": "历史批次：仅保留通过校验的文件，拒绝与重复数量不可回推",
                "uploader_role": "unknown", "created_at": now, "updated_at": now,
            })


def _review_history(batch_id: str) -> list[dict]:
    return db.query("ingestion_reviews", "batch_id=? ORDER BY created_at, id", (batch_id,))


def batch_view(row: dict) -> dict:
    return {**row, "summary": db.jloads(row["summary"]), "results": db.jloads(row["results"]),
            "reviews": _review_history(row["id"])}


def list_batches() -> list[dict]:
    return [batch_view(row) for row in db.query(
        "ingestion_batches", "1=1 ORDER BY created_at DESC LIMIT 20")]


def move_batch(batch_id: str, folder: str) -> dict:
    folder = folder.strip().strip("/")
    parts = folder.split("/") if folder else []
    if not parts or any(not part or part in (".", "..") for part in parts) or folder in ("__auto__", "__custom__"):
        raise ValueError("请选择有效的已有素材目录")
    row = db.get("ingestion_batches", batch_id)
    if not row:
        raise ValueError("批次不存在")
    results = db.jloads(row["results"])
    if not any(item.get("status") == "accepted" for item in results):
        raise ValueError("本批没有可整理的接收文件")
    batch_assets = db.query("assets", "json_extract(meta,'$.uploadBatchId')=?", (batch_id,))
    by_path = {db.jloads(asset["meta"]).get("relativePath"): asset for asset in batch_assets}
    moved = 0
    for result in results:
        if result.get("status") != "accepted":
            continue
        asset_id = result.get("assetId")
        if not asset_id:
            asset = by_path.get(result.get("path"))
            asset_id = asset["id"] if asset else None
        if not asset_id or not db.get("assets", asset_id):
            raise ValueError(f"找不到待整理素材：{result.get('path')}")
        db.update("assets", asset_id, {"folder": folder})
        result["folder"] = folder
        moved += 1
    with db._conn() as conn:
        conn.execute("INSERT OR IGNORE INTO library_folders (path,created_at) VALUES (?,?)",
                     (folder, db.now()))
    db.update("ingestion_batches", batch_id, {
        "results": json.dumps(results, ensure_ascii=False), "folder": folder, "updated_at": db.now(),
    })
    return {**batch_view(db.get("ingestion_batches", batch_id)), "movedCount": moved}


def review_batch(batch_id: str, action: str, comment: str, actor_role: str) -> dict:
    row = db.get("ingestion_batches", batch_id)
    if not row:
        raise ValueError("批次不存在")
    summary = db.jloads(row["summary"])
    if action not in ("needs_revision", "confirm_archive", "admin_alignment"):
        raise ValueError("不支持的审核动作")
    if action == "needs_revision" and not comment.strip():
        raise ValueError("提出修改意见时必须填写具体内容")
    if action == "confirm_archive" and not summary.get("accepted"):
        raise ValueError("没有通过校验的文件，不能归档")
    if action == "admin_alignment" and actor_role != "admin":
        raise PermissionError("管理员对齐需要管理员权限")
    status = {"needs_revision": "teacher_changes_requested",
              "confirm_archive": "teacher_archived",
              "admin_alignment": "admin_alignment"}[action]
    db.insert("ingestion_reviews", {
        "id": db.new_id("ingrev"), "batch_id": batch_id, "action": action,
        "comment": comment.strip(), "actor_role": actor_role, "created_at": db.now(),
    })
    db.update("ingestion_batches", batch_id, {"status": status, "updated_at": db.now()})
    return batch_view(db.get("ingestion_batches", batch_id))
