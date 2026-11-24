"""真实 FFmpeg 候选视频与指定场景送修测试。"""
import gc
import io
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

os.environ.setdefault("ARK_API_KEY", "test-dummy-key")
os.environ.setdefault("BJMOMA_API_KEY", "test-dummy-key")
os.environ.pop("ADMIN_USER", None)
os.environ.pop("ADMIN_PASS", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from fastapi.testclient import TestClient

from app import db, teaching_video_import, teaching_video_produce
from app.main import app

PASS, FAIL = [], []
FIXTURE = Path(__file__).parent / "fixtures" / "teaching-video"


def check(name, ok):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}")


def package():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.write(FIXTURE / "manifest.json", "manifest.json")
        for asset in json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))["assets"]:
            zf.write(FIXTURE / asset["uri"], asset["uri"])
    return buf.getvalue()


def main():
    with tempfile.TemporaryDirectory() as tmp:
        db.DB_PATH = str(Path(tmp) / "test.db")
        teaching_video_import.ASSETS_DIR = Path(tmp) / "assets"
        teaching_video_produce.ASSETS_DIR = teaching_video_import.ASSETS_DIR
        teaching_video_produce.ASSETS_DIR.mkdir()
        db.init_db()
        client = TestClient(app)
        content = package()
        result = client.post("/api/projects/import/teaching-video", files={"file": ("pwm.zip", content, "application/zip")}).json()
        pid = result["project_id"]
        confirmation = client.post(f"/api/projects/{pid}/teaching-video/confirm", json={
            "importTaskId": result["importTaskId"], "packageHash": result["packageHash"],
            "sceneIds": [s["sceneId"] for s in json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))["scenes"]],
            "generativeMediaAuthorized": False, "paidGenerationApproved": False,
        })
        check("方案确认后可生产", confirmation.status_code == 200)
        started = teaching_video_produce.start(pid, {})
        teaching_video_produce.run(pid, started["candidateId"])
        first = db.query("video_candidates", "id=?", (started["candidateId"],))[0]
        manifest = json.loads(first["manifest"])
        check("首版候选待验收", first["status"] == "pending_acceptance")
        video = teaching_video_import.ASSETS_DIR / f"{manifest['videoAssetId']}.mp4"
        check("真实 MP4 已生成", video.exists() and video.stat().st_size > 1000)
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
                               capture_output=True, text=True)
        check("视频可由 ffprobe 读取", probe.returncode == 0 and float(probe.stdout.strip()) > 0)
        check("字幕已生成", (teaching_video_import.ASSETS_DIR / f"{manifest['subtitleAssetId']}.srt").exists())
        states = {x["sceneId"]: x["status"] for x in manifest["sceneVersions"]}
        check("波形与真实素材已生成", states["scene-pwm-waveform"] == "produced" and states["scene-led-demo"] == "produced")
        check("教师缺件保留待补", states["scene-opening"] == "waiting_material" and states["scene-summary"] == "waiting_material")
        check("无付费调用", all(x["provider"] == "local" and x["estimated_cost_cny"] == 0 for x in db.query("model_tasks")))

        revised = teaching_video_produce.start(pid, {
            "revisionOf": started["candidateId"],
            "patch": {"sceneId": "scene-pwm-waveform", "comment": "缩短波形并修改口播",
                      "targetSeconds": 15, "selectedRoute": "diagram_2d", "narration": "修改后的占空比讲解"},
        })
        teaching_video_produce.run(pid, revised["candidateId"])
        second = db.query("video_candidates", "id=?", (revised["candidateId"],))[0]
        revised_manifest = json.loads(second["manifest"])
        check("送修产生第二版", revised_manifest["version"] == 2 and second["status"] == "pending_acceptance")
        check("仅指定场景哈希改变", "scene-pwm-waveform" not in revised_manifest["unchangedSceneIds"]
              and "scene-led-demo" in revised_manifest["unchangedSceneIds"])
        revision = db.query("video_revision_requests", "new_candidate_id=?", (revised["candidateId"],))[0]
        check("送修原因和版本可回查", revision["scene_id"] == "scene-pwm-waveform"
              and revision["status"] == "candidate_created")
        check("送修视频实际存在", (teaching_video_import.ASSETS_DIR / f"{revised_manifest['videoAssetId']}.mp4").exists())
        check("旧候选仍可回读", db.query("video_candidates", "id=?", (started["candidateId"],))[0]["status"] == "pending_acceptance")
        client.close()
        gc.collect()


if __name__ == "__main__":
    main()
    print(f"\n通过 {len(PASS)}/{len(PASS) + len(FAIL)}")
    sys.exit(1 if FAIL else 0)
