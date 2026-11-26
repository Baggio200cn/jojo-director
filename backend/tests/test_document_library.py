"""项目介绍与合作手册在线阅读、下载及资源测试。"""
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
from app.main import app


def main():
    with tempfile.TemporaryDirectory():
        client = TestClient(app)
        project = client.get("/api/docs/project")
        assert project.status_code == 200
        assert "Scientific Scene Production Engine" in project.json()["content"]
        assert project.json()["contentHash"].startswith("sha256:")

        guide = client.get("/api/docs/guide")
        assert guide.status_code == 200
        assert "巴老师" in guide.json()["content"]
        assert "19129910199" in guide.json()["content"]
        assert "zhaoliang690816@gmail.com" in guide.json()["content"]

        download = client.get("/api/docs/guide/download")
        assert download.status_code == 200
        assert download.headers["content-type"].startswith("text/markdown")
        assert ".md" in download.headers["content-disposition"]
        assert download.content.decode("utf-8").replace("\r\n", "\n") == guide.json()["content"].replace("\r\n", "\n")

        hero = client.get("/api/docs/assets/readme-hero")
        assert hero.status_code == 200 and b"<svg" in hero.content
        architecture = client.get("/api/docs/assets/architecture")
        assert architecture.status_code == 200 and architecture.content.startswith(b"\x89PNG")
        assert client.get("/api/docs/not-found").status_code == 404
        assert client.get("/api/docs/assets/not-found").status_code == 404
        client.close()
    print("通过：项目介绍、合作手册、下载、图片与未知路径共 15 项检查")


if __name__ == "__main__":
    main()
