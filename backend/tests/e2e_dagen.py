# -*- coding: utf-8 -*-
"""E2E 实战大考：教材第2章《打光定生死》微课全流程（脚本→分镜→展开产线→逐镜生产→拼接）。
全程记录问题清单。用法：cd backend && python tests/e2e_dagen.py
"""
import io
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

BASE = "http://127.0.0.1:8000"
ISSUES: list[str] = []
LOG = Path(__file__).parent / "e2e_dagen_log.txt"


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def issue(msg: str) -> None:
    ISSUES.append(msg)
    log(f"⚠ 问题记录: {msg}")


def req(method: str, path: str, body=None, timeout=900):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    r = urllib.request.Request(f"{BASE}{path}", data=data, method=method,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read())


def run_node(nid: str, wait_running=True) -> dict:
    """执行节点；视频类返回 running 时轮询到落定。"""
    r = req("POST", f"/api/nodes/{nid}/execute")
    if r.get("status") == "running" and wait_running:
        for _ in range(120):
            time.sleep(10)
            n = req("GET", f"/api/nodes/{nid}")
            if n["status"] in ("succeeded", "failed"):
                return n
        return req("GET", f"/api/nodes/{nid}")
    return r


def qc_and_remediate(qc_id: str, target_id: str, tag: str) -> str:
    """跑质检；reject 则采纳教练建议重跑一次再复检（标准操作员动作）。返回最终裁决。"""
    rq = run_node(qc_id)
    out = rq.get("outputs", {})
    verdict = out.get("verdict", "?")
    log(f"  {tag}质检: {verdict} | {str(out.get('summary', ''))[:80]}")
    if verdict != "reject":
        return verdict
    fails = [f"{x.get('id')}:{str(x.get('evidence',''))[:50]}"
             for x in (out.get("blockers") or [])[:3]]
    issue(f"{tag} 首次质检不合格: {'; '.join(fails)}")
    sug = str(out.get("suggested_prompt") or "").strip()
    if not sug:
        issue(f"{tag} 质检不合格但教练未给出建议提示词")
        return verdict
    t = req("GET", f"/api/nodes/{target_id}")
    is_edit = bool(t["inputs"].get("ref_node") or t["inputs"].get("edit_delta"))
    patch = {"edit_delta": sug} if is_edit else {"prompt": sug}
    req("PATCH", f"/api/nodes/{target_id}", {"inputs": {**t["inputs"], **patch}})
    log(f"  采纳教练建议重跑 {tag}（{'编辑指令' if is_edit else '提示词'}通道）")
    rn = run_node(target_id)
    if rn["status"] != "succeeded":
        issue(f"{tag} 采纳建议后重生成失败: {str(rn.get('outputs', {}).get('error'))[:80]}")
        return "failed"
    rq2 = run_node(qc_id)
    v2 = rq2.get("outputs", {}).get("verdict", "?")
    log(f"  {tag}复检: {v2}")
    if v2 == "reject":
        issue(f"{tag} 采纳建议重跑后复检仍不合格（advisory 模式放行进入拼接，需人工把关）")
    return v2


def main() -> None:
    LOG.write_text("", encoding="utf-8")
    t_start = time.time()

    # ── 1. 建项目 + 脚本 ──
    p = req("POST", "/api/projects", {"title": "E2E大考-打光定生死"})
    pid = p["id"]
    log(f"项目: {pid}")
    goal = ("机器视觉打光技术：同一个金属瓶盖在四种光源下的成像差异——"
            "环形光留下环形反光、低角度条形光让划痕拖出阴影（暗场）、背光只给剪影适合测量、"
            "同轴光专治高反光表面。收尾记忆口诀：测量选背光，反光选同轴，划痕选暗场。")
    s = req("POST", f"/api/projects/{pid}/nodes", {
        "type": "script", "title": "打光定生死-脚本",
        "inputs": {"goal": goal, "duration": 90}, "position": {"x": 60, "y": 60}})
    n = run_node(s["id"])
    if n["status"] != "succeeded":
        issue(f"脚本生成失败: {n.get('outputs')}")
        return
    script = n["outputs"]["script"]
    log(f"脚本OK: 《{script.get('title')}》{script.get('duration_seconds')}s "
        f"{len(script.get('segments', []))}段")

    # ── 2. 分镜 ──
    b = req("POST", f"/api/projects/{pid}/nodes", {
        "type": "storyboard", "title": "打光定生死-分镜", "inputs": {},
        "position": {"x": 360, "y": 60}})
    req("POST", f"/api/projects/{pid}/edges",
        {"source_node_id": s["id"], "target_node_id": b["id"]})
    n = run_node(b["id"])
    if n["status"] != "succeeded":
        issue(f"分镜生成失败: {n.get('outputs')}")
        return
    sb = n["outputs"]["storyboard"]
    shots = sb.get("shots", [])
    lint = n["outputs"].get("lint")
    log(f"分镜OK: {len(shots)} 镜 | 歧义词Lint: {lint or '无命中'}")
    for sh in shots:
        log(f"  镜头{sh['index']} [{sh['type']}] {str(sh.get('motion', ''))[:40]}")

    # code_render 模板适配检查：本课无光路/波形内容，不匹配的转 ai_video 并记录
    TEMPLATE_KW = ("光路", "透镜", "聚焦", "波形", "PWM", "框图", "钻孔")
    changed = False
    for sh in shots:
        if sh.get("type") == "code_render" and not any(
                k in (sh.get("motion", "") + sh.get("first_frame_prompt", "")) for k in TEMPLATE_KW):
            issue(f"镜头{sh['index']} 被标为 code_render 但模板库无匹配模板（打光主题缺专用模板），已转 ai_video")
            sh["type"] = "ai_video"
            changed = True
    if changed:
        req("PATCH", f"/api/nodes/{b['id']}", {})  # 仅提示：分镜对象在下方直接以内存版展开
        # 将修改写回分镜节点输出，保证展开读到修正版
        cur = req("GET", f"/api/nodes/{b['id']}")
        # 由后端展开接口读取节点 outputs，因此需要更新 outputs——通过重存 inputs 不行，直接改DB没有API；
        # 记录平台缺口：
        issue("平台缺口：无法通过 API 修正分镜输出（只能改 inputs），展开将按原分镜执行")

    # ── 3. 展开逐镜产线 ──
    r = req("POST", f"/api/nodes/{b['id']}/expand_storyboard", {"domain": "optics"})
    log(f"展开产线: {r['created']} 节点 / {r['shots']} 镜, compose={r['compose_id']}")
    compose_id = r["compose_id"]
    g = req("GET", f"/api/projects/{pid}/graph")
    names = {nd["title"]: nd for nd in g["nodes"]}

    # ── 4. 逐镜生产 ──
    for i in range(1, len(shots) + 1):
        log(f"── 镜头{i} ──")
        cr = names.get(f"镜头{i}·代码渲染")
        if cr:
            n = run_node(cr["id"])
            log(f"  代码渲染: {n['status']}")
            if n["status"] != "succeeded":
                issue(f"镜头{i} 代码渲染失败: {str(n.get('outputs', {}).get('error'))[:80]}")
            q = names.get(f"镜头{i}·质检")
            if q:
                qc_and_remediate(q["id"], cr["id"], f"镜头{i}代码渲染")
            continue
        f1 = names.get(f"镜头{i}·首帧")
        if not f1:
            issue(f"镜头{i} 缺少首帧节点")
            continue
        n = run_node(f1["id"])
        log(f"  首帧: {n['status']}")
        if n["status"] != "succeeded":
            issue(f"镜头{i} 首帧生成失败: {str(n.get('outputs', {}).get('error'))[:80]}")
            continue
        q1 = names.get(f"镜头{i}·首帧质检")
        if q1:
            qc_and_remediate(q1["id"], f1["id"], f"镜头{i}首帧")
        f2 = names.get(f"镜头{i}·尾帧")
        if f2:
            n = run_node(f2["id"])
            pre = n.get("outputs", {}).get("precheck", "")
            log(f"  尾帧: {n['status']} {pre}")
            if n["status"] != "succeeded":
                issue(f"镜头{i} 尾帧生成失败: {str(n.get('outputs', {}).get('error'))[:80]}")
            else:
                q2 = names.get(f"镜头{i}·尾帧质检")
                if q2:
                    qc_and_remediate(q2["id"], f2["id"], f"镜头{i}尾帧")
        v = names.get(f"镜头{i}·视频")
        if not v:
            issue(f"镜头{i} 缺少视频节点")
            continue
        n = run_node(v["id"])
        log(f"  视频: {n['status']}")
        if n["status"] != "succeeded":
            issue(f"镜头{i} 视频生成失败: {str(n.get('outputs', {}).get('error'))[:100]}")
            continue
        qv = names.get(f"镜头{i}·视频质检")
        if qv:
            rq = run_node(qv["id"])
            vd = rq.get("outputs", {}).get("verdict", "?")
            log(f"  视频质检: {vd} | {str(rq.get('outputs', {}).get('summary', ''))[:70]}")
            if vd == "reject":
                issue(f"镜头{i} 视频质检不合格（视频不自动重跑，advisory 放行）: "
                      f"{str(rq.get('outputs', {}).get('summary', ''))[:80]}")

    # ── 5. 拼接 ──
    n = run_node(compose_id)
    log(f"拼接: {n['status']} | {json.dumps(n.get('outputs', {}), ensure_ascii=False)[:160]}")
    if n["status"] == "succeeded":
        url = n["outputs"].get("asset_url", "")
        src = Path(__file__).parent.parent / "assets" / url.split("/")[-1]
        dst = Path(r"C:\Users\Zhaol\Desktop") / "E2E大考-打光定生死-成片.mp4"
        dst.write_bytes(src.read_bytes())
        log(f"成片已拷贝到桌面: {dst.name}")
        if n["outputs"].get("note"):
            log(f"拼接备注: {n['outputs']['note']}")
    else:
        issue(f"拼接失败: {str(n.get('outputs', {}).get('error'))[:100]}")

    # ── 6. 汇总 ──
    mins = round((time.time() - t_start) / 60, 1)
    log(f"═══ 完成，耗时 {mins} 分钟，问题 {len(ISSUES)} 条 ═══")
    for k, x in enumerate(ISSUES, 1):
        log(f"{k}. {x}")


if __name__ == "__main__":
    main()
