"""文件夹投稿的本地检查、去重与可审核归类。"""
import hashlib
from pathlib import Path, PurePosixPath

from . import db
from .config import ASSETS_DIR

MAX_FILES = 200
MAX_TOTAL_BYTES = 500 * 1024 * 1024
EXTENSIONS = {
    ".mp4": "video", ".mov": "video", ".webm": "video",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image",
    ".pdf": "doc", ".txt": "doc", ".md": "doc", ".csv": "doc",
}
DOMAIN_RULES = {
    "基础医学教学类": ["医学", "生理", "解剖", "心脏", "细胞", "血液", "药物", "护理", "medical", "anatomy", "physiology", "cardiac", "cell", "pharmacology", "nursing"],
    "光学类": ["光学", "透镜", "光路", "机器视觉", "相机", "标定", "光源", "镜头", "焦距", "optics", "lens", "camera", "calibration", "vision"],
    "电气与电子": ["pwm", "led", "电路", "占空比", "电气", "电子", "电力", "控制", "circuit", "electrical", "electronic"],
    "计算机科学与技术": ["代码", "算法", "程序", "数据结构", "计算机", "网络", "数据库", "python", "software", "algorithm", "programming", "computer"],
    "机械工程": ["机械", "齿轮", "轴承", "液压", "气动", "机器人", "mechanical", "gear", "hydraulic", "robot"],
    "化学": ["化学", "反应", "溶液", "酸碱", "chemistry", "chemical"],
    "生物学": ["生物", "遗传", "微生物", "植物", "动物", "biology", "genetic"],
    "运动科学与体育": ["运动", "体育", "康复", "体适能", "sport", "exercise"],
    "地球与空间科学": ["地质", "天文", "气象", "海洋", "geology", "astronomy", "weather", "ocean"],
}


def safe_relative_path(value: str) -> str:
    value = value.replace("\\", "/").strip().strip("/")
    path = PurePosixPath(value)
    if not value or value.startswith("/") or any(x in ("", ".", "..") for x in path.parts):
        raise ValueError("文件相对路径无效")
    return "/".join(path.parts)


def signature_valid(ext: str, content: bytes) -> bool:
    if ext == ".png": return content.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in (".jpg", ".jpeg"): return content.startswith(b"\xff\xd8\xff")
    if ext == ".webp": return len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    if ext == ".pdf": return content.startswith(b"%PDF-")
    if ext in (".mp4", ".mov"): return len(content) >= 12 and content[4:8] == b"ftyp"
    if ext == ".webm": return content.startswith(b"\x1a\x45\xdf\xa3")
    if ext in (".txt", ".md", ".csv"):
        try:
            content.decode("utf-8-sig")
            return True
        except UnicodeDecodeError:
            return False
    return False


def classify(relative_path: str, note: str, text_content: str = "") -> dict:
    text = f"{relative_path} {note} {text_content}".lower()
    scores = {domain: sum(text.count(keyword.lower()) for keyword in keywords)
              for domain, keywords in DOMAIN_RULES.items()}
    candidates = sorted(({"domain": domain, "score": score} for domain, score in scores.items() if score),
                        key=lambda x: -x["score"])
    if not candidates:
        return {"suggestedDomain": "待确认", "candidates": [], "ambiguous": False}
    ambiguous = len(candidates) > 1 and candidates[0]["score"] == candidates[1]["score"]
    return {"suggestedDomain": "待确认" if ambiguous else candidates[0]["domain"],
            "candidates": candidates[:3], "ambiguous": ambiguous}


def _existing_hash(content_hash: str) -> dict | None:
    for row in db.query("assets"):
        if db.jloads(row.get("meta")).get("contentHash") == content_hash:
            return row
    return None


def ingest_file(content: bytes, relative_path: str, folder: str, rights: str,
                note: str, batch_id: str, seen_hashes: set[str]) -> dict:
    try:
        relative_path = safe_relative_path(relative_path)
    except ValueError as exc:
        return {"status": "rejected", "path": relative_path, "reason": str(exc)}
    ext = Path(relative_path).suffix.lower()
    if ext not in EXTENSIONS:
        return {"status": "rejected", "path": relative_path, "reason": "不支持的文件格式"}
    if not signature_valid(ext, content):
        return {"status": "rejected", "path": relative_path, "reason": "文件内容与格式签名不匹配或文件损坏"}
    content_hash = hashlib.sha256(content).hexdigest()
    if content_hash in seen_hashes:
        return {"status": "duplicate", "path": relative_path, "reason": "本批次已有相同内容"}
    existing = _existing_hash(content_hash)
    if existing:
        return {"status": "duplicate", "path": relative_path,
                "reason": "素材库已有相同内容", "assetId": existing["id"]}

    text_content = content.decode("utf-8-sig")[:65536] if ext in (".txt", ".md", ".csv") else ""
    classification = classify(relative_path, note, text_content)
    parent = PurePosixPath(relative_path).parent.as_posix()
    topic = "" if parent == "." else parent
    if folder == "__auto__":
        actual_folder = classification["suggestedDomain"]
        if actual_folder != "待确认" and topic:
            actual_folder = f"{actual_folder}/{topic}"
    else:
        actual_folder = folder.strip().strip("/") or "待确认"
    kind = EXTENSIONS[ext]
    asset_ext = ".mp4" if ext in (".mov", ".webm") else ext
    asset_id = db.new_id("asset")
    filename = f"{asset_id}{asset_ext}"
    (ASSETS_DIR / filename).write_bytes(content)
    analysis = {
        "method": "local_rules_v1", "contentScope": "filename_path_note_and_utf8_text" if text_content else "format_signature_filename_path_note",
        "signatureValid": True, "sizeBytes": len(content),
        "classification": classification, "reviewRequired": True,
    }
    meta = {
        "source": "teacher_folder_upload", "note": note.strip(),
        "orig_name": PurePosixPath(relative_path).name,
        "relativePath": relative_path, "contentHash": content_hash,
        "uploadBatchId": batch_id, "analysis": analysis,
    }
    db.insert("assets", {
        "id": asset_id, "project_id": "", "node_id": "", "kind": kind,
        "filename": filename, "meta": __import__("json").dumps(meta, ensure_ascii=False),
        "created_at": db.now(), "folder": actual_folder, "rights": rights,
        "subject_id": "", "library": 1,
    })
    if actual_folder:
        with db._conn() as conn:
            conn.execute("INSERT OR IGNORE INTO library_folders (path,created_at) VALUES (?,?)",
                         (actual_folder, db.now()))
    seen_hashes.add(content_hash)
    return {"status": "accepted", "path": relative_path, "assetId": asset_id,
            "folder": actual_folder, "kind": kind, "analysis": analysis}
