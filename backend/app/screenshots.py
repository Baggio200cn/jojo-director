"""讨论截图：受控上传、按需视觉分析与评论附件。"""
import base64
import json
from functools import wraps
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import asset_ingest, auth, community, db
from .config import SCREENSHOTS_DIR, estimate_cost_cny, route
from .executors import _gw

router = APIRouter(prefix="/api/communities")
MAX_SCREENSHOT_BYTES = 8 * 1024 * 1024
MIME_BY_EXTENSION = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def _view(row: dict) -> dict:
    return {**row, "url": f"/api/communities/screenshots/{row['id']}/image"}


def _owned(screenshot_id: str, session: dict) -> dict:
    row = db.get("comment_screenshots", screenshot_id)
    if not row:
        raise ValueError("截图不存在")
    community._member(row["community_id"], session)
    return row


async def upload_screenshot(community_id: str, contribution_id: str, file: UploadFile, session: dict) -> dict:
    community._member(community_id, session)
    contribution = db.get("community_contributions", contribution_id)
    if not contribution or contribution["community_id"] != community_id:
        raise ValueError("贡献不存在")
    extension = Path(file.filename or "").suffix.lower()
    if extension not in MIME_BY_EXTENSION:
        raise ValueError("截图仅支持 PNG、JPG、JPEG、WebP")
    content = await file.read(MAX_SCREENSHOT_BYTES + 1)
    if len(content) > MAX_SCREENSHOT_BYTES:
        raise ValueError("单张截图不能超过 8MB")
    if not asset_ingest.signature_valid(extension, content):
        raise ValueError("截图内容与格式签名不匹配或文件损坏")
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    screenshot_id = db.new_id("shot")
    filename = f"{screenshot_id}{extension}"
    (SCREENSHOTS_DIR / filename).write_bytes(content)
    actor_id, _ = community._actor(session)
    row = {"id": screenshot_id, "community_id": community_id, "contribution_id": contribution_id,
           "comment_id": None, "filename": filename, "orig_name": Path(file.filename or "screenshot").name,
           "mime": MIME_BY_EXTENSION[extension], "size": len(content),
           "analysis_status": "uploaded", "analysis_text": "", "analysis_model": "",
           "analysis_error": "", "author_id": actor_id, "created_at": db.now(), "analyzed_at": None}
    db.insert("comment_screenshots", row)
    return _view(row)


async def vision_chat(route_config: dict, messages: list[dict]) -> dict:
    return await _gw(route_config).chat(route_config["model"], messages, max_tokens=1800)


async def analyze_screenshot(screenshot_id: str, note: str, session: dict) -> dict:
    row = _owned(screenshot_id, session)
    path = SCREENSHOTS_DIR / row["filename"]
    if not path.is_file():
        raise ValueError("截图文件不存在")
    auth.check_and_log(session, "qc")
    route_config = route("qc_vision")
    if not route_config.get("model"):
        raise ValueError("服务端尚未配置截图分析模型")
    task_id = db.new_id("task")
    db.insert("model_tasks", {
        "id": task_id, "project_id": "", "node_id": "", "provider": route_config["provider"],
        "model": route_config["model"], "task_type": "screenshot_analysis", "status": "created",
        "request_payload": json.dumps({"screenshotId": screenshot_id, "note": note[:1000]}, ensure_ascii=False),
        "created_at": db.now(),
    })
    db.update("comment_screenshots", screenshot_id, {"analysis_status": "analyzing", "analysis_error": ""})
    data_uri = f"data:{row['mime']};base64,{base64.b64encode(path.read_bytes()).decode()}"
    prompt = ("请分析这张教学社区讨论截图，依次给出：1. 内容摘要；2. 可见文字或界面状态；"
              "3. 可能存在的问题；4. 建议讨论或修改的事项。不要臆测截图之外的信息，无法辨认处请明确标注不确定。"
              + (f"\n老师补充说明：{note[:1000]}" if note.strip() else ""))
    try:
        response = await vision_chat(route_config, [{"role": "user", "content": [
            {"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": data_uri}},
        ]}])
        text = (response.get("text") or "").strip()
        if not text:
            raise RuntimeError("视觉模型未返回分析内容")
        now = db.now()
        db.update("comment_screenshots", screenshot_id, {
            "analysis_status": "succeeded", "analysis_text": text,
            "analysis_model": response.get("reported_model") or route_config["model"], "analyzed_at": now,
        })
        db.update("model_tasks", task_id, {
            "status": "succeeded", "finished_at": now,
            "input_tokens": response.get("input_tokens", 0), "output_tokens": response.get("output_tokens", 0),
            "reported_model": response.get("reported_model"),
            "estimated_cost_cny": estimate_cost_cny(route_config["model"], "text_generation",
                response.get("input_tokens", 0), response.get("output_tokens", 0)) or 0,
        })
    except Exception as exc:
        message = str(exc) or type(exc).__name__
        db.update("comment_screenshots", screenshot_id, {"analysis_status": "failed", "analysis_error": message})
        db.update("model_tasks", task_id, {"status": "failed", "finished_at": db.now(), "error": message})
        raise ValueError(f"截图分析失败：{message}") from exc
    return _view(db.get("comment_screenshots", screenshot_id))


def screenshots_for_comment(comment_id: str) -> list[dict]:
    return [_view(row) for row in db.query("comment_screenshots", "comment_id=? ORDER BY created_at", (comment_id,))]


def validate_attachments(screenshot_ids: list[str], community_id: str, contribution_id: str,
                         session: dict) -> None:
    for screenshot_id in dict.fromkeys(screenshot_ids):
        row = _owned(screenshot_id, session)
        if row["community_id"] != community_id or row["contribution_id"] != contribution_id:
            raise ValueError("截图与当前贡献不匹配")
        if row["comment_id"]:
            raise ValueError("截图已经用于其他评论")


def attach_to_comment(screenshot_ids: list[str], community_id: str, contribution_id: str,
                      comment_id: str, session: dict) -> None:
    validate_attachments(screenshot_ids, community_id, contribution_id, session)
    for screenshot_id in dict.fromkeys(screenshot_ids):
        db.update("comment_screenshots", screenshot_id, {"comment_id": comment_id})


class AnalysisIn(BaseModel):
    note: str = ""


def errors(fn):
    @wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
    return wrapper


@router.post("/{community_id}/contributions/{contribution_id}/screenshots")
@errors
async def upload_route(community_id: str, contribution_id: str, request: Request,
                       file: UploadFile = File(...)):
    return await upload_screenshot(community_id, contribution_id, file, auth.require(request))


@router.post("/screenshots/{screenshot_id}/analyze")
@errors
async def analyze_route(screenshot_id: str, payload: AnalysisIn, request: Request):
    return await analyze_screenshot(screenshot_id, payload.note, auth.require(request))


@router.get("/screenshots/{screenshot_id}/image")
@errors
async def image_route(screenshot_id: str, request: Request):
    row = _owned(screenshot_id, auth.require(request))
    path = SCREENSHOTS_DIR / row["filename"]
    if not path.is_file():
        raise ValueError("截图文件不存在")
    return FileResponse(path, media_type=row["mime"], filename=row["orig_name"])
