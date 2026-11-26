"""课题社区共建闭环测试。"""
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
from app import asset_ingest, auth, community, db
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
        import app.main as main_module
        main_module.ASSETS_DIR = asset_ingest.ASSETS_DIR
        db.init_db()
        auth.init()
        client = TestClient(app)

        created = client.post("/api/communities", json={
            "title": "CWC能力地图课程重构", "domain": "人工智能（AI）",
            "description": "共同复核课程讲稿与知识点", "goals": "形成可生产的课题版本",
        })
        check("管理员创建课题社区", created.status_code == 200
              and created.json()["member_count"] == 1)
        community_id = created.json()["id"]
        detail = client.get(f"/api/communities/{community_id}")
        check("社区详情与主持人身份", detail.status_code == 200
              and detail.json()["community"]["my_role"] == "host")

        contribution = client.post(f"/api/communities/{community_id}/contributions", json={
            "kind": "question", "title": "L01 讲稿知识点复核", "body": "请核对能力地图与讲稿是否一致",
        })
        check("教师可发起课题讨论", contribution.status_code == 200
              and contribution.json()["status"] == "under_discussion")
        contribution_id = contribution.json()["id"]
        comment = client.post(f"/api/communities/{community_id}/contributions/{contribution_id}/comments",
                              json={"body": "建议先补充能力地图出处", "parent_id": ""})
        check("贡献下可回复讨论", comment.status_code == 200)
        review = client.post(f"/api/communities/{community_id}/contributions/{contribution_id}/review",
                             json={"decision": "approved", "comment": "主持人审校通过"})
        check("学科审校形成决定", review.status_code == 200
              and review.json()["contribution"]["status"] == "approved_for_production"
              and len(review.json()["reviews"]) == 1)
        revised = client.post(f"/api/communities/{community_id}/contributions/{contribution_id}/revisions",
                              json={"body": "已补充出处与对应讲稿", "note": "按讨论修改"})
        check("贡献可形成新版本", revised.status_code == 200
              and revised.json()["contribution"]["version"] == 2
              and revised.json()["contribution"]["status"] == "under_discussion")
        client.post(f"/api/communities/{community_id}/contributions/{contribution_id}/review",
                    json={"decision": "approved", "comment": "新版通过"})

        task = client.post(f"/api/communities/{community_id}/tasks", json={
            "title": "复核 L02 讲稿", "description": "补充出处并标记争议点", "kind": "review",
        })
        task_id = task.json()["id"]
        check("主持人创建共建任务", task.status_code == 200 and task.json()["status"] == "open")
        claimed = client.post(f"/api/communities/{community_id}/tasks/{task_id}/claim")
        completed = client.post(f"/api/communities/{community_id}/tasks/{task_id}/complete")
        done = client.post(f"/api/communities/{community_id}/tasks/{task_id}/done")
        check("任务认领、提交与验收", claimed.json()["status"] == "in_progress"
              and completed.json()["status"] == "review" and done.json()["status"] == "done")

        project = client.post("/api/projects", json={"title": "社区接驳测试项目"}).json()
        node = client.post(f"/api/projects/{project['id']}/nodes", json={
            "type": "script", "title": "L01 场景", "inputs": {}, "position": {"x": 0, "y": 0},
        }).json()
        bound = client.post(f"/api/communities/{community_id}/contributions/{contribution_id}/bind",
                            json={"project_id": project["id"], "node_id": node["id"]})
        check("审校贡献可接驳真实场景", bound.status_code == 200
              and bound.json()["contribution"]["status"] == "bound_to_scene"
              and db.get("canvas_nodes", node["id"])["inputs"].find(contribution_id) >= 0)
        release = client.post(f"/api/communities/{community_id}/versions", json={
            "title": "首轮共建版本", "summary": "完成 L01 讨论、审校与场景接驳",
        })
        check("发布课题版本并署名", release.status_code == 200
              and release.json()["version"] == 1 and contribution_id in release.json()["contribution_ids"])

        uploaded = client.post("/api/assets/upload", files={
            "file": ("lesson.png", b"\x89PNG\r\n\x1a\nL03", "image/png"),
        }, data={"folder": "人工智能（AI）/CWC", "rights": "own", "note": "首次提交",
                 "community_id": community_id})
        check("单文件上传直接形成贡献", uploaded.status_code == 200
              and uploaded.json().get("contributionId"))
        asset_contribution_id = uploaded.json()["contributionId"]
        new_version = client.post("/api/assets/upload", files={
            "file": ("lesson-v2.png", b"\x89PNG\r\n\x1a\nL03V2", "image/png"),
        }, data={"folder": "人工智能（AI）/CWC", "rights": "own", "note": "根据讨论修订",
                 "community_id": community_id, "contribution_id": asset_contribution_id})
        check("文件贡献可上传新版本", new_version.status_code == 200
              and new_version.json().get("contributionVersion") == 2)

        folder = client.post("/api/assets/upload_folder", files=[
            ("files", ("l04.md", "L04 机器学习讲稿".encode("utf-8"), "text/markdown")),
        ], data={"paths": ["CWC/L04.md"], "folder": "__auto__", "rights": "own",
                 "note": "文件夹共建", "community_id": community_id})
        check("文件夹上传逐项形成贡献", folder.status_code == 200
              and folder.json()["communityId"] == community_id
              and folder.json()["results"][0].get("contributionId"))
        imported = client.post(f"/api/communities/{community_id}/import-batch",
                               json={"batch_id": folder.json()["batchId"]})
        check("历史批次导入不会重复建贡献", imported.status_code == 200
              and imported.json()[0]["id"] == folder.json()["results"][0]["contributionId"])

        db.insert("invite_codes", {"id": "inv_test", "code": "TEST-TEACHER", "label": "医学审校老师",
                                   "daily_video_limit": 3, "daily_cost_limit_cny": 10,
                                   "disabled": 0, "created_at": db.now()})
        teacher = {"role": "invite", "invite_code": "TEST-TEACHER"}
        community.join_community(community_id, teacher)
        teacher_comment = community.add_comment(community_id, contribution_id, {
            "body": "医学部分已复核", "parent_id": "",
        }, teacher)
        check("邀请教师拥有稳定社区身份", teacher_comment["author_name"] == "医学审校老师"
              and teacher_comment["author_id"].startswith("invite_")
              and "TEST-TEACHER" not in str(teacher_comment))
        client.post(f"/api/communities/{community_id}/contributions/{contribution_id}/comments",
                    json={"body": "请医学审校老师继续确认下一版", "parent_id": teacher_comment["id"]})
        notices = community.community_detail(community_id, teacher)["notifications"]
        check("教师可收到社区通知", len(notices) > 0)
        check("通知已读", community.mark_notifications_read(community_id, teacher)["ok"] is True)
        client.close()
        import gc
        gc.collect()


if __name__ == "__main__":
    main()
    print(f"\n通过 {len(PASS)}/{len(PASS) + len(FAIL)}")
    sys.exit(1 if FAIL else 0)
