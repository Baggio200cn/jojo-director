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
        client.close()
        import gc
        gc.collect()


if __name__ == "__main__":
    main()
    print(f"\n通过 {len(PASS)}/{len(PASS) + len(FAIL)}")
    sys.exit(1 if FAIL else 0)
