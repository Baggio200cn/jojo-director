"""驭课微课制作包导入：只生成可审候选，不触发模型或付费媒体任务。"""
import io
import json
import zipfile

from . import db

FORMAT = "yuke-micro-intro/v1"
MAX_ZIP_BYTES = 60 * 1024 * 1024
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_SHOTS = 50
MAX_TOTAL_SECONDS = 900
METHODS = {"teacher", "slide", "ai", "jojo"}


def _text(value, label: str, limit: int, required: bool = True) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ValueError(f"{label}不能为空")
    if len(text) > limit:
        raise ValueError(f"{label}超过 {limit} 字限制")
    return text


def read_package(content: bytes) -> dict:
    if not content or len(content) > MAX_ZIP_BYTES:
        raise ValueError("制作包为空或超过 60MB 限制")
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ValueError("请上传驭课导出的 ZIP 制作包") from exc
    with archive:
        entries = [x for x in archive.infolist()
                   if x.filename.rstrip("/").endswith("JOJO-handoff.json")]
        if len(entries) != 1:
            raise ValueError("制作包应且只应包含一份 JOJO-handoff.json；多课次包请分课导入")
        entry = entries[0]
        if entry.file_size > MAX_JSON_BYTES:
            raise ValueError("交接 JSON 超过 8MB 限制")
        try:
            data = json.loads(archive.read(entry).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("交接 JSON 无法读取") from exc
    return normalize(data)


def normalize(data: dict) -> dict:
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError(f"仅支持 {FORMAT} 制作包")
    course = _text(data.get("course"), "课程名称", 200)
    lesson = data.get("lessonNumber")
    if not isinstance(lesson, int) or lesson < 1:
        raise ValueError("课次编号无效")
    source_version = _text(data.get("sourceVersion"), "上游来源版本", 300)
    plan = data.get("plan")
    if not isinstance(plan, dict):
        raise ValueError("制作包缺少分镜方案")
    title = _text(plan.get("title"), "方案标题", 200)
    goal = _text(plan.get("goal"), "教学用途", 1000)
    shots = plan.get("shots")
    if not isinstance(shots, list) or not 1 <= len(shots) <= MAX_SHOTS:
        raise ValueError(f"镜头数量应为 1–{MAX_SHOTS}")
    normalized = []
    total = 0
    seen = set()
    for index, shot in enumerate(shots, start=1):
        if not isinstance(shot, dict):
            raise ValueError(f"镜头 {index} 格式无效")
        shot_id = shot.get("id")
        if not isinstance(shot_id, int) or shot_id < 1 or shot_id in seen:
            raise ValueError("镜头 ID 必须为正整数且不能重复")
        seen.add(shot_id)
        seconds = shot.get("seconds")
        if not isinstance(seconds, int) or not 1 <= seconds <= 60:
            raise ValueError(f"镜头 {shot_id} 时长应为 1–60 秒")
        total += seconds
        method = shot.get("method")
        if method not in METHODS:
            raise ValueError(f"镜头 {shot_id} 制作方式无效")
        visual = _text(shot.get("visual"), f"镜头 {shot_id} 画面", 4000)
        narration = _text(shot.get("narration"), f"镜头 {shot_id} 口播", 4000, required=False)
        prompt = _text(shot.get("prompt"), f"镜头 {shot_id} 提示词", 6000, required=False)
        review = _text(shot.get("review"), f"镜头 {shot_id} 核对事项", 2000, required=False)
        sources = shot.get("sources") or []
        if not isinstance(sources, list) or len(sources) > 20:
            raise ValueError(f"镜头 {shot_id} 来源数量无效")
        sources = [_text(x, f"镜头 {shot_id} 来源", 200) for x in sources]
        route_type = "code_render" if method == "slide" else "live_action" if method == "teacher" else "ai_video"
        normalized.append({
            "index": index,
            "sourceShotId": shot_id,
            "type": route_type,
            "sourceMethod": method,
            "seconds": seconds,
            "first_frame_prompt": prompt or visual,
            "last_frame_delta": "",
            "motion": prompt,
            "frame_elements": [],
            "caption": "",
            "assertions": [],
            "route_status": "needs_review",
            "assertion_status": "needs_review",
            "review_notes": review,
            "sourceSourceIds": sources,
            "narration": narration,
            "visual": visual,
        })
    if total > MAX_TOTAL_SECONDS:
        raise ValueError(f"总时长超过 {MAX_TOTAL_SECONDS} 秒限制")
    sources = []
    for source in data.get("sources") or []:
        if not isinstance(source, dict):
            continue
        sources.append({key: source.get(key) for key in ("id", "type", "label", "version", "usable")
                        if source.get(key) is not None})
    return {
        "course": course,
        "lessonNumber": lesson,
        "sourceVersion": source_version,
        "title": title,
        "goal": goal,
        "shots": normalized,
        "totalSeconds": total,
        "sources": sources,
        "referenceFramesAvailable": len(data.get("referenceFrames") or []),
    }


def create_project(package: dict) -> dict:
    pid = db.new_id("proj")
    script_id = db.new_id("node")
    storyboard_id = db.new_id("node")
    edge_id = db.new_id("edge")
    now = db.now()
    title = f"{package['course']} · 第{package['lessonNumber']}节 · {package['title']}"
    source_manifest = {
        "format": FORMAT,
        "course": package["course"],
        "lessonNumber": package["lessonNumber"],
        "sourceVersion": package["sourceVersion"],
        "sources": package["sources"],
        "integration": "yuke-zip-import",
    }
    segments = [{
        "index": shot["index"],
        "narration": shot["narration"],
        "visual": shot["visual"],
        "seconds": shot["seconds"],
    } for shot in package["shots"]]
    storyboard = {
        "title": package["title"],
        "goal": package["goal"],
        "sourceVersion": package["sourceVersion"],
        "shots": package["shots"],
    }
    review = {
        "routeReviewRequired": True,
        "assertionReviewRequired": True,
        "referenceFramesAvailable": package["referenceFramesAvailable"],
        "referenceFramesImported": 0,
        "paidGenerationStarted": False,
    }
    with db._conn() as conn:
        conn.execute("INSERT INTO projects (id,title,status,source_manifest,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                     (pid, title, "draft", json.dumps(source_manifest, ensure_ascii=False), now, now))
        conn.execute("INSERT INTO canvas_nodes (id,project_id,type,title,position_x,position_y,inputs,outputs,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (script_id, pid, "script", "驭课确认口播", 80, 160,
                      json.dumps({"goal": package["goal"], "duration": package["totalSeconds"],
                                  "sourceVersion": package["sourceVersion"]}, ensure_ascii=False),
                      json.dumps({"script": {"title": package["title"],
                                            "duration_seconds": package["totalSeconds"],
                                            "segments": segments},
                                  "sourceVersion": package["sourceVersion"]}, ensure_ascii=False),
                      "succeeded", now, now))
        conn.execute("INSERT INTO canvas_nodes (id,project_id,type,title,position_x,position_y,inputs,outputs,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (storyboard_id, pid, "storyboard", "驭课导入分镜 · 待路线与断言复核", 400, 160,
                      json.dumps({"shot_count": len(package["shots"]),
                                  "total_duration": package["totalSeconds"],
                                  "sourceVersion": package["sourceVersion"]}, ensure_ascii=False),
                      json.dumps({"storyboard": storyboard, "importReview": review}, ensure_ascii=False),
                      "succeeded", now, now))
        conn.execute("INSERT INTO canvas_edges (id,project_id,source_node_id,target_node_id,source_handle,target_handle) VALUES (?,?,?,?,?,?)",
                     (edge_id, pid, script_id, storyboard_id, "output", "input"))
    return {"project_id": pid, "script_node_id": script_id,
            "storyboard_node_id": storyboard_id, "shots": len(package["shots"]),
            "total_seconds": package["totalSeconds"], "review": review}
