"""无付费教学视频候选：确定性 2D、已有素材、字幕和场景级送修。"""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from . import db
from .config import ASSETS_DIR
from .render.pwm_waveform import render_sweep


def _ffmpeg(args: list[str]) -> None:
    result = subprocess.run(["ffmpeg", "-y", *args], capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if result.returncode:
        raise RuntimeError("ffmpeg 合成失败: " + result.stderr[-500:])


def _asset(project_id: str, node_id: str, kind: str, filename: str, meta: dict) -> str:
    asset_id = Path(filename).stem
    db.insert("assets", {
        "id": asset_id, "project_id": project_id, "node_id": node_id,
        "kind": kind, "filename": filename, "meta": json.dumps(meta, ensure_ascii=False),
        "created_at": db.now(), "folder": "", "rights": "own", "library": 0,
    })
    return asset_id


def _source_asset(project_id: str, original_id: str) -> dict | None:
    for row in db.query("assets", "project_id=?", (project_id,)):
        if db.jloads(row.get("meta")).get("assetId") == original_id:
            return row
    return None


def _scene_hash(scene: dict) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(scene, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _srt_time(seconds: float) -> str:
    total_ms = round(seconds * 1000)
    hours, rest = divmod(total_ms, 3600000)
    minutes, rest = divmod(rest, 60000)
    secs, ms = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _subtitle(project_id: str, node_id: str, candidate_id: str, scenes: list[dict]) -> str:
    cursor = 0.0
    blocks = []
    for index, scene in enumerate(scenes, start=1):
        text = str(scene.get("narration") or "").strip()
        duration = float(scene["targetSeconds"])
        if text:
            blocks.append(f"{index}\n{_srt_time(cursor)} --> {_srt_time(cursor + duration)}\n{text}\n")
        cursor += duration
    filename = f"{candidate_id}.srt"
    (ASSETS_DIR / filename).write_text("\n".join(blocks), encoding="utf-8")
    return _asset(project_id, node_id, "doc", filename,
                  {"candidateId": candidate_id, "role": "subtitle", "rights": "own"})


def _scene_video(project_id: str, node_id: str, candidate_id: str, scene: dict) -> tuple[str, str]:
    scene_id = scene["sceneId"]
    route = scene["route"]
    asset_id = db.new_id("asset")
    out = ASSETS_DIR / f"{asset_id}.mp4"
    if route == "diagram_2d":
        spec = scene.get("visualSpec") or {}
        duties = spec.get("dutyCycleSequence")
        if not duties:
            refs = scene.get("slideRefs") or []
            if refs:
                return _slide_video(project_id, node_id, candidate_id, scene)
            raise ValueError(f"场景 {scene_id} 缺少可执行的确定性 2D 参数")
        render_sweep(str(out), duty_from=duties[0], duty_to=duties[-1],
                     steps=len(duties), duration=scene["targetSeconds"], fps=24)
    elif route == "live_action":
        refs = scene.get("evidenceRefs") or []
        sources = [_source_asset(project_id, ref) for ref in refs]
        sources = [x for x in sources if x and x["kind"] == "video"]
        if not sources:
            raise ValueError(f"场景 {scene_id} 缺少可用的真实视频素材")
        shutil.copyfile(ASSETS_DIR / sources[0]["filename"], out)
    elif route == "slide_composite":
        return _slide_video(project_id, node_id, candidate_id, scene)
    else:
        raise ValueError(f"场景 {scene_id} 的路线 {route} 尚未实现")
    return _asset(project_id, node_id, "video", out.name,
                  {"candidateId": candidate_id, "sceneId": scene_id, "route": route,
                   "rights": "own", "syntheticFixture": True}), out.name


def _slide_video(project_id: str, node_id: str, candidate_id: str, scene: dict) -> tuple[str, str]:
    refs = scene.get("slideRefs") or scene.get("evidenceRefs") or []
    sources = [_source_asset(project_id, ref) for ref in refs]
    sources = [x for x in sources if x and x["kind"] == "image"]
    if not sources:
        raise ValueError(f"场景 {scene['sceneId']} 缺少课件图片素材")
    asset_id = db.new_id("asset")
    out = ASSETS_DIR / f"{asset_id}.mp4"
    _ffmpeg(["-loop", "1", "-framerate", "24", "-i", str(ASSETS_DIR / sources[0]["filename"]),
             "-t", str(scene["targetSeconds"]), "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "24", str(out)])
    return _asset(project_id, node_id, "video", out.name,
                  {"candidateId": candidate_id, "sceneId": scene["sceneId"],
                   "route": "slide_composite", "rights": "own", "syntheticFixture": True}), out.name


def start(project_id: str, payload: dict) -> dict:
    nodes = db.query("canvas_nodes", "project_id=? AND type='storyboard'", (project_id,))
    if not nodes:
        raise ValueError("当前项目没有教学视频任务包")
    node = nodes[0]
    out = db.jloads(node["outputs"])
    review = out.get("importReview") or {}
    if not review.get("confirmation"):
        raise ValueError("请先确认制作方案，再生成候选视频")
    data = out.get("teachingVideo") or {}
    scenes = data.get("scenes") or []
    parent_id = payload.get("revisionOf")
    patch = payload.get("patch") or {}
    if parent_id:
        parents = db.query("video_candidates", "id=?", (parent_id,))
        if not parents or parents[0]["status"] != "pending_acceptance":
            raise ValueError("原候选不存在或当前不可送修")
        if not patch.get("sceneId") or not str(patch.get("comment") or "").strip():
            raise ValueError("送修必须指定场景并填写修改意见")
        if not any(scene["sceneId"] == patch["sceneId"] for scene in scenes):
            raise ValueError("送修场景不存在")
        if not patch.get("narration") and not patch.get("targetSeconds") and not patch.get("selectedRoute"):
            raise ValueError("送修至少修改口播、时长或制作路线中的一项")
        scenes = [dict(scene) for scene in scenes]
        for scene in scenes:
            if scene["sceneId"] == patch["sceneId"]:
                if patch.get("narration") is not None:
                    scene["narration"] = str(patch["narration"]).strip()
                if patch.get("targetSeconds") is not None:
                    seconds = patch["targetSeconds"]
                    if not isinstance(seconds, int) or not 1 <= seconds <= 300:
                        raise ValueError("送修时长应为 1–300 秒")
                    scene["targetSeconds"] = seconds
                if patch.get("selectedRoute"):
                    scene["route"] = patch["selectedRoute"]
    candidate_id = db.new_id("candidate")
    version = len(db.query("video_candidates", "project_id=?", (project_id,))) + 1
    manifest = {
        "candidateId": candidate_id,
        "importTaskId": review["importTaskId"],
        "packageHash": review["packageHash"],
        "parentId": parent_id,
        "version": version,
        "sceneIds": [scene["sceneId"] for scene in scenes],
        "patch": ({key: patch.get(key) for key in ("narration", "targetSeconds", "selectedRoute") if patch.get(key) is not None}
                  if parent_id else None),
        "sceneVersions": [{"sceneId": scene["sceneId"], "contentHash": _scene_hash(scene),
                           "route": scene["route"], "status": "queued"} for scene in scenes],
        "paidGenerationStarted": False,
        "status": "running",
    }
    db.insert("video_candidates", {
        "id": candidate_id, "project_id": project_id, "import_task_id": review["importTaskId"],
        "package_hash": review["packageHash"], "parent_id": parent_id, "version": version,
        "status": "running", "manifest": json.dumps(manifest, ensure_ascii=False), "created_at": db.now(),
    })
    if parent_id:
        db.insert("video_revision_requests", {
            "id": db.new_id("revision"), "candidate_id": parent_id, "scene_id": patch["sceneId"],
            "comment": str(patch["comment"]).strip(), "status": "queued",
            "new_candidate_id": candidate_id, "created_at": db.now(),
        })
    out["production"] = {"candidateId": candidate_id, "status": "running", "version": version}
    db.update("canvas_nodes", node["id"], {"outputs": json.dumps(out, ensure_ascii=False), "updated_at": db.now()})
    return {"candidateId": candidate_id, "status": "running", "version": version,
            "paidGenerationStarted": False}


def run(project_id: str, candidate_id: str) -> None:
    rows = db.query("video_candidates", "id=?", (candidate_id,))
    if not rows:
        return
    candidate = rows[0]
    manifest = db.jloads(candidate["manifest"])
    node = db.query("canvas_nodes", "project_id=? AND type='storyboard'", (project_id,))[0]
    out = db.jloads(node["outputs"])
    scenes = (out.get("teachingVideo") or {}).get("scenes") or []
    if manifest.get("parentId"):
        revision = db.query("video_revision_requests", "new_candidate_id=?", (candidate_id,))[0]
        patch = manifest.get("patch") or {}
        for scene in scenes:
            if scene["sceneId"] == revision["scene_id"]:
                if "narration" in patch:
                    scene["narration"] = patch["narration"]
                if "targetSeconds" in patch:
                    scene["targetSeconds"] = patch["targetSeconds"]
                if "selectedRoute" in patch:
                    scene["route"] = patch["selectedRoute"]
    produced = []
    scene_versions = []
    missing = []
    try:
        for scene in scenes:
            scene_id = scene["sceneId"]
            try:
                asset_db_id, filename = _scene_video(project_id, node["id"], candidate_id, scene)
                produced.append(filename)
                scene_versions.append({"sceneId": scene_id, "contentHash": _scene_hash(scene),
                                       "route": scene["route"], "status": "produced", "assetId": asset_db_id})
            except ValueError as exc:
                missing.append({"sceneId": scene_id, "reason": str(exc)})
                scene_versions.append({"sceneId": scene_id, "contentHash": _scene_hash(scene),
                                       "route": scene["route"], "status": "waiting_material", "reason": str(exc)})
        if not produced:
            raise ValueError("没有可生成的场景，请先补齐素材")
        candidate_asset_id = db.new_id("asset")
        output = ASSETS_DIR / f"{candidate_asset_id}.mp4"
        if len(produced) == 1:
            shutil.copyfile(ASSETS_DIR / produced[0], output)
        else:
            list_file = ASSETS_DIR / f"{candidate_id}-concat.txt"
            list_file.write_text("".join(f"file '{(ASSETS_DIR / name).as_posix()}'\n" for name in produced), encoding="utf-8")
            _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(output)])
            list_file.unlink(missing_ok=True)
        _asset(project_id, node["id"], "video", output.name,
               {"candidateId": candidate_id, "role": "candidate_video", "rights": "own", "syntheticFixture": True})
        subtitle_id = _subtitle(project_id, node["id"], candidate_id, scenes)
        unchanged = []
        if manifest.get("parentId"):
            parent = db.query("video_candidates", "id=?", (manifest["parentId"],))[0]
            old_hashes = {x["sceneId"]: x["contentHash"] for x in db.jloads(parent["manifest"]).get("sceneVersions") or []}
            unchanged = [x["sceneId"] for x in scene_versions if old_hashes.get(x["sceneId"]) == x["contentHash"]]
        manifest.update({
            "status": "pending_acceptance", "videoAssetId": candidate_asset_id,
            "videoUrl": f"/assets/{output.name}", "subtitleAssetId": subtitle_id,
            "sceneVersions": scene_versions, "missingEvidence": missing,
            "unchangedSceneIds": unchanged, "finishedAt": db.now(),
        })
        with db._conn() as conn:
            conn.execute("UPDATE video_candidates SET status='pending_acceptance',video_asset_id=?,manifest=?,finished_at=? WHERE id=?",
                         (candidate_asset_id, json.dumps(manifest, ensure_ascii=False), manifest["finishedAt"], candidate_id))
            conn.execute("UPDATE video_revision_requests SET status='candidate_created' WHERE new_candidate_id=?", (candidate_id,))
        out["production"] = manifest
        db.update("canvas_nodes", node["id"], {"outputs": json.dumps(out, ensure_ascii=False), "updated_at": db.now()})
        db.insert("model_tasks", {
            "id": db.new_id("task"), "project_id": project_id, "node_id": node["id"],
            "provider": "local", "model": "deterministic-2d-and-existing-assets", "task_type": "teaching_video_compose",
            "status": "succeeded", "estimated_cost_cny": 0, "created_at": db.now(), "finished_at": db.now(),
        })
    except Exception as exc:
        manifest.update({"status": "failed", "error": str(exc), "sceneVersions": scene_versions,
                         "missingEvidence": missing})
        with db._conn() as conn:
            conn.execute("UPDATE video_candidates SET status='failed',manifest=?,finished_at=? WHERE id=?",
                         (json.dumps(manifest, ensure_ascii=False), db.now(), candidate_id))
        out["production"] = manifest
        db.update("canvas_nodes", node["id"], {"outputs": json.dumps(out, ensure_ascii=False), "updated_at": db.now()})
