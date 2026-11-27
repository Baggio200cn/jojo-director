"""课题社区：贡献、讨论、任务、审校、场景接驳与版本发布。"""
import hashlib
import json
from functools import wraps

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import auth, db

router = APIRouter(prefix="/api/communities")
REVIEW_ROLES = {"host", "reviewer"}
BIND_ROLES = {"host", "reviewer", "technical"}
CONTRIBUTION_KINDS = {"text", "question", "evidence", "scene", "asset", "repository"}
TASK_ACTIONS = {"claim", "complete", "done", "reopen"}


def _actor(session: dict) -> tuple[str, str]:
    if session.get("role") == "admin":
        return "admin", "管理员"
    code = session.get("invite_code") or "unknown"
    actor_id = "invite_" + hashlib.sha256(code.encode("utf-8")).hexdigest()[:16]
    rows = db.query("invite_codes", "code=?", (code,))
    return actor_id, (rows[0]["label"] or "邀请教师") if rows else "邀请教师"


def _membership(community_id: str, actor_id: str) -> dict | None:
    rows = db.query("community_members", "community_id=? AND actor_id=?", (community_id, actor_id))
    return rows[0] if rows else None


def _member(community_id: str, session: dict) -> tuple[str, str, dict]:
    actor_id, name = _actor(session)
    membership = _membership(community_id, actor_id)
    if session.get("role") == "admin" and not membership:
        membership = {"role": "host", "actor_id": actor_id, "display_name": name}
    if not membership:
        raise PermissionError("请先加入该课题社区")
    return actor_id, name, membership


def _can_review(session: dict, membership: dict) -> bool:
    return session.get("role") == "admin" or membership.get("role") in REVIEW_ROLES


def _can_manage(session: dict, membership: dict) -> bool:
    return session.get("role") == "admin" or membership.get("role") == "host"


def _community(community_id: str) -> dict:
    row = db.get("communities", community_id)
    if not row:
        raise ValueError("课题社区不存在")
    return row


def _notify(recipient_id: str, community_id: str, kind: str, message: str,
            contribution_id: str | None = None) -> None:
    db.insert("community_notifications", {
        "id": db.new_id("notice"), "recipient_id": recipient_id,
        "community_id": community_id, "contribution_id": contribution_id,
        "kind": kind, "message": message, "read_at": None, "created_at": db.now(),
    })


def _notify_members(community_id: str, actor_id: str, kind: str, message: str,
                    contribution_id: str | None = None) -> None:
    for member in db.query("community_members", "community_id=?", (community_id,)):
        if member["actor_id"] != actor_id:
            _notify(member["actor_id"], community_id, kind, message, contribution_id)


def _community_summary(row: dict, session: dict) -> dict:
    actor_id, _ = _actor(session)
    membership = _membership(row["id"], actor_id)
    with db._conn() as conn:
        counts = conn.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM community_members WHERE community_id=?) members, "
            "(SELECT COUNT(*) FROM community_contributions WHERE community_id=?) contributions, "
            "(SELECT COUNT(*) FROM community_tasks WHERE community_id=? AND status!='done') open_tasks",
            (row["id"], row["id"], row["id"])).fetchone()
    return {**row, "member_count": counts["members"], "contribution_count": counts["contributions"],
            "open_task_count": counts["open_tasks"],
            "my_role": membership["role"] if membership else ("host" if session.get("role") == "admin" else "")}


def list_communities(session: dict) -> list[dict]:
    return [_community_summary(row, session)
            for row in db.query("communities", "status='active' ORDER BY updated_at DESC")]


def create_community(payload: dict, session: dict) -> dict:
    if session.get("role") != "admin":
        raise PermissionError("只有管理员可以创建课题社区")
    title = str(payload.get("title") or "").strip()
    if not title:
        raise ValueError("课题名称不能为空")
    actor_id, _ = _actor(session)
    now = db.now()
    community_id = db.new_id("community")
    row = {"id": community_id, "title": title,
           "domain": str(payload.get("domain") or "").strip(),
           "description": str(payload.get("description") or "").strip(),
           "goals": str(payload.get("goals") or "").strip(), "status": "active",
           "current_version": 0, "created_by": actor_id,
           "created_at": now, "updated_at": now}
    db.insert("communities", row)
    db.insert("community_members", {
        "id": db.new_id("member"), "community_id": community_id, "actor_id": actor_id,
        "display_name": "管理员", "role": "host", "joined_at": now,
    })
    return _community_summary(row, session)


def join_community(community_id: str, session: dict) -> dict:
    _community(community_id)
    actor_id, name = _actor(session)
    with db._conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO community_members "
            "(id,community_id,actor_id,display_name,role,joined_at) VALUES (?,?,?,?,?,?)",
            (db.new_id("member"), community_id, actor_id, name,
             "host" if session.get("role") == "admin" else "teacher", db.now()))
    return community_detail(community_id, session)


def _release_view(row: dict) -> dict:
    return {**row, "contribution_ids": json.loads(row["contribution_ids"])}


def _contribution_view(row: dict) -> dict:
    out = dict(row)
    if row.get("asset_id"):
        asset = db.get("assets", row["asset_id"])
        if asset:
            out["asset"] = {"id": asset["id"], "kind": asset["kind"],
                            "url": f"/assets/{asset['filename']}", "folder": asset["folder"],
                            "rights": asset["rights"], "meta": db.jloads(asset["meta"])}
    if row.get("kind") == "repository":
        from . import repository
        out["repository"] = repository.repository_for_contribution(row["id"])
    with db._conn() as conn:
        out["comment_count"] = conn.execute(
            "SELECT COUNT(*) FROM community_comments WHERE contribution_id=?", (row["id"],)).fetchone()[0]
    return out


def _create_contribution(community_id: str, payload: dict, session: dict,
                         asset: dict | None = None, batch_id: str | None = None) -> dict:
    actor_id, name, _ = _member(community_id, session)
    if asset:
        rows = db.query("community_contributions", "community_id=? AND asset_id=?",
                        (community_id, asset["id"]))
        if rows:
            return _contribution_view(rows[0])
    title = str(payload.get("title") or "").strip()
    if not title:
        raise ValueError("贡献标题不能为空")
    kind = str(payload.get("kind") or ("asset" if asset else "text"))
    if kind not in CONTRIBUTION_KINDS or (kind == "asset" and not asset):
        raise ValueError("不支持的贡献类型")
    task_id = payload.get("task_id") or None
    if task_id:
        task = db.get("community_tasks", task_id)
        if not task or task["community_id"] != community_id:
            raise ValueError("关联任务不存在")
    now = db.now()
    contribution_id = db.new_id("contribution")
    body = str(payload.get("body") or "").strip()
    row = {"id": contribution_id, "community_id": community_id, "batch_id": batch_id,
           "asset_id": asset["id"] if asset else None,
           "task_id": task_id, "kind": kind, "title": title,
           "body": body, "status": "under_discussion", "version": 1,
           "author_id": actor_id, "author_name": name,
           "bound_project_id": None, "bound_node_id": None,
           "created_at": now, "updated_at": now}
    db.insert("community_contributions", row)
    db.insert("contribution_versions", {
        "id": db.new_id("cver"), "contribution_id": contribution_id, "version": 1,
        "body": body, "asset_id": asset["id"] if asset else None,
        "note": "首次提交", "author_id": actor_id, "author_name": name, "created_at": now,
    })
    db.update("communities", community_id, {"updated_at": now})
    _notify_members(community_id, actor_id, "new_contribution", f"{name} 提交了《{title}》", contribution_id)
    return _contribution_view(row)


def create_text_contribution(community_id: str, payload: dict, session: dict) -> dict:
    _community(community_id)
    return _create_contribution(community_id, payload, session)


def create_asset_contribution(community_id: str, asset_id: str, session: dict,
                              batch_id: str | None = None) -> dict:
    _community(community_id)
    asset = db.get("assets", asset_id)
    if not asset:
        raise ValueError("素材不存在")
    meta = db.jloads(asset["meta"])
    return _create_contribution(community_id, {
        "kind": "asset", "title": meta.get("orig_name") or asset["filename"],
        "body": meta.get("note") or meta.get("relativePath") or "",
    }, session, asset=asset, batch_id=batch_id)


def import_batch(community_id: str, batch_id: str, session: dict) -> list[dict]:
    if session.get("role") != "admin":
        raise PermissionError("历史批次导入需要管理员权限")
    if not db.get("ingestion_batches", batch_id):
        raise ValueError("上传批次不存在")
    rows = db.query("assets", "json_extract(meta,'$.uploadBatchId')=?", (batch_id,))
    return [create_asset_contribution(community_id, row["id"], session, batch_id) for row in rows]


def community_detail(community_id: str, session: dict) -> dict:
    community = _community(community_id)
    summary = _community_summary(community, session)
    actor_id, _ = _actor(session)
    contributions = [_contribution_view(row) for row in db.query(
        "community_contributions", "community_id=? ORDER BY updated_at DESC", (community_id,))]
    return {
        "community": summary,
        "members": db.query("community_members", "community_id=? ORDER BY joined_at", (community_id,)),
        "contributions": contributions,
        "tasks": db.query("community_tasks", "community_id=? ORDER BY updated_at DESC", (community_id,)),
        "versions": [_release_view(row) for row in db.query(
            "community_versions", "community_id=? ORDER BY version DESC", (community_id,))],
        "notifications": db.query(
            "community_notifications", "recipient_id=? AND community_id=? ORDER BY created_at DESC LIMIT 20",
            (actor_id, community_id)),
    }


def contribution_detail(community_id: str, contribution_id: str, session: dict) -> dict:
    _community(community_id)
    row = db.get("community_contributions", contribution_id)
    if not row or row["community_id"] != community_id:
        raise ValueError("贡献不存在")
    from . import screenshots
    comments = db.query("community_comments", "contribution_id=? ORDER BY created_at", (contribution_id,))
    return {"contribution": _contribution_view(row),
            "comments": [{**comment, "screenshots": screenshots.screenshots_for_comment(comment["id"])}
                         for comment in comments],
            "reviews": db.query("community_reviews", "contribution_id=? ORDER BY created_at", (contribution_id,)),
            "versions": db.query("contribution_versions", "contribution_id=? ORDER BY version DESC", (contribution_id,))}


def add_comment(community_id: str, contribution_id: str, payload: dict, session: dict) -> dict:
    actor_id, name, _ = _member(community_id, session)
    body = str(payload.get("body") or "").strip()
    screenshot_ids = list(payload.get("screenshot_ids") or [])
    if not body and not screenshot_ids:
        raise ValueError("回复内容或截图不能为空")
    contribution = db.get("community_contributions", contribution_id)
    if not contribution or contribution["community_id"] != community_id:
        raise ValueError("贡献不存在")
    parent_id = payload.get("parent_id") or None
    if parent_id:
        parent = db.get("community_comments", parent_id)
        if not parent or parent["contribution_id"] != contribution_id:
            raise ValueError("回复目标不存在")
    anchor_path = str(payload.get("anchor_path") or "").strip()
    anchor_line = int(payload.get("anchor_line") or 0) or None
    if anchor_line and not anchor_path:
        raise ValueError("代码行评论必须同时指定文件")
    if anchor_path:
        from . import repository
        anchor_path = repository.safe_path(anchor_path)
        repo = repository.repository_for_contribution(contribution_id)
        if not repo:
            raise ValueError("只有代码仓库贡献支持文件评论")
        files = db.query("code_repository_files", "repository_id=? AND path=?", (repo["id"], anchor_path))
        if not files:
            raise ValueError("评论文件不在仓库快照中")
        if anchor_line and (anchor_line < 1 or anchor_line > files[0]["lines"]):
            raise ValueError("评论代码行超出文件范围")
    from . import screenshots
    screenshots.validate_attachments(screenshot_ids, community_id, contribution_id, session)
    row = {"id": db.new_id("comment"), "community_id": community_id,
           "contribution_id": contribution_id, "parent_id": parent_id, "body": body,
           "author_id": actor_id, "author_name": name, "created_at": db.now(),
           "anchor_path": anchor_path or None, "anchor_line": anchor_line}
    db.insert("community_comments", row)
    screenshots.attach_to_comment(screenshot_ids, community_id, contribution_id, row["id"], session)
    db.update("community_contributions", contribution_id, {"updated_at": db.now()})
    recipients = {contribution["author_id"]}
    if parent_id:
        recipients.add(db.get("community_comments", parent_id)["author_id"])
    for recipient in recipients - {actor_id}:
        _notify(recipient, community_id, "comment", f"{name} 回复了《{contribution['title']}》", contribution_id)
    return row


def review_contribution(community_id: str, contribution_id: str, payload: dict, session: dict) -> dict:
    actor_id, name, membership = _member(community_id, session)
    if not _can_review(session, membership):
        raise PermissionError("需要学科审校人或主持人权限")
    decision = str(payload.get("decision") or "")
    if decision not in ("approved", "changes_requested"):
        raise ValueError("不支持的审校决定")
    comment = str(payload.get("comment") or "").strip()
    if decision == "changes_requested" and not comment:
        raise ValueError("退回修改必须填写具体意见")
    contribution = db.get("community_contributions", contribution_id)
    if not contribution or contribution["community_id"] != community_id:
        raise ValueError("贡献不存在")
    row = {"id": db.new_id("review"), "contribution_id": contribution_id,
           "decision": decision, "comment": comment, "reviewer_id": actor_id,
           "reviewer_name": name, "created_at": db.now()}
    db.insert("community_reviews", row)
    status = "approved_for_production" if decision == "approved" else "changes_requested"
    db.update("community_contributions", contribution_id, {"status": status, "updated_at": db.now()})
    if contribution["author_id"] != actor_id:
        _notify(contribution["author_id"], community_id, "review",
                f"《{contribution['title']}》{'已通过学科审校' if decision == 'approved' else '需要修改'}", contribution_id)
    return contribution_detail(community_id, contribution_id, session)


def add_text_revision(community_id: str, contribution_id: str, payload: dict, session: dict) -> dict:
    actor_id, name, membership = _member(community_id, session)
    row = db.get("community_contributions", contribution_id)
    if not row or row["community_id"] != community_id:
        raise ValueError("贡献不存在")
    if row["author_id"] != actor_id and not _can_review(session, membership):
        raise PermissionError("只有贡献作者或审校人可以提交新版本")
    body = str(payload.get("body") or "").strip()
    if not body:
        raise ValueError("新版本内容不能为空")
    version = int(row["version"]) + 1
    db.insert("contribution_versions", {
        "id": db.new_id("cver"), "contribution_id": contribution_id, "version": version,
        "body": body, "asset_id": row["asset_id"],
        "note": str(payload.get("note") or "").strip(), "author_id": actor_id,
        "author_name": name, "created_at": db.now(),
    })
    db.update("community_contributions", contribution_id, {
        "body": body, "version": version, "status": "under_discussion",
        "bound_project_id": None, "bound_node_id": None, "updated_at": db.now(),
    })
    return contribution_detail(community_id, contribution_id, session)


def add_asset_revision(community_id: str, contribution_id: str, asset_id: str,
                       note: str, session: dict) -> dict:
    actor_id, name, membership = _member(community_id, session)
    row = db.get("community_contributions", contribution_id)
    if not row or row["community_id"] != community_id:
        raise ValueError("贡献不存在")
    if row["author_id"] != actor_id and not _can_review(session, membership):
        raise PermissionError("只有贡献作者或审校人可以提交新版本")
    if not db.get("assets", asset_id):
        raise ValueError("新素材不存在")
    version = int(row["version"]) + 1
    db.insert("contribution_versions", {
        "id": db.new_id("cver"), "contribution_id": contribution_id, "version": version,
        "body": row["body"], "asset_id": asset_id, "note": note.strip(),
        "author_id": actor_id, "author_name": name, "created_at": db.now(),
    })
    db.update("community_contributions", contribution_id, {
        "asset_id": asset_id, "version": version, "status": "under_discussion",
        "bound_project_id": None, "bound_node_id": None, "updated_at": db.now(),
    })
    return contribution_detail(community_id, contribution_id, session)


def create_task(community_id: str, payload: dict, session: dict) -> dict:
    actor_id, _, membership = _member(community_id, session)
    if not _can_review(session, membership):
        raise PermissionError("只有主持人或审校人可以创建任务")
    title = str(payload.get("title") or "").strip()
    if not title:
        raise ValueError("任务标题不能为空")
    now = db.now()
    row = {"id": db.new_id("task"), "community_id": community_id, "title": title,
           "description": str(payload.get("description") or "").strip(),
           "kind": str(payload.get("kind") or "review"), "status": "open",
           "assignee_id": None, "assignee_name": None, "created_by": actor_id,
           "created_at": now, "updated_at": now}
    db.insert("community_tasks", row)
    return row


def task_action(community_id: str, task_id: str, action: str, session: dict) -> dict:
    actor_id, name, membership = _member(community_id, session)
    if action not in TASK_ACTIONS:
        raise ValueError("不支持的任务动作")
    row = db.get("community_tasks", task_id)
    if not row or row["community_id"] != community_id:
        raise ValueError("任务不存在")
    can_review = _can_review(session, membership)
    fields = {"updated_at": db.now()}
    if action == "claim":
        if row["status"] != "open":
            raise ValueError("该任务已被认领或已结束")
        fields.update({"status": "in_progress", "assignee_id": actor_id, "assignee_name": name})
    elif action == "complete":
        if row["assignee_id"] != actor_id and not can_review:
            raise PermissionError("只有认领人或审校人可以提交任务")
        fields["status"] = "review"
    elif action == "done":
        if not can_review:
            raise PermissionError("任务验收需要主持人或审校人权限")
        fields["status"] = "done"
    else:
        if not can_review:
            raise PermissionError("重开任务需要主持人或审校人权限")
        fields.update({"status": "open", "assignee_id": None, "assignee_name": None})
    db.update("community_tasks", task_id, fields)
    if row["created_by"] != actor_id:
        _notify(row["created_by"], community_id, "task", f"{name} 更新了任务《{row['title']}》")
    return db.get("community_tasks", task_id)


def bind_contribution(community_id: str, contribution_id: str, payload: dict, session: dict) -> dict:
    actor_id, _, membership = _member(community_id, session)
    if session.get("role") != "admin" and membership.get("role") not in BIND_ROLES:
        raise PermissionError("场景接驳需要主持人、审校人或技术团队权限")
    row = db.get("community_contributions", contribution_id)
    if not row or row["community_id"] != community_id:
        raise ValueError("贡献不存在")
    if row["status"] not in ("approved_for_production", "bound_to_scene"):
        raise ValueError("贡献通过学科审校后才能接驳场景")
    project_id = str(payload.get("project_id") or "")
    node_id = str(payload.get("node_id") or "")
    if not db.get("projects", project_id):
        raise ValueError("目标项目不存在")
    node = db.get("canvas_nodes", node_id)
    if not node or node["project_id"] != project_id:
        raise ValueError("目标场景不存在")
    if row["asset_id"]:
        asset = db.get("assets", row["asset_id"])
        outputs = db.jloads(node["outputs"])
        outputs.update({"asset_url": f"/assets/{asset['filename']}", "asset_id": asset["id"],
                        "source": "community", "community_contribution_id": contribution_id})
        db.update("canvas_nodes", node_id, {"outputs": json.dumps(outputs, ensure_ascii=False),
                                            "status": "succeeded", "updated_at": db.now()})
    inputs = db.jloads(node["inputs"])
    inputs["community_contribution_id"] = contribution_id
    db.update("canvas_nodes", node_id, {"inputs": json.dumps(inputs, ensure_ascii=False),
                                        "updated_at": db.now()})
    db.update("community_contributions", contribution_id, {
        "bound_project_id": project_id, "bound_node_id": node_id,
        "status": "bound_to_scene", "updated_at": db.now(),
    })
    if row["author_id"] != actor_id:
        _notify(row["author_id"], community_id, "binding", f"《{row['title']}》已接驳教学场景", contribution_id)
    return contribution_detail(community_id, contribution_id, session)


def publish_version(community_id: str, payload: dict, session: dict) -> dict:
    actor_id, name, membership = _member(community_id, session)
    if not _can_manage(session, membership):
        raise PermissionError("课题版本发布需要主持人权限")
    title = str(payload.get("title") or "").strip()
    if not title:
        raise ValueError("版本标题不能为空")
    community = _community(community_id)
    approved = db.query("community_contributions",
                        "community_id=? AND status IN ('approved_for_production','bound_to_scene') ORDER BY updated_at",
                        (community_id,))
    if not approved:
        raise ValueError("没有已通过审校的贡献，不能发布课题版本")
    version = int(community["current_version"]) + 1
    row = {"id": db.new_id("release"), "community_id": community_id, "version": version,
           "title": title, "summary": str(payload.get("summary") or "").strip(),
           "contribution_ids": json.dumps([x["id"] for x in approved]),
           "created_by": actor_id, "creator_name": name, "created_at": db.now()}
    db.insert("community_versions", row)
    db.update("communities", community_id, {"current_version": version, "updated_at": db.now()})
    _notify_members(community_id, actor_id, "version", f"课题版本 V{version}《{title}》已发布")
    return {**row, "contribution_ids": [x["id"] for x in approved]}


def set_member_role(community_id: str, member_id: str, role: str, session: dict) -> dict:
    actor_id, _, membership = _member(community_id, session)
    if not _can_manage(session, membership):
        raise PermissionError("成员角色管理需要主持人权限")
    if role not in ("teacher", "reviewer", "technical"):
        raise ValueError("不支持的成员角色")
    row = db.get("community_members", member_id)
    if not row or row["community_id"] != community_id:
        raise ValueError("成员不存在")
    db.update("community_members", member_id, {"role": role})
    if row["actor_id"] != actor_id:
        _notify(row["actor_id"], community_id, "member", "您在课题社区中的角色已更新")
    return db.get("community_members", member_id)


def mark_notifications_read(community_id: str, session: dict) -> dict:
    actor_id, _ = _actor(session)
    with db._conn() as conn:
        conn.execute("UPDATE community_notifications SET read_at=? WHERE recipient_id=? AND community_id=? AND read_at IS NULL",
                     (db.now(), actor_id, community_id))
    return {"ok": True}


def errors(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
    return wrapper


class ContributionIn(BaseModel):
    kind: str = "text"
    title: str
    body: str = ""
    task_id: str = ""


class CommentIn(BaseModel):
    body: str
    parent_id: str = ""
    anchor_path: str = ""
    anchor_line: int = 0
    screenshot_ids: list[str] = []


class ReviewIn(BaseModel):
    decision: str
    comment: str = ""


class RevisionIn(BaseModel):
    body: str
    note: str = ""


class TaskIn(BaseModel):
    title: str
    description: str = ""
    kind: str = "review"


class VersionIn(BaseModel):
    title: str
    summary: str = ""


class BindIn(BaseModel):
    project_id: str
    node_id: str


class BatchIn(BaseModel):
    batch_id: str


@router.get("")
@errors
def list_route(request: Request):
    return list_communities(auth.require(request))


@router.post("")
@errors
def create_route(payload: dict, request: Request):
    return create_community(payload, auth.require(request))


@router.get("/{community_id}")
@errors
def detail_route(community_id: str, request: Request):
    return community_detail(community_id, auth.require(request))


@router.post("/{community_id}/join")
@errors
def join_route(community_id: str, request: Request):
    return join_community(community_id, auth.require(request))


@router.post("/{community_id}/contributions")
@errors
def contribution_route(community_id: str, payload: ContributionIn, request: Request):
    return create_text_contribution(community_id, payload.model_dump(), auth.require(request))


@router.get("/{community_id}/contributions/{contribution_id}")
@errors
def contribution_detail_route(community_id: str, contribution_id: str, request: Request):
    return contribution_detail(community_id, contribution_id, auth.require(request))


@router.post("/{community_id}/contributions/{contribution_id}/comments")
@errors
def comment_route(community_id: str, contribution_id: str, payload: CommentIn, request: Request):
    return add_comment(community_id, contribution_id, payload.model_dump(), auth.require(request))


@router.post("/{community_id}/contributions/{contribution_id}/review")
@errors
def review_route(community_id: str, contribution_id: str, payload: ReviewIn, request: Request):
    return review_contribution(community_id, contribution_id, payload.model_dump(), auth.require(request))


@router.post("/{community_id}/contributions/{contribution_id}/revisions")
@errors
def revision_route(community_id: str, contribution_id: str, payload: RevisionIn, request: Request):
    return add_text_revision(community_id, contribution_id, payload.model_dump(), auth.require(request))


@router.post("/{community_id}/contributions/{contribution_id}/bind")
@errors
def bind_route(community_id: str, contribution_id: str, payload: BindIn, request: Request):
    return bind_contribution(community_id, contribution_id, payload.model_dump(), auth.require(request))


@router.post("/{community_id}/tasks")
@errors
def task_route(community_id: str, payload: TaskIn, request: Request):
    return create_task(community_id, payload.model_dump(), auth.require(request))


@router.post("/{community_id}/tasks/{task_id}/{action}")
@errors
def task_action_route(community_id: str, task_id: str, action: str, request: Request):
    return task_action(community_id, task_id, action, auth.require(request))


@router.post("/{community_id}/versions")
@errors
def version_route(community_id: str, payload: VersionIn, request: Request):
    return publish_version(community_id, payload.model_dump(), auth.require(request))


@router.post("/{community_id}/members/{member_id}/{role}")
@errors
def member_role_route(community_id: str, member_id: str, role: str, request: Request):
    return set_member_role(community_id, member_id, role, auth.require(request))


@router.post("/{community_id}/notifications/read")
@errors
def notification_route(community_id: str, request: Request):
    return mark_notifications_read(community_id, auth.require(request))


@router.post("/{community_id}/import-batch")
@errors
def import_batch_route(community_id: str, payload: BatchIn, request: Request):
    return import_batch(community_id, payload.batch_id, auth.require(request))
