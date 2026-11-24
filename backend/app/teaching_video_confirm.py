"""教学视频方案确认：版本与场景范围绑定，首版只允许无付费路线。"""
import hashlib
import json

from . import db


def confirm(project_id: str, payload: dict) -> dict:
    import_id = str(payload.get("importTaskId") or "")
    package_hash = str(payload.get("packageHash") or "")
    scene_ids = payload.get("sceneIds") or []
    if not import_id or not package_hash or not isinstance(scene_ids, list) or not scene_ids:
        raise ValueError("确认记录需要导入任务、包哈希和场景范围")
    if payload.get("generativeMediaAuthorized") or payload.get("paidGenerationApproved"):
        raise ValueError("首版仅允许确定性 2D 与已有素材合成，未开放生成式媒体或付费批准")
    rows = db.query("import_tasks", "id=?", (import_id,))
    if not rows or rows[0]["project_id"] != project_id:
        raise ValueError("导入任务不存在或不属于当前项目")
    task = rows[0]
    if task["package_hash"] != package_hash:
        raise ValueError("包哈希与导入任务不一致，请重新导入核对")
    if task["status"] == "needs_material":
        raise ValueError("仍有必需素材缺失，不能批准制作")
    audit = db.jloads(task["audit"])
    allowed = {x["sceneId"] for x in audit["scenePlans"]}
    selected = set(scene_ids)
    if not selected.issubset(allowed):
        raise ValueError("确认范围包含不存在的场景")
    plans = [x for x in audit["scenePlans"] if x["sceneId"] in selected]
    plan_hash = "sha256:" + hashlib.sha256(json.dumps({
        "packageHash": package_hash, "scenePlans": plans,
    }, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    existing = db.query("video_plan_confirmations", "plan_hash=?", (plan_hash,))
    if existing:
        record = confirmation_record(existing[0])
        audit["status"] = "plan_approved"
        audit["confirmation"] = record
        with db._conn() as conn:
            conn.execute("UPDATE import_tasks SET status='plan_approved',audit=? WHERE id=?",
                         (json.dumps(audit, ensure_ascii=False), import_id))
            nodes = db.query("canvas_nodes", "project_id=? AND type='storyboard'", (project_id,))
            for node in nodes:
                out = db.jloads(node["outputs"])
                if out.get("importReview", {}).get("importTaskId") == import_id:
                    out["importReview"] = audit
                    conn.execute("UPDATE canvas_nodes SET outputs=?,updated_at=? WHERE id=?",
                                 (json.dumps(out, ensure_ascii=False), db.now(), node["id"]))
        return {**record, "importTaskId": import_id,
                "packageHash": package_hash, "reused": True}
    confirmation_id = db.new_id("confirm")
    now = db.now()
    nodes = db.query("canvas_nodes", "project_id=? AND type='storyboard'", (project_id,))
    with db._conn() as conn:
        conn.execute("INSERT INTO video_plan_confirmations (id,project_id,import_task_id,package_hash,plan_hash,scene_ids,generative_media,paid_generation,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                     (confirmation_id, project_id, import_id, package_hash, plan_hash,
                      json.dumps(sorted(selected)), "not_authorized", "not_required", now))
        audit["status"] = "plan_approved"
        audit["confirmation"] = {
            "confirmationId": confirmation_id, "planHash": plan_hash,
            "status": "plan_approved", "sceneIds": sorted(selected),
            "generativeMedia": "not_authorized", "paidGeneration": "not_required",
            "confirmedAt": now,
        }
        conn.execute("UPDATE import_tasks SET status='plan_approved',audit=? WHERE id=?",
                     (json.dumps(audit, ensure_ascii=False), import_id))
        for node in nodes:
            out = db.jloads(node["outputs"])
            if out.get("importReview", {}).get("importTaskId") == import_id:
                out["importReview"] = audit
                conn.execute("UPDATE canvas_nodes SET outputs=?,updated_at=? WHERE id=?",
                             (json.dumps(out, ensure_ascii=False), now, node["id"]))
    return {**audit["confirmation"], "importTaskId": import_id,
            "packageHash": package_hash, "reused": False}


def confirmation_record(row: dict) -> dict:
    return {
        "confirmationId": row["id"], "planHash": row["plan_hash"],
        "status": "plan_approved", "sceneIds": db.jloads(row["scene_ids"]),
        "generativeMedia": row["generative_media"], "paidGeneration": row["paid_generation"],
        "confirmedAt": row["created_at"],
    }
