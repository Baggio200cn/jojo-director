"""DOCX 上传、解析与素材本页阅读接口测试。"""
import io
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
from app import asset_ingest, auth, db, document_text
from app.main import app

PASS, FAIL = [], []


def check(name, ok):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}")


def docx_bytes() -> bytes:
    document = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>L01 机器学习导论</w:t></w:r></w:p>
<w:p><w:r><w:t>本课讲解神经网络与机器视觉的基本关系。</w:t></w:r></w:p>
<w:p><w:pPr><w:numPr/></w:pPr><w:r><w:t>核对能力地图</w:t></w:r></w:p>
<w:tbl><w:tr><w:tc><w:p><w:r><w:t>知识点</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>讲稿位置</w:t></w:r></w:p></w:tc></w:tr>
<w:tr><w:tc><w:p><w:r><w:t>神经网络</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>第二段</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
</w:body></w:document>"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'/>")
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def main():
    parsed = document_text.parse_docx(docx_bytes())
    check("DOCX 标题、列表和表格解析", "# L01 机器学习导论" in parsed["text"]
          and "- 核对能力地图" in parsed["text"] and "| 知识点 | 讲稿位置 |" in parsed["text"])
    check("DOCX 结构统计", parsed["paragraphCount"] >= 5 and parsed["tableCount"] == 1)
    check("非法 DOCX 被拒绝", not document_text.valid_docx(b"not-a-word-file"))

    with tempfile.TemporaryDirectory() as tmp:
        db.DB_PATH = str(Path(tmp) / "test.db")
        asset_ingest.ASSETS_DIR = Path(tmp) / "assets"
        asset_ingest.ASSETS_DIR.mkdir()
        import app.main as main_module
        main_module.ASSETS_DIR = asset_ingest.ASSETS_DIR
        db.init_db()
        auth.init()
        client = TestClient(app)
        response = client.post("/api/assets/upload", files={
            "file": ("L01讲稿.docx", docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        }, data={"folder": "人工智能（AI）/CWC", "rights": "own", "note": "Word 投稿"})
        check("单文件 Word 投稿成功", response.status_code == 200)
        asset_id = response.json()["id"]
        preview = client.get(f"/api/assets/{asset_id}/text")
        check("Word 可在本页读取正文", preview.status_code == 200
              and preview.json()["format"] == "markdown"
              and "神经网络与机器视觉" in preview.json()["text"])

        folder = client.post("/api/assets/upload_folder", files=[
            ("files", ("L02讲稿.docx", docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")),
        ], data={"paths": ["CWC/L02讲稿.docx"], "folder": "__auto__", "rights": "own", "note": "文件夹 Word 投稿"})
        check("文件夹 Word 上传并按正文归类", folder.status_code == 200
              and folder.json()["summary"]["accepted"] == 1
              and folder.json()["results"][0]["folder"].startswith("人工智能（AI）/"))
        broken = client.post("/api/assets/upload", files={
            "file": ("broken.docx", b"broken", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        }, data={"folder": "待确认", "rights": "own", "note": ""})
        check("单文件损坏 Word 明确拒绝", broken.status_code == 400)
        unsupported = client.get(f"/api/assets/{asset_id}/text".replace(asset_id, "unknown"))
        check("未知素材返回明确错误", unsupported.status_code == 404)
        client.close()
        import gc
        gc.collect()


if __name__ == "__main__":
    main()
    print(f"\n通过 {len(PASS)}/{len(PASS) + len(FAIL)}")
    sys.exit(1 if FAIL else 0)
