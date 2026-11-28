"""讨论截图上传、按需分析与评论附件测试。"""
import io
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("ARK_API_KEY", "test-dummy-key")
os.environ.setdefault("BJMOMA_API_KEY", "test-dummy-key")
os.environ.pop("ADMIN_USER", None)
os.environ.pop("ADMIN_PASS", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from fastapi.testclient import TestClient
from app import auth, db, screenshots
from app.main import app

PASS, FAIL = [], []
CALLS = []


def check(name, ok):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}")


async def fake_vision(route_config, messages):
    CALLS.append(messages)
    return {"text": "1. 截图显示上传预检错误。\n2. 文件数量超过限制。\n3. 建议按课次拆分。",
            "reported_model": "test-vision", "input_tokens": 10, "output_tokens": 20}


def main():
    with tempfile.TemporaryDirectory() as tmp:
        db.DB_PATH = str(Path(tmp) / "test.db")
        screenshots.SCREENSHOTS_DIR = Path(tmp) / "screenshots"
        screenshots.vision_chat = fake_vision
        db.init_db()
        auth.init()
        client = TestClient(app)
        community = client.post("/api/communities", json={"title": "截图讨论测试"}).json()
        contribution = client.post(f"/api/communities/{community['id']}/contributions",
                                   json={"kind": "question", "title": "上传问题", "body": "请分析"}).json()
        uploaded = client.post(f"/api/communities/{community['id']}/contributions/{contribution['id']}/screenshots",
                               files={"file": ("error.png", b"\x89PNG\r\n\x1a\nimage-data", "image/png")})
        check("截图上传成功且未调用模型", uploaded.status_code == 200 and not CALLS
              and uploaded.json()["analysis_status"] == "uploaded")
        screenshot_id = uploaded.json()["id"]
        invalid = client.post(f"/api/communities/{community['id']}/contributions/{contribution['id']}/screenshots",
                              files={"file": ("bad.png", b"not-png", "image/png")})
        check("损坏截图被拒绝", invalid.status_code == 400)
        analyzed = client.post(f"/api/communities/screenshots/{screenshot_id}/analyze",
                               json={"note": "请判断为什么不能上传"})
        check("点击后完成截图分析", analyzed.status_code == 200 and len(CALLS) == 1
              and analyzed.json()["analysis_status"] == "succeeded"
              and "拆分" in analyzed.json()["analysis_text"])
        check("分析任务与模型已记录", analyzed.json()["analysis_model"] == "test-vision"
              and len(db.query("model_tasks", "task_type='screenshot_analysis'")) == 1)
        comment = client.post(f"/api/communities/{community['id']}/contributions/{contribution['id']}/comments",
                              json={"body": "这是上传失败时的截图", "parent_id": "",
                                    "screenshot_ids": [screenshot_id]})
        check("截图与文字可一起发表评论", comment.status_code == 200)
        detail = client.get(f"/api/communities/{community['id']}/contributions/{contribution['id']}")
        check("评论详情包含截图和分析", detail.status_code == 200
              and detail.json()["comments"][0]["screenshots"][0]["id"] == screenshot_id
              and detail.json()["comments"][0]["screenshots"][0]["analysis_text"])
        image = client.get(f"/api/communities/screenshots/{screenshot_id}/image")
        check("截图通过权限接口读取", image.status_code == 200
              and image.content.startswith(b"\x89PNG"))
        reused = client.post(f"/api/communities/{community['id']}/contributions/{contribution['id']}/comments",
                             json={"body": "重复使用", "parent_id": "", "screenshot_ids": [screenshot_id]})
        check("同一截图不能重复绑定其他评论", reused.status_code == 400)
        client.close()
        import gc
        gc.collect()


if __name__ == "__main__":
    main()
    print(f"\n通过 {len(PASS)}/{len(PASS) + len(FAIL)}")
    sys.exit(1 if FAIL else 0)
