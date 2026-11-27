"""文件夹上传、本地分析、归类与去重测试。"""
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
from app import asset_ingest, db
from app.main import app

PASS, FAIL = [], []


def check(name, ok):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        db.DB_PATH = str(Path(tmp) / "test.db")
        asset_ingest.ASSETS_DIR = Path(tmp) / "assets"
        asset_ingest.ASSETS_DIR.mkdir()
        db.init_db()
        client = TestClient(app)
        files = [
            ("files", ("lens.md", "透镜成像与光路-v1".encode("utf-8"), "text/markdown")),
            ("files", ("pwm.txt", "PWM 占空比与 LED 调光-v1".encode("utf-8"), "text/plain")),
            ("files", ("pwm-copy.txt", "PWM 占空比与 LED 调光-v1".encode("utf-8"), "text/plain")),
            ("files", ("broken.pdf", b"not-a-pdf", "application/pdf")),
            ("files", ("escape.txt", b"invalid path", "text/plain")),
        ]
        data = {
            "paths": ["光学实验/透镜-v1.md", "电子实验/pwm-v1.txt", "电子实验/pwm-copy.txt",
                      "电子实验/broken.pdf", "../escape.txt"],
            "rights": "own", "folder": "__auto__", "note": "首轮合作素材",
        }
        response = client.post("/api/assets/upload_folder", files=files, data=data)
        if response.status_code != 200:
            print(response.text)
        check("文件夹接口返回 200", response.status_code == 200)
        result = response.json()
        check("接收、拒绝、重复数量正确", result["summary"] == {"accepted": 2, "rejected": 2, "duplicate": 1})
        check("不调用付费模型", result["paidModelCalled"] is False and not db.query("model_tasks"))
        check("全部结果待审核", result["reviewRequired"] is True)
        accepted = {x["path"]: x for x in result["results"] if x["status"] == "accepted"}
        check("光学内容正确归类", accepted["光学实验/透镜-v1.md"]["folder"] == "光学类/光学实验")
        check("PWM 文本内容正确归类", accepted["电子实验/pwm-v1.txt"]["folder"] == "电气与电子/电子实验")
        check("AI 独立大类自动归类", asset_ingest.classify("AI实验/神经网络.md", "", "")["suggestedDomain"] == "人工智能（AI）")
        check("AI 缩写不误伤普通英文", asset_ingest.classify("detail.md", "", "")["suggestedDomain"] == "待确认")
        check("保留相对路径与哈希", all(x["analysis"]["reviewRequired"] for x in accepted.values())
              and all(x["analysis"]["method"] == "local_rules_v1" for x in accepted.values()))
        rows = db.query("assets")
        check("实际仅保存两个文件", len(rows) == 2
              and len(list(asset_ingest.ASSETS_DIR.iterdir())) == 2)
        check("原路径与内容哈希入库", all(db.jloads(x["meta"]).get("relativePath")
              and db.jloads(x["meta"]).get("contentHash") for x in rows))
        check("损坏 PDF 被明确拒绝", any(x["status"] == "rejected" and "签名" in x["reason"]
              for x in result["results"]))
        check("路径穿越被明确拒绝", any(x["status"] == "rejected" and "路径" in x["reason"]
              for x in result["results"]))
        mismatch = client.post("/api/assets/upload_folder", files=[files[0]],
                               data={"paths": ["a.md", "b.md"]})
        check("文件与路径数量不匹配被拒绝", mismatch.status_code == 400)

        batch_id = result["batchId"]
        moved = client.post(f"/api/assets/ingestion-batches/{batch_id}/move",
                            json={"folder": "人工智能（AI）/CWC"})
        check("整批整理到已有目录并持久化", moved.status_code == 200
              and moved.json()["movedCount"] == 2
              and all(x["folder"] == "人工智能（AI）/CWC" for x in moved.json()["results"] if x["status"] == "accepted")
              and all(x["folder"] == "人工智能（AI）/CWC" for x in db.query("assets")))
        persisted = client.get("/api/assets/ingestion-batches")
        check("批次与结果已持久化", persisted.status_code == 200
              and persisted.json()[0]["id"] == batch_id
              and persisted.json()[0]["status"] == "analyzed_pending_teacher")
        check("空修改意见被拒绝", client.post(f"/api/assets/ingestion-batches/{batch_id}/review",
              json={"action": "needs_revision", "comment": ""}).status_code == 400)
        revised = client.post(f"/api/assets/ingestion-batches/{batch_id}/review",
                              json={"action": "needs_revision", "comment": "调整 PWM 目录并补充教材出处"})
        check("教师修改意见已记录", revised.status_code == 200
              and revised.json()["status"] == "teacher_changes_requested")
        archived = client.post(f"/api/assets/ingestion-batches/{batch_id}/review",
                               json={"action": "confirm_archive", "comment": "教师侧核对完成"})
        check("教师确认后暂时归档", archived.status_code == 200
              and archived.json()["status"] == "teacher_archived")
        aligned = client.post(f"/api/assets/ingestion-batches/{batch_id}/review",
                              json={"action": "admin_alignment", "comment": "管理员与技术团队核对目录"})
        check("管理员对齐完成", aligned.status_code == 200
              and aligned.json()["status"] == "admin_alignment"
              and len(aligned.json()["reviews"]) == 3)
        check("对齐不自动批准投产", aligned.json()["summary"]["accepted"] == 2
              and not db.query("model_tasks"))
        check("未知批次审核被拒绝", client.post("/api/assets/ingestion-batches/unknown/review",
              json={"action": "confirm_archive", "comment": ""}).status_code == 400)
        client.close()
        import gc
        gc.collect()


if __name__ == "__main__":
    main()
    print(f"\n通过 {len(PASS)}/{len(PASS) + len(FAIL)}")
    sys.exit(1 if FAIL else 0)
