"""训练库分类、候选数据和候选 Skill 的离线契约测试。"""
import io
import json
import sys
from pathlib import Path

import yaml

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
PASS, FAIL = [], []


def check(name, ok):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}")


taxonomy = yaml.safe_load((ROOT / "training" / "taxonomy.yaml").read_text(encoding="utf-8"))
levels = taxonomy["levels"]
codes = []

def walk(items):
    for item in items:
        codes.append(item["code"])
        walk(item.get("children") or [])
walk(levels)
check("分类编码唯一", len(codes) == len(set(codes)))
check("计算机独立一级分类", any(x["code"] == "SCI-CS" for x in levels))
check("基础医学目录已建立", any(x["code"] == "SCI-MED" for x in levels))
check("分类分裂需审核", taxonomy["rules"]["split_requires_reviewer"] is True)

report = json.loads((ROOT.parent / "reports" / "training" / "phase0-hackertube-distribution.json").read_text(encoding="utf-8"))
check("真实记录总数正确", report["totals"]["videos"] == 6454 and report["totals"]["topics"] == 150)
check("候选覆盖不等于科学可用率", "不等于科学可用率" in report["notes"][0])
candidates = json.loads((ROOT.parent / "reports" / "training" / "phase0-candidates-100.json").read_text(encoding="utf-8"))
check("分层候选为 100 条", len(candidates) == 100)
check("候选视频 ID 去重", len({x["vid"] for x in candidates}) == 100)
check("候选只做发现参考", all(x["reviewStatus"] == "candidate" and x["evidenceStatus"] == "discovery_only"
                              and x["rights"] == "reference_only" for x in candidates))
check("候选包含计算机", any(x["candidateL1"] == "SCI-CS" for x in candidates))
check("候选包含基础医学", any(x["candidateL1"] == "SCI-MED" for x in candidates))

for schema_name in ("training-evidence-card-v1.json", "training-skill-manifest-v1.json"):
    schema = json.loads((ROOT / "schemas" / schema_name).read_text(encoding="utf-8"))
    check(f"Schema 可读取 {schema_name}", isinstance(schema.get("required"), list))

skills = sorted((ROOT / "skills").glob("SCI-*/manifest.yaml"))
check("首批三个候选 Skill", len(skills) == 3)
for path in skills:
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    check(f"{manifest['skillId']} 未投产", manifest["status"] == "candidate"
          and manifest["reviewStatus"]["status"] == "pending")
    check(f"{manifest['skillId']} 有真实来源", bool(manifest["source"]))
    check(f"{manifest['skillId']} 禁止生成式主画面", "generated_scene" in manifest["forbiddenRoutes"])
    spec = json.loads((path.parent / "spec_template.json").read_text(encoding="utf-8"))
    check(f"{manifest['skillId']} 场景规格需复核", spec["reviewRequired"] is True)

print(f"\n通过 {len(PASS)}/{len(PASS) + len(FAIL)}")
sys.exit(1 if FAIL else 0)
