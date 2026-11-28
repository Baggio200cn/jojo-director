"""项目成本：真实免费、可估价与缺失价格配置的区分测试。"""
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
from app import auth, db
from app.main import app

PASS, FAIL = [], []


def check(name, ok):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        db.DB_PATH = str(Path(tmp) / "test.db")
        db.init_db()
        auth.init()
        import app.main as main_module
        main_module._migrate()
        client = TestClient(app)
        project = client.post("/api/projects", json={"title": "成本测试"}).json()
        db.insert("model_tasks", {
            "id": "local-compose", "project_id": project["id"], "node_id": "n1",
            "provider": "local", "model": "deterministic-compose", "task_type": "teaching_video_compose",
            "status": "succeeded", "estimated_cost_cny": 0, "created_at": db.now(), "finished_at": db.now(),
        })
        local = client.get(f"/api/projects/{project['id']}/stats").json()
        check("本地合成明确免费", local["total_cost_cny"] == 0 and not local["has_unpriced_generation"])
        db.insert("model_tasks", {
            "id": "ark-image-unknown", "project_id": project["id"], "node_id": "n2",
            "provider": "ark", "model": "ep-m-unknown-price", "task_type": "image_generation",
            "status": "succeeded", "estimated_cost_cny": 0, "created_at": db.now(), "finished_at": db.now(),
        })
        unknown = client.get(f"/api/projects/{project['id']}/stats").json()
        check("缺失 EP 价格时标记待核算", unknown["has_unpriced_generation"]
              and unknown["cost_status"] == "uncalculated"
              and unknown["unpriced_tasks"][0]["model"] == "ep-m-unknown-price")
        db.insert("model_tasks", {
            "id": "ark-image-priced", "project_id": project["id"], "node_id": "n3",
            "provider": "ark", "model": "doubao-seedream-5-0-pro-260628", "task_type": "image_generation",
            "status": "succeeded", "estimated_cost_cny": 0, "created_at": db.now(), "finished_at": db.now(),
        })
        priced = client.get(f"/api/projects/{project['id']}/stats").json()
        check("已配置价格可独立估算", priced["total_cost_cny"] == 0.46
              and priced["cost_status"] == "partial" and priced["has_unpriced_generation"])
        client.close()
        import gc
        gc.collect()


if __name__ == "__main__":
    main()
    print(f"\n通过 {len(PASS)}/{len(PASS) + len(FAIL)}")
    sys.exit(1 if FAIL else 0)
