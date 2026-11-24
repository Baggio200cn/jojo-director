"""教学视频任务包 v1 的导入审核与幂等离线测试。"""
import gc
import hashlib
import io
import json
import os
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

from app import db, teaching_video_import
from app.main import app

PASS, FAIL = [], []
FIXTURE = Path(__file__).parent / "fixtures" / "teaching-video"


def check(name, ok):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}")


def manifest():
    return json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))


def make_zip(doc=None, tamper=False):
    doc = doc or manifest()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(doc, ensure_ascii=False))
        for asset in doc["assets"]:
            content = (FIXTURE / asset["uri"]).read_bytes()
            if tamper and asset["assetId"] == "slide-4":
                content += b"tampered"
            zf.writestr(asset["uri"], content)
    return buf.getvalue()


def main():
    with tempfile.TemporaryDirectory() as tmp:
        db.DB_PATH = str(Path(tmp) / "test.db")
        teaching_video_import.ASSETS_DIR = Path(tmp) / "assets"
        teaching_video_import.ASSETS_DIR.mkdir()
        db.init_db()
        client = TestClient(app)
        content = make_zip()
        r = client.post("/api/projects/import/teaching-video", files={"file": ("pwm.zip", content, "application/zip")})
        check("任务包导入返回 200", r.status_code == 200)
        if r.status_code != 200:
            print(r.text)
            client.close()
            return
        result = r.json()
        pid = result["project_id"]
        check("回执含导入任务和整包哈希", result["importTaskId"].startswith("import_")
              and result["packageHash"] == "sha256:" + hashlib.sha256(content).hexdigest())
        check("四场景审核完成", len(result["scenePlans"]) == 4 and result["status"] == "received")
        check("波形走确定性 2D", next(x for x in result["scenePlans"]
              if x["sceneId"] == "scene-pwm-waveform")["selectedRoute"] == "diagram_2d")
        check("调光走真实素材路线", next(x for x in result["scenePlans"]
              if x["sceneId"] == "scene-led-demo")["selectedRoute"] == "live_action")
        check("导入不发起付费任务", result["paidGenerationStarted"] is False and db.query("model_tasks") == [])
        check("素材实际落盘", len(list(teaching_video_import.ASSETS_DIR.iterdir())) == 3)
        graph = client.get(f"/api/projects/{pid}/graph").json()
        storyboard = next(n for n in graph["nodes"] if n["type"] == "storyboard")
        check("画布可读取场景工程", len(storyboard["outputs"]["teachingVideo"]["scenes"]) == 4)
        project = db.get("projects", pid)
        source = json.loads(project["source_manifest"])
        check("独立上游版本保留", source["sourceVersions"]["blueprint"] == "blueprint-v7"
              and source["sourceVersions"]["lecture"] == "lecture-v6")
        check("契约样例有明确标记", result["fixtureOnly"] is True)

        confirmation = client.post(f"/api/projects/{pid}/teaching-video/confirm", json={
            "importTaskId": result["importTaskId"], "packageHash": result["packageHash"],
            "sceneIds": [s["sceneId"] for s in manifest()["scenes"]],
            "generativeMediaAuthorized": False, "paidGenerationApproved": False,
        })
        check("方案确认返回 plan_approved", confirmation.status_code == 200
              and confirmation.json()["status"] == "plan_approved")
        check("确认明确无付费生成", confirmation.json()["paidGeneration"] == "not_required"
              and confirmation.json()["generativeMedia"] == "not_authorized")
        confirmed_again = client.post(f"/api/projects/{pid}/teaching-video/confirm", json={
            "importTaskId": result["importTaskId"], "packageHash": result["packageHash"],
            "sceneIds": [s["sceneId"] for s in manifest()["scenes"]],
            "generativeMediaAuthorized": False, "paidGenerationApproved": False,
        })
        check("相同方案确认幂等", confirmed_again.json()["reused"] is True)
        wrong_hash = client.post(f"/api/projects/{pid}/teaching-video/confirm", json={
            "importTaskId": result["importTaskId"], "packageHash": "sha256:" + "0" * 64,
            "sceneIds": [s["sceneId"] for s in manifest()["scenes"]],
            "generativeMediaAuthorized": False, "paidGenerationApproved": False,
        })
        check("错误包哈希不能确认", wrong_hash.status_code == 400)
        paid = client.post(f"/api/projects/{pid}/teaching-video/confirm", json={
            "importTaskId": result["importTaskId"], "packageHash": result["packageHash"],
            "sceneIds": [s["sceneId"] for s in manifest()["scenes"]],
            "generativeMediaAuthorized": False, "paidGenerationApproved": True,
        })
        check("首版付费批准被明确拒绝", paid.status_code == 400)

        again = client.post("/api/projects/import/teaching-video", files={"file": ("pwm.zip", content, "application/zip")})
        check("同包重复导入返回原任务", again.status_code == 200 and again.json()["reused"] is True
              and again.json()["project_id"] == pid)
        check("重复导入不重复建项目和素材", len(db.query("projects")) == 1
              and len(db.query("import_tasks")) == 1
              and len(list(teaching_video_import.ASSETS_DIR.iterdir())) == 3)

        changed = manifest()
        changed["teachingGoal"] = "相同版本号但内容已经变化"
        conflict = client.post("/api/projects/import/teaching-video", files={"file": ("changed.zip", make_zip(changed), "application/zip")})
        check("同版本不同哈希被拒绝", conflict.status_code == 400)
        tampered = client.post("/api/projects/import/teaching-video", files={"file": ("tampered.zip", make_zip(tamper=True), "application/zip")})
        check("素材哈希篡改被拒绝", tampered.status_code == 400)

        required = manifest()
        required["packageVersion"] = "pkg-v2"
        required["missingEvidence"][0]["required"] = True
        needs = client.post("/api/projects/import/teaching-video", files={"file": ("required.zip", make_zip(required), "application/zip")})
        check("必需缺项进入 needs_material", needs.status_code == 200 and needs.json()["status"] == "needs_material")
        blocked = client.post(f"/api/projects/{needs.json()['project_id']}/teaching-video/confirm", json={
            "importTaskId": needs.json()["importTaskId"], "packageHash": needs.json()["packageHash"],
            "sceneIds": [s["sceneId"] for s in required["scenes"]],
            "generativeMediaAuthorized": False, "paidGenerationApproved": False,
        })
        check("必需缺项不能批准制作", blocked.status_code == 400)
        bad_ref = manifest()
        bad_ref["packageVersion"] = "pkg-v3"
        bad_ref["scenes"][0]["evidenceRefs"] = ["missing-asset"]
        rejected = client.post("/api/projects/import/teaching-video", files={"file": ("bad-ref.zip", make_zip(bad_ref), "application/zip")})
        check("不存在的场景证据引用被拒绝", rejected.status_code == 400)
        check("失败样例不触发模型调用", db.query("model_tasks") == [])
        client.close()
        gc.collect()


if __name__ == "__main__":
    main()
    print(f"\n通过 {len(PASS)}/{len(PASS) + len(FAIL)}")
    sys.exit(1 if FAIL else 0)
