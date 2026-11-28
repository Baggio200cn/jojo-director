"""教学视频任务包 v1：校验素材与版本，生成导入审核和幂等回执。"""
import hashlib
import io
import json
import zipfile
from pathlib import Path, PurePosixPath

from . import db
from .config import ASSETS_DIR

FORMAT = "yuke-teaching-video/v1"
MAX_ZIP_BYTES = 200 * 1024 * 1024
MAX_ASSET_BYTES = 100 * 1024 * 1024
TEMPLATES = {"intro", "concept_explainer", "operation_demo", "case_explainer"}
ROUTES = {"live_action", "slide_composite", "diagram_2d", "model_3d",
          "code_execution", "simulation", "generated_scene"}
SUFFIXES = {".txt", ".md", ".json", ".png", ".jpg", ".jpeg", ".svg", ".mp4", ".mov",
            ".webm", ".wav", ".mp3", ".csv", ".pdf", ".docx", ".pptx", ".blend", ".glb", ".gltf"}


def _required_text(value, label: str, limit: int = 2000) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label}不能为空")
    if len(text) > limit:
        raise ValueError(f"{label}超过 {limit} 字限制")
    return text


def _entries_by_name(archive: zipfile.ZipFile) -> dict:
    entries = {}
    for entry in archive.infolist():
        path = PurePosixPath(entry.filename)
        if path.is_absolute() or ".." in path.parts or entry.is_dir():
            continue
        if entry.file_size > MAX_ASSET_BYTES:
            raise ValueError(f"素材 {entry.filename} 超过 100MB 单项限制")
        entries[str(path)] = entry
    return entries


def read_package(content: bytes) -> dict:
    if not content or len(content) > MAX_ZIP_BYTES:
        raise ValueError("任务包为空或超过 200MB 限制")
    package_hash = "sha256:" + hashlib.sha256(content).hexdigest()
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ValueError("请上传教学视频任务 ZIP") from exc
    with archive:
        entries = _entries_by_name(archive)
        if "manifest.json" not in entries:
            raise ValueError("任务包根目录缺少 manifest.json")
        try:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("manifest.json 无法读取") from exc
        files = {}
        for asset in manifest.get("assets") or []:
            uri = str(asset.get("uri") or "")
            if uri not in entries:
                raise ValueError(f"素材 {asset.get('assetId') or uri} 对应的文件 {uri} 不存在")
            files[uri] = archive.read(uri)
    audit = validate_manifest(manifest, files)
    return {"manifest": manifest, "files": files, "packageHash": package_hash, "audit": audit}


def validate_manifest(manifest: dict, files: dict) -> dict:
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 1 or manifest.get("format") != FORMAT:
        raise ValueError(f"仅支持 {FORMAT}")
    package_id = _required_text(manifest.get("packageId"), "packageId", 120)
    package_version = _required_text(manifest.get("packageVersion"), "packageVersion", 120)
    course = manifest.get("course") or {}
    lesson = manifest.get("lesson") or {}
    _required_text(course.get("courseId"), "courseId", 120)
    _required_text(course.get("title"), "课程名称", 200)
    _required_text(lesson.get("lessonId"), "lessonId", 120)
    _required_text(lesson.get("audience"), "授课对象", 300)
    if not isinstance(lesson.get("lessonNumber"), int) or lesson["lessonNumber"] < 1:
        raise ValueError("课次编号无效")
    if manifest.get("template") not in TEMPLATES:
        raise ValueError("教学视频模板无效")
    _required_text(manifest.get("teachingGoal"), "教学目的")
    _required_text(manifest.get("studentObservation"), "学生观察点")
    source_versions = manifest.get("sourceVersions")
    if not isinstance(source_versions, dict) or not source_versions:
        raise ValueError("缺少独立上游版本 sourceVersions")
    for key, value in source_versions.items():
        _required_text(value, f"上游版本 {key}", 300)
    missing = manifest.get("missingEvidence") or []
    if not isinstance(missing, list):
        raise ValueError("missingEvidence 格式无效")
    for item in missing:
        if not isinstance(item, dict) or not item.get("description") or not isinstance(item.get("required"), bool):
            raise ValueError("missingEvidence 每项需含 description 和 required")
    assets = manifest.get("assets") or []
    if not isinstance(assets, list):
        raise ValueError("assets 格式无效")
    asset_ids = set()
    received_assets = []
    for asset in assets:
        asset_id = _required_text(asset.get("assetId"), "assetId", 120)
        if asset_id in asset_ids:
            raise ValueError("assetId 不能重复")
        asset_ids.add(asset_id)
        uri = _required_text(asset.get("uri"), f"素材 {asset_id} 路径", 500)
        expected = _required_text(asset.get("contentHash"), f"素材 {asset_id} 哈希", 100)
        actual = "sha256:" + hashlib.sha256(files[uri]).hexdigest()
        if actual != expected:
            raise ValueError(f"素材 {asset_id} 哈希不一致，预期 {expected}，实际 {actual}")
        for field in ("kind", "role", "sourceVersion", "rights", "allowedEgress", "allowedUse"):
            _required_text(asset.get(field), f"素材 {asset_id} 的 {field}", 300)
        if Path(uri).suffix.lower() not in SUFFIXES:
            raise ValueError(f"素材 {asset_id} 的文件类型暂不支持")
        received_assets.append({"assetId": asset_id, "kind": asset["kind"], "role": asset["role"],
                                "sourceVersion": asset["sourceVersion"], "rights": asset["rights"],
                                "allowedEgress": asset["allowedEgress"], "allowedUse": asset["allowedUse"]})
    scenes = manifest.get("scenes") or []
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= 50:
        raise ValueError("场景数量应为 1–50")
    scene_ids = set()
    scene_plans = []
    for scene in scenes:
        scene_id = _required_text(scene.get("sceneId"), "sceneId", 120)
        if scene_id in scene_ids:
            raise ValueError("sceneId 不能重复")
        scene_ids.add(scene_id)
        _required_text(scene.get("pointId"), f"场景 {scene_id} 知识点", 120)
        _required_text(scene.get("title"), f"场景 {scene_id} 标题", 200)
        _required_text(scene.get("teachingGoal"), f"场景 {scene_id} 教学目的")
        seconds = scene.get("targetSeconds")
        if not isinstance(seconds, int) or not 1 <= seconds <= 300:
            raise ValueError(f"场景 {scene_id} 时长应为 1–300 秒")
        routes = scene.get("routeCandidates") or []
        if not routes or any(route not in ROUTES for route in routes):
            raise ValueError(f"场景 {scene_id} 制作路线无效")
        evidence = scene.get("evidenceRefs") or []
        missing_refs = sorted(set(evidence) - asset_ids)
        if missing_refs:
            raise ValueError(f"场景 {scene_id} 引用了不存在的素材：{', '.join(missing_refs)}")
        selected = select_route(scene)
        scene_plans.append({
            "sceneId": scene_id,
            "pointId": scene["pointId"],
            "targetSeconds": seconds,
            "selectedRoute": selected,
            "routeReason": route_reason(scene, selected),
            "reviewRequired": bool(scene.get("reviewRequired", True)),
            "assertionReviewRequired": not bool(scene.get("assertions")),
            "checks": scene.get("assertions") or [],
        })
    required_missing = [x for x in missing if x["required"]]
    if not isinstance(manifest.get("paidGenerationApproved"), bool):
        raise ValueError("paidGenerationApproved 必须为布尔值")
    approval = manifest.get("approval") or {}
    if manifest["paidGenerationApproved"] and approval.get("paidGeneration") != "required":
        raise ValueError("已批准付费时 approval.paidGeneration 必须为 required")
    return {
        "status": "needs_material" if required_missing else "received",
        "packageId": package_id,
        "packageVersion": package_version,
        "receivedAssets": received_assets,
        "missingEvidence": missing,
        "scenePlans": scene_plans,
        "routeReviewRequired": any(x["reviewRequired"] for x in scene_plans),
        "assertionReviewRequired": any(x["assertionReviewRequired"] for x in scene_plans),
        "paidGenerationStarted": False,
        "generativeMediaAuthorized": approval.get("generativeMedia") == "authorized",
        "paidGenerationRequired": approval.get("paidGeneration") == "required",
        "fixtureOnly": any(x.get("syntheticFixture") for x in assets),
    }


def select_route(scene: dict) -> str:
    routes = scene.get("routeCandidates") or []
    if "diagram_2d" in routes and scene.get("visualSpec"):
        return "diagram_2d"
    if "live_action" in routes:
        return "live_action"
    if "slide_composite" in routes:
        return "slide_composite"
    if "diagram_2d" in routes:
        return "diagram_2d"
    return routes[0]


def route_reason(scene: dict, selected: str) -> str:
    reasons = {
        "live_action": "保留真实人物、设备或实验过程，不用生成画面冒充真实现场",
        "slide_composite": "课件与教师画面可确定性合成",
        "diagram_2d": "结构、参数或波形可由确定性二维场景表达",
        "model_3d": "空间关系有教学价值且资料充分，进入三维复核",
        "code_execution": "代码和输出需要受控执行与真实记录",
        "simulation": "需要受控仿真验证过程与参数",
        "generated_scene": "仅作为生成式候选，仍需单独批准范围与预算",
    }
    return reasons[selected]


def create_or_get(package: dict) -> dict:
    manifest = package["manifest"]
    package_hash = package["packageHash"]
    existing = db.query("import_tasks", "package_hash=?", (package_hash,))
    if existing:
        audit = db.jloads(existing[0]["audit"])
        return {**audit, "project_id": existing[0]["project_id"], "reused": True}
    same_version = db.query("import_tasks", "package_id=? AND package_version=?",
                            (manifest["packageId"], manifest["packageVersion"]))
    if same_version:
        raise ValueError("相同 packageId/packageVersion 的内容哈希已变化，请递增包版本")
    pid = db.new_id("proj")
    script_id = db.new_id("node")
    storyboard_id = db.new_id("node")
    edge_id = db.new_id("edge")
    import_id = db.new_id("import")
    now = db.now()
    course = manifest["course"]
    lesson = manifest["lesson"]
    title = f"{course['title']} · 第{lesson['lessonNumber']}节 · {manifest['teachingGoal'][:60]}"
    source_manifest = {
        "format": FORMAT,
        "packageId": manifest["packageId"],
        "packageVersion": manifest["packageVersion"],
        "packageHash": package_hash,
        "sourceVersions": manifest["sourceVersions"],
        "unusedInputs": manifest.get("unusedInputs") or [],
    }
    scenes = []
    plan_by_id = {x["sceneId"]: x for x in package["audit"]["scenePlans"]}
    for scene in manifest["scenes"]:
        plan = plan_by_id[scene["sceneId"]]
        scenes.append({**scene, "route": plan["selectedRoute"], "routeReason": plan["routeReason"],
                       "routeStatus": "needs_review" if plan["reviewRequired"] else "reviewed",
                       "assertionStatus": "needs_review" if plan["assertionReviewRequired"] else "reviewed"})
    audit = {**package["audit"], "importTaskId": import_id, "packageHash": package_hash}
    created_files = []
    try:
        with db._conn() as conn:
            conn.execute("INSERT INTO projects (id,title,status,source_manifest,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                         (pid, title, "draft", json.dumps(source_manifest, ensure_ascii=False), now, now))
            segments = [{"index": i, "narration": s.get("narration") or "",
                         "visual": s.get("visual") or s["title"], "seconds": s["targetSeconds"]}
                        for i, s in enumerate(manifest["scenes"], start=1)]
            conn.execute("INSERT INTO canvas_nodes (id,project_id,type,title,position_x,position_y,inputs,outputs,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (script_id, pid, "script", "教学视频口播与场景来源", 80, 160,
                          json.dumps({"sourceVersions": manifest["sourceVersions"]}, ensure_ascii=False),
                          json.dumps({"script": {"title": manifest["teachingGoal"], "segments": segments}},
                                     ensure_ascii=False), "succeeded", now, now))
            conn.execute("INSERT INTO canvas_nodes (id,project_id,type,title,position_x,position_y,inputs,outputs,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (storyboard_id, pid, "storyboard", "教学视频任务包 · 导入审核", 400, 160,
                          json.dumps({"packageId": manifest["packageId"],
                                      "packageVersion": manifest["packageVersion"]}, ensure_ascii=False),
                          json.dumps({"teachingVideo": {"manifest": manifest, "scenes": scenes},
                                      "importReview": audit}, ensure_ascii=False), "succeeded", now, now))
            conn.execute("INSERT INTO canvas_edges (id,project_id,source_node_id,target_node_id,source_handle,target_handle) VALUES (?,?,?,?,?,?)",
                         (edge_id, pid, script_id, storyboard_id, "output", "input"))
            for asset in manifest["assets"]:
                asset_id = db.new_id("asset")
                suffix = Path(asset["uri"]).suffix.lower()
                filename = asset_id + suffix
                (ASSETS_DIR / filename).write_bytes(package["files"][asset["uri"]])
                created_files.append(filename)
                meta = {key: asset.get(key) for key in
                        ("assetId", "role", "sourceVersion", "rights", "allowedEgress", "allowedUse",
                         "attribution", "locator", "syntheticFixture")}
                conn.execute("INSERT INTO assets (id,project_id,node_id,kind,filename,meta,created_at,folder,rights,library) VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (asset_id, pid, storyboard_id, asset["kind"], filename,
                              json.dumps(meta, ensure_ascii=False), now, "", asset["rights"], 0))
            conn.execute("INSERT INTO import_tasks (id,project_id,package_id,package_version,package_hash,format,status,manifest,audit,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (import_id, pid, manifest["packageId"], manifest["packageVersion"], package_hash,
                          FORMAT, audit["status"], json.dumps(manifest, ensure_ascii=False),
                          json.dumps(audit, ensure_ascii=False), now))
    except Exception:
        for filename in created_files:
            (ASSETS_DIR / filename).unlink(missing_ok=True)
        raise
    return {**audit, "project_id": pid, "storyboard_node_id": storyboard_id,
            "script_node_id": script_id, "reused": False}
