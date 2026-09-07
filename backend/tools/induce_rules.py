"""LLM 规则归纳：自我学习飞轮的"规则草稿"生成器。

输入：assertion_samples.yaml 的正/反例 + 各课题包的 pitfalls（误判沉淀）
     （后续接入改判记录表后，改判是主要原料）
产出：qc_rules/_drafts.yaml —— status=pending 的规则草稿，
     必须由教研团队审核、移入正式规则文件后才生效（铁律：机器规则不自动生效）。

用法：
    cd backend && python -m tools.induce_rules            # 归纳全部学科
    cd backend && python -m tools.induce_rules optics     # 只归纳 optics
"""
import asyncio
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.gateway.ark import ArkAdapter  # noqa: E402

DRAFTS_PATH = config.QC_RULES_DIR / "_drafts.yaml"

PROMPT = """你是科学实验视频质检规则的归纳助手。下面是某学科真实审核中沉淀的
【正例断言】（什么是合格的）和【反例/误判记录】（机器曾判错或容易混淆的）。

请归纳出最多 5 条新的质检规则草稿，要求：
- 每条规则是"什么情况算不合格"的可执行判断，具体到可见画面特征
- 不与常识重复，不抄正例原文，从反例的教训中提炼
- 输出严格 JSON（不要 markdown 代码块）：
{{"rules": [{{"id": "DRAFT-XX", "severity": "high|medium|low",
            "check": "判断描述", "from": ["来源样例id"]}}]}}

【正例】
{positives}

【反例/误判记录】
{negatives}

只输出 JSON，不要任何解释。"""


def _collect(domain: str) -> tuple[list[dict], list[dict]]:
    """收集该学科的样例：assertion_samples + 课题包 pitfalls + 真实改判记录。"""
    pos, neg = [], []
    samples_file = config.QC_RULES_DIR / "assertion_samples.yaml"
    if samples_file.exists():
        data = yaml.safe_load(samples_file.read_text(encoding="utf-8")) or {}
        for s in data.get("samples", []):
            if s.get("domain") in (domain, "general"):
                (pos if s.get("kind") == "positive" else neg).append(s)
    for f in config.QC_RULES_DIR.glob("*.yaml"):
        if f.name.startswith(("_", "assertion_samples")):
            continue
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        if data.get("topic_of") == domain:
            for p in data.get("pitfalls", []):
                neg.append(p)
    # 改判记录：飞轮最有价值的原料（老师纠正机器的实例），最近 50 条
    try:
        from app import db
        for r in db.query("qc_corrections", "1=1 ORDER BY created_at DESC LIMIT 50"):
            neg.append({"id": r["id"], "kind": "negative",
                        "text": f"{r['fail_type']}：{r['reason']}"
                                f"（机器原判{r['orig_verdict']}→人工终裁{r['human_verdict']}）"})
    except Exception:
        pass  # 表不存在（旧库）时静默跳过
    return pos, neg


def _merge_drafts(new_rules: list[dict], domain: str) -> int:
    """合并进 _drafts.yaml，按 check 文本去重，全部标 status=pending。"""
    existing = {"domain": domain, "rules": []}
    if DRAFTS_PATH.exists():
        existing = yaml.safe_load(DRAFTS_PATH.read_text(encoding="utf-8")) or existing
    rules = existing.setdefault("rules", [])
    seen = {r.get("check", "").strip() for r in rules}
    added = 0
    for r in new_rules:
        if not isinstance(r, dict) or not r.get("check"):
            continue
        if r["check"].strip() in seen:
            continue
        r["status"] = "pending"
        r["domain"] = domain
        rules.append(r)
        seen.add(r["check"].strip())
        added += 1
    DRAFTS_PATH.write_text(
        yaml.safe_dump(existing, allow_unicode=True, sort_keys=False),
        encoding="utf-8")
    return added


async def induce(domain: str) -> dict:
    pos, neg = _collect(domain)
    if not neg:
        return {"domain": domain, "added": 0, "reason": "无反例/误判沉淀，不归纳（禁止编造）"}
    ark = ArkAdapter()
    prompt = PROMPT.format(
        positives=json.dumps(pos, ensure_ascii=False, indent=1),
        negatives=json.dumps(neg, ensure_ascii=False, indent=1))
    r = await ark.chat(config.route("script")["model"],
                       [{"role": "user", "content": prompt}],
                       json_mode=True, max_tokens=4000)
    text = r["text"].strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    if not text:
        return {"domain": domain, "added": 0,
                "reason": f"模型输出为空（reasoning {len(r.get('reasoning', ''))} 字符）"}
    try:
        draft = json.loads(text)
    except json.JSONDecodeError as e:
        return {"domain": domain, "added": 0, "reason": f"模型输出非JSON: {e}"}
    added = _merge_drafts(draft.get("rules", []), domain)
    return {"domain": domain, "added": added,
            "tokens": r["input_tokens"] + r["output_tokens"]}


if __name__ == "__main__":
    domains = [sys.argv[1]] if len(sys.argv) > 1 else ["optics"]
    for d in domains:
        result = asyncio.run(induce(d))
        print(f"[{d}] 新增草稿 {result.get('added', 0)} 条 "
              f"({result.get('reason', 'ok')}, tokens={result.get('tokens', '-')})")
    print(f"草稿文件: {DRAFTS_PATH} —— 待教研审核后方可转入正式规则")
