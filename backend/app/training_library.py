"""科学素材训练库的只读目录与追加式人工抽检记录。"""
import hashlib
import json
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from . import db

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent
SKILLS_DIR = BACKEND_ROOT / "skills"
TAXONOMY_PATH = BACKEND_ROOT / "training" / "taxonomy.yaml"
DISCOVERY_PATH = REPO_ROOT / "reports" / "training" / "phase0-candidates-100.json"
DISCOVERY_VERSION = "phase0-candidates-100@1"
REVIEW_DECISIONS = {"sample_passed", "needs_evidence", "needs_revision"}
REQUIRED_CHECKS = {"source_checked", "rights_checked", "scope_checked"}
ALLOWED_CHECKS = REQUIRED_CHECKS | {"claim_checked", "reproducibility_checked"}


class ReviewIn(BaseModel):
    objectType: Literal["skill", "discovery"]
    objectId: str = Field(min_length=1, max_length=160)
    contentHash: str = Field(min_length=64, max_length=64)
    reviewer: str = Field(min_length=1, max_length=120)
    decision: Literal["sample_passed", "needs_evidence", "needs_revision"]
    comment: str = Field(default="", max_length=4000)
    checks: list[str] = Field(default_factory=list, max_length=5)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _reviews(object_type: str, object_id: str) -> list[dict]:
    rows = db.query("training_reviews",
                    "object_type=? AND object_id=? ORDER BY created_at DESC, rowid DESC",
                    (object_type, object_id))
    return [{**row, "checks": json.loads(row["checks"])} for row in rows]


def _skill_items() -> list[dict]:
    items = []
    for manifest_path in sorted(SKILLS_DIR.glob("SCI-*/manifest.yaml")):
        folder = manifest_path.parent
        files = [manifest_path, folder / "spec_template.json", folder / "qc" / "rules.yaml"]
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        spec = json.loads((folder / "spec_template.json").read_text(encoding="utf-8"))
        qc = yaml.safe_load((folder / "qc" / "rules.yaml").read_text(encoding="utf-8"))
        digest = _sha256("".join(p.read_text(encoding="utf-8") for p in files).encode("utf-8"))
        reviews = _reviews("skill", manifest["skillId"])
        items.append({
            "kind": "skill", "objectId": manifest["skillId"],
            "version": str(manifest["version"]), "contentHash": digest,
            "manifest": manifest, "spec": spec, "qc": qc,
            "reviews": reviews, "latestReview": reviews[0] if reviews else None,
        })
    return items


def _discovery_items() -> list[dict]:
    records = json.loads(DISCOVERY_PATH.read_text(encoding="utf-8"))
    items = []
    for record in records:
        digest = _sha256(json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        reviews = _reviews("discovery", record["vid"])
        items.append({
            **record, "kind": "discovery", "objectId": record["vid"],
            "version": DISCOVERY_VERSION, "contentHash": digest,
            "reviews": reviews, "latestReview": reviews[0] if reviews else None,
        })
    return items


def library() -> dict:
    taxonomy = yaml.safe_load(TAXONOMY_PATH.read_text(encoding="utf-8"))
    skills = _skill_items()
    discoveries = _discovery_items()
    sampled = sum(1 for item in [*skills, *discoveries] if item["latestReview"])
    return {
        "taxonomy": taxonomy,
        "skills": skills,
        "discoveries": discoveries,
        "stats": {
            "skills": len(skills), "discoveries": len(discoveries),
            "approvedSkills": sum(1 for x in skills if x["manifest"]["status"] == "approved"),
            "sampled": sampled, "pendingReview": len(skills) + len(discoveries) - sampled,
        },
        "governance": {
            "reviewIsAppendOnly": True,
            "samplePassApprovesProduction": False,
            "discoveryIsScientificTruth": False,
        },
    }


def add_review(payload: ReviewIn, session: dict) -> dict:
    reviewer = payload.reviewer.strip()
    comment = payload.comment.strip()
    checks = set(payload.checks)
    if not reviewer:
        raise ValueError("请填写审核人")
    if not checks.issubset(ALLOWED_CHECKS):
        raise ValueError("审核检查项无效")
    if payload.decision == "sample_passed" and not REQUIRED_CHECKS.issubset(checks):
        raise ValueError("抽检通过前须核对来源、权利范围和适用范围")
    if payload.decision != "sample_passed" and not comment:
        raise ValueError("退回补证或修改时必须填写具体原因")

    items = _skill_items() if payload.objectType == "skill" else _discovery_items()
    item = next((x for x in items if x["objectId"] == payload.objectId), None)
    if not item:
        raise ValueError("审核对象不存在")
    if item["contentHash"] != payload.contentHash:
        raise ValueError("候选内容已变化，请刷新后基于新版本重新审核")

    row = {
        "id": db.new_id("trv"), "object_type": payload.objectType,
        "object_id": payload.objectId, "object_version": item["version"],
        "content_hash": payload.contentHash, "reviewer": reviewer,
        "decision": payload.decision, "comment": comment,
        "checks": json.dumps(sorted(checks), ensure_ascii=False),
        "session_role": session.get("role", "unknown"), "created_at": db.now(),
    }
    db.insert("training_reviews", row)
    return {**row, "checks": sorted(checks),
            "productionStatus": item["manifest"]["status"] if payload.objectType == "skill" else "reference_only"}
