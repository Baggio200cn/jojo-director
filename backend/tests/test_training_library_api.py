"""训练库浏览、筛选数据与追加式人工抽检 API 测试。"""
import gc
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
from app import db
from app.main import app

PASS, FAIL = [], []


def check(name, ok):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        db.DB_PATH = str(Path(tmp) / "test.db")
        db.init_db()
        client = TestClient(app)
        response = client.get("/api/training/library")
        check("训练库 API 可读取", response.status_code == 200)
        data = response.json()
        check("目录与候选数量正确", len(data["taxonomy"]["levels"]) == 9
              and len(data["skills"]) == 3 and len(data["discoveries"]) == 100)
        check("候选均未批准投产", data["stats"]["approvedSkills"] == 0
              and all(x["manifest"]["status"] == "candidate" for x in data["skills"]))
        check("外部发现不等于科学真值", data["governance"]["discoveryIsScientificTruth"] is False
              and all(x["evidenceStatus"] == "discovery_only" for x in data["discoveries"]))

        skill = data["skills"][0]
        body = {
            "objectType": "skill", "objectId": skill["objectId"],
            "contentHash": skill["contentHash"], "reviewer": "测试教研员",
            "decision": "sample_passed", "comment": "完成首轮人工抽检",
            "checks": ["source_checked", "rights_checked", "scope_checked"],
        }
        check("缺检查项不能抽检通过", client.post("/api/training/reviews", json={
            **body, "checks": ["source_checked"]}).status_code == 400)
        check("内容哈希不符不能审核", client.post("/api/training/reviews", json={
            **body, "contentHash": "0" * 64}).status_code == 400)
        check("未知对象不能审核", client.post("/api/training/reviews", json={
            **body, "objectId": "UNKNOWN"}).status_code == 400)
        check("退回补证须写原因", client.post("/api/training/reviews", json={
            **body, "decision": "needs_evidence", "comment": ""}).status_code == 400)

        created = client.post("/api/training/reviews", json=body)
        check("合法抽检记录保存成功", created.status_code == 200
              and created.json()["productionStatus"] == "candidate")
        refreshed = client.get("/api/training/library").json()
        reviewed = next(x for x in refreshed["skills"] if x["objectId"] == skill["objectId"])
        check("刷新后可回查审核记录", reviewed["latestReview"]["reviewer"] == "测试教研员"
              and len(reviewed["reviews"]) == 1)
        check("抽检不修改候选投产状态", reviewed["manifest"]["status"] == "candidate"
              and refreshed["stats"]["approvedSkills"] == 0)

        discovery = data["discoveries"][0]
        external = client.post("/api/training/reviews", json={
            **body, "objectType": "discovery", "objectId": discovery["objectId"],
            "contentHash": discovery["contentHash"], "decision": "needs_evidence",
            "comment": "只有外部摘要，需补充教材或原始论文证据",
        })
        check("外部发现可退回补证", external.status_code == 200
              and external.json()["productionStatus"] == "reference_only")
        check("审核历史采用追加记录", len(db.query("training_reviews")) == 2)
        client.close()
        gc.collect()


if __name__ == "__main__":
    main()
    print(f"\n通过 {len(PASS)}/{len(PASS) + len(FAIL)}")
    sys.exit(1 if FAIL else 0)
