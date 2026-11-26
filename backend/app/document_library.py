"""供登录用户在线阅读和下载的项目协作文档。"""
import hashlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DOCUMENTS = {
    "project": {
        "path": REPO_ROOT / "README.md",
        "filename": "JOJO-项目介绍-README.md",
        "title": "JOJO Director 项目介绍",
    },
    "guide": {
        "path": REPO_ROOT / "docs" / "JOJO科学素材合作者操作手册.md",
        "filename": "JOJO-科学素材合作者操作手册.md",
        "title": "JOJO 科学素材合作者操作手册",
    },
}
ASSETS = {
    "readme-hero": REPO_ROOT / "brand-assets" / "jojo-director-readme-hero.svg",
    "architecture": REPO_ROOT / "docs" / "architecture.png",
}


def document(name: str) -> dict:
    if name not in DOCUMENTS:
        raise ValueError("文档不存在")
    item = DOCUMENTS[name]
    content = item["path"].read_text(encoding="utf-8")
    return {
        "name": name, "title": item["title"], "filename": item["filename"],
        "content": content,
        "contentHash": "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }


def document_path(name: str) -> Path:
    if name not in DOCUMENTS:
        raise ValueError("文档不存在")
    return DOCUMENTS[name]["path"]


def asset_path(name: str) -> Path:
    if name not in ASSETS:
        raise ValueError("文档资源不存在")
    return ASSETS[name]
