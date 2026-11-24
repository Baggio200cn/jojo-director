"""解析 HackerTube 官方 ODC 数据包，生成分布报告和分层候选清单。"""
import argparse
import json
import zipfile
from collections import Counter
from pathlib import Path

import yaml

LICENSE = {
    "dataset": "HackerTube and open-index/hacker-news",
    "license": "ODC-By 1.0",
    "attribution": "HackerTube and the open-index/hacker-news dataset",
    "source": "https://hackertube.org/data",
}


def load_rows(archive: zipfile.ZipFile, name: str) -> list[dict]:
    return [json.loads(line) for line in archive.read(name).decode("utf-8").splitlines() if line.strip()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("zip_path", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--sample-size", type=int, default=100)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    mapping = yaml.safe_load((root / "training" / "hackertube_mapping.yaml").read_text(encoding="utf-8"))
    category_map = mapping["category_map"]
    quotas = mapping["quotas"]
    with zipfile.ZipFile(args.zip_path) as archive:
        rows = load_rows(archive, "hackertube-data/videos.jsonl")
        taxonomy = json.loads(archive.read("hackertube-data/taxonomy.json"))
    topic_counts = Counter()
    channel_counts = Counter()
    content_types = Counter()
    levels = Counter()
    shelf_life = Counter()
    candidate_l1_counts = Counter()
    for row in rows:
        for category in row.get("categories") or []:
            topic_counts[category["id"]] += 1
            if category["id"] in category_map:
                candidate_l1_counts[category_map[category["id"]]] += 1
        channel_counts[row.get("channel") or "unknown"] += 1
        content_types[row.get("content_type") or "unknown"] += 1
        levels[row.get("level") or "unknown"] += 1
        shelf_life[row.get("shelf_life") or "unknown"] += 1
    candidates = []
    for row in rows:
        categories = row.get("categories") or []
        matched = next((category_map[c["id"]] for c in categories if c["id"] in category_map), None)
        if not matched:
            continue
        item = {key: row.get(key) for key in
                ("vid", "url", "title", "channel", "summary", "content_type", "level", "shelf_life", "score", "hn")}
        item.update({
            "candidateL1": matched,
            "categories": categories,
            "reviewStatus": "candidate",
            "evidenceStatus": "discovery_only",
            "rights": "reference_only",
            "usage": "教师备课参考；不得直接作为科学真值或生产素材",
        })
        candidates.append(item)
    candidates.sort(key=lambda item: (-(item.get("score") or 0), item.get("title") or ""))
    selected = []
    per_l1 = Counter()
    per_topic = Counter()
    for item in candidates:
        l1 = item["candidateL1"]
        if per_l1[l1] >= quotas.get(l1, 0):
            continue
        topic_key = item["categories"][0]["id"]
        if per_topic[topic_key] >= 3:
            continue
        selected.append(item)
        per_l1[l1] += 1
        per_topic[topic_key] += 1
        if len(selected) >= args.sample_size:
            break
    report = {
        "source": LICENSE,
        "totals": {
            "videos": len(rows),
            "areas": len(taxonomy.get("areas") or []),
            "topics": sum(len(area.get("categories") or []) for area in taxonomy.get("areas") or []),
            "candidateRecords": len(candidates),
            "candidateShare": round(len(candidates) / len(rows), 4) if rows else 0,
        },
        "topicCounts": topic_counts.most_common(),
        "channelCounts": channel_counts.most_common(30),
        "contentTypes": content_types.most_common(),
        "levels": levels.most_common(),
        "shelfLife": shelf_life.most_common(),
        "candidateL1Counts": candidate_l1_counts.most_common(),
        "sample": {"size": len(selected), "perL1": per_l1.most_common(), "perTopicLimit": 3},
        "notes": [
            "候选占比是分类映射覆盖率，不等于科学可用率；科学可用率需人工复核。",
            "YouTube 观看量、时长和发布日期不在导出包内，缺失字段不得补猜。",
            "候选只用于备课参考，入 Skill 前需原始证据、验证和教研批准。",
        ],
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "phase0-hackertube-distribution.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out_dir / "phase0-candidates-100.json").write_text(
        json.dumps(selected, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"videos": len(rows), "candidates": len(candidates), "selected": len(selected),
                      "candidateShare": report["totals"]["candidateShare"], "perL1": per_l1}, ensure_ascii=False))


if __name__ == "__main__":
    main()
