"""公开仓库导入、只读快照与代码讨论测试。"""
import asyncio
import io
import os
import sys
import tempfile
from pathlib import Path

import httpx

os.environ.setdefault("ARK_API_KEY", "test-dummy-key")
os.environ.setdefault("BJMOMA_API_KEY", "test-dummy-key")
os.environ.pop("ADMIN_USER", None)
os.environ.pop("ADMIN_PASS", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from fastapi.testclient import TestClient
from app import auth, community, db, repository
from app.main import app

PASS, FAIL = [], []
SHA = "a" * 40


def check(name, ok):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}")


def handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url == "https://api.github.com/repos/Baggio200cn/jojo-director":
        return httpx.Response(200, json={"private": False, "default_branch": "main", "description": "JOJO"})
    if url.endswith("/branches/feat%2Fscience-training-library") or url.endswith("/branches/feat/science-training-library"):
        return httpx.Response(200, json={"commit": {"sha": SHA}})
    if f"/git/trees/{SHA}" in url:
        return httpx.Response(200, json={"truncated": False, "tree": [
            {"type": "blob", "path": "README.md", "size": 20, "sha": "r1"},
            {"type": "blob", "path": "backend/app/main.py", "size": 30, "sha": "m1"},
            {"type": "blob", "path": ".env", "size": 20, "sha": "s1"},
            {"type": "blob", "path": "node_modules/demo/index.js", "size": 20, "sha": "n1"},
            {"type": "blob", "path": "frontend/src/App.tsx", "size": 35, "sha": "a1"},
        ]})
    if "raw.githubusercontent.com" in url:
        if url.endswith("/README.md"):
            return httpx.Response(200, content="# JOJO Director\n公开仓库测试\n".encode())
        if url.endswith("/backend/app/main.py"):
            return httpx.Response(200, content=b"def main():\n    return 'JOJO'\n")
        if url.endswith("/frontend/src/App.tsx"):
            return httpx.Response(200, content=b"export default function App() { return null }\n")
    return httpx.Response(404, json={"message": "not found"})


def main():
    with tempfile.TemporaryDirectory() as tmp:
        db.DB_PATH = str(Path(tmp) / "test.db")
        repository.REPOSITORIES_DIR = Path(tmp) / "repositories"
        repository.github_client = repository.GitHubClient(transport=httpx.MockTransport(handler))
        db.init_db()
        auth.init()
        client = TestClient(app)
        created = client.post("/api/communities", json={"title": "代码共建测试", "domain": "人工智能（AI）"})
        community_id = created.json()["id"]
        inspected = client.post("/api/repositories/inspect", json={
            "source_url": "https://github.com/Baggio200cn/jojo-director",
            "branch": "feat/science-training-library",
        })
        check("公开仓库与分支检查", inspected.status_code == 200
              and inspected.json()["commitSha"] == SHA and inspected.json()["fileCount"] == 3)
        check("敏感文件和依赖目录被过滤", all(x["path"] != ".env" and not x["path"].startswith("node_modules/")
              for x in inspected.json()["files"]))
        imported = client.post("/api/repositories/import", json={
            "source_url": "https://github.com/Baggio200cn/jojo-director",
            "branch": "feat/science-training-library", "community_id": community_id,
            "paths": ["README.md", "backend/app/main.py"],
        })
        check("选择性导入形成仓库贡献", imported.status_code == 200
              and imported.json()["contribution"]["kind"] == "repository"
              and imported.json()["repository"]["file_count"] == 2)
        repository_id = imported.json()["repository"]["id"]
        contribution_id = imported.json()["contribution"]["id"]
        check("快照记录 Commit 且文件落盘", imported.json()["repository"]["commit_sha"] == SHA
              and (repository.REPOSITORIES_DIR / repository_id / "README.md").is_file()
              and not (repository.REPOSITORIES_DIR / repository_id / "frontend").exists())
        files = client.get(f"/api/repositories/{repository_id}/files")
        check("仓库文件清单受社区权限保护", files.status_code == 200 and len(files.json()) == 2)
        content = client.get(f"/api/repositories/{repository_id}/file", params={"path": "backend/app/main.py"})
        check("代码可按快照本页读取", content.status_code == 200
              and "return 'JOJO'" in content.json()["text"] and content.json()["readOnly"] is True)
        comment = client.post(f"/api/communities/{community_id}/contributions/{contribution_id}/comments",
                              json={"body": "这里需要补充教学说明", "parent_id": "",
                                    "anchor_path": "backend/app/main.py", "anchor_line": 2})
        check("可按文件和代码行讨论", comment.status_code == 200
              and comment.json()["anchor_path"] == "backend/app/main.py"
              and comment.json()["anchor_line"] == 2)
        bad_comment = client.post(f"/api/communities/{community_id}/contributions/{contribution_id}/comments",
                                  json={"body": "错误行号", "parent_id": "",
                                        "anchor_path": "backend/app/main.py", "anchor_line": 99})
        check("无效代码行评论被拒绝", bad_comment.status_code == 400)
        invalid_url = client.post("/api/repositories/inspect", json={"source_url": "http://127.0.0.1/repo"})
        check("非 GitHub 地址不能用于导入", invalid_url.status_code == 400)
        client.close()
        import gc
        gc.collect()


if __name__ == "__main__":
    main()
    print(f"\n通过 {len(PASS)}/{len(PASS) + len(FAIL)}")
    sys.exit(1 if FAIL else 0)
