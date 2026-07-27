# -*- coding: utf-8 -*-
"""E2E 全量生产：《LED驱动器与智慧照明控制》微课（增量协作测试-PWM 项目）。
跑通现有 12 镜产线的全部待产节点 → 拼接成片 → 问题清单。
"""
import io
import json
import re
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

BASE = "http://127.0.0.1:8000"
PID = "proj_a90aaa5c8987"
ISSUES: list[str] = []
LOG = Path(__file__).parent / "e2e_led_log.txt"


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def issue(msg: str) -> None:
    ISSUES.append(msg)
    log(f"⚠ 问题: {msg}")


def req(method: str, path: str, body=None, timeout=1200):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    r = urllib.request.Request(f"{BASE}{path}", data=data, method=method,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read())


def run_node(nid: str) -> dict:
    try:
        r = req("POST", f"/api/nodes/{nid}/execute")
    except urllib.error.HTTPError as e:
        return {"status": "failed", "outputs": {"error": e.read().decode()[:120]}}
    if r.get("status") == "running":
        for _ in range(150):
            time.sleep(10)
            n = req("GET", f"/api/nodes/{nid}")
            if n["status"] in ("succeeded", "failed"):
                return n
        return req("GET", f"/api/nodes/{nid}")
    return r


def qc_remediate(qc_id: str, target_id: str, tag: str) -> str:
    rq = run_node(qc_id)
    out = rq.get("outputs", {})
    v = out.get("verdict", "?")
    log(f"  {tag}质检: {v} | {str(out.get('summary', ''))[:70]}")
    if v != "reject":
        return v
    fails = [f"{x.get('id')}:{str(x.get('evidence', ''))[:45]}"
             for x in (out.get("blockers") or [])[:3]]
    issue(f"{tag} 质检不合格: {'; '.join(fails)}")
    sug = str(out.get("suggested_prompt") or "").strip()
    if not sug:
        issue(f"{tag} 教练未给出修正建议")
        return v
    t = req("GET", f"/api/nodes/{target_id}")
    is_edit = bool(t["inputs"].get("ref_node") or t["inputs"].get("edit_delta"))
    patch = {"edit_delta": sug} if is_edit else {"prompt": sug}
    req("PATCH", f"/api/nodes/{target_id}", {"inputs": {**t["inputs"], **patch}})
    log(f"  采纳教练建议重跑（{'编辑' if is_edit else '提示词'}通道）")
    rn = run_node(target_id)
    if rn["status"] != "succeeded":
        issue(f"{tag} 重生成失败: {str(rn.get('outputs', {}).get('error'))[:80]}")
        return "failed"
    v2 = run_node(qc_id).get("outputs", {}).get("verdict", "?")
    log(f"  {tag}复检: {v2}")
    if v2 == "reject":
        issue(f"{tag} 复检仍不合格（advisory 放行，需人工把关）")
    return v2


def main() -> None:
    LOG.write_text("", encoding="utf-8")
    t0 = time.time()
    g = req("GET", f"/api/projects/{PID}/graph")
    by_title = {n["title"]: n for n in g["nodes"] if n["title"]}
    shots: dict[int, dict] = defaultdict(dict)
    for n in g["nodes"]:
        m = re.match(r"^镜头(\d+)·(.+)$", n["title"] or "")
        if m:
            shots[int(m.group(1))][m.group(2)] = n
    log(f"产线盘点: {len(shots)} 镜")

    for i in sorted(shots):
        parts = shots[i]
        log(f"══ 镜头{i} ══")
        # 代码渲染路线
        if "代码渲染" in parts:
            cr = parts["代码渲染"]
            # 模板适配：PWM 主题镜头用 pwm_waveform 而不是默认 lens_focus
            if cr["inputs"].get("template") == "lens_focus":
                req("PATCH", f"/api/nodes/{cr['id']}",
                    {"inputs": {**cr["inputs"], "template": "pwm_waveform"}})
                issue(f"镜头{i} 代码渲染默认模板 lens_focus 与内容不符，操作员改为 pwm_waveform"
                      "（平台缺口：展开时未按镜头内容选模板）")
            if cr["status"] != "succeeded":
                n = run_node(cr["id"])
                log(f"  代码渲染: {n['status']}")
                if n["status"] != "succeeded":
                    issue(f"镜头{i} 代码渲染失败: {str(n.get('outputs', {}).get('error'))[:90]}")
                    continue
            if "质检" in parts:
                qc_remediate(parts["质检"]["id"], cr["id"], f"镜头{i}渲染")
            continue
        # AI 路线：首帧 → 质检 → 尾帧 → 质检 → 视频 → 质检
        f1 = parts.get("首帧")
        if not f1:
            issue(f"镜头{i} 缺首帧节点")
            continue
        if f1["status"] != "succeeded":
            n = run_node(f1["id"])
            log(f"  首帧: {n['status']}")
            if n["status"] != "succeeded":
                issue(f"镜头{i} 首帧失败: {str(n.get('outputs', {}).get('error'))[:90]}")
                continue
            if "首帧质检" in parts:
                qc_remediate(parts["首帧质检"]["id"], f1["id"], f"镜头{i}首帧")
        else:
            log("  首帧: 已完成(复用)")
        f2 = parts.get("尾帧")
        if f2 and f2["status"] != "succeeded":
            n = run_node(f2["id"])
            log(f"  尾帧: {n['status']} {n.get('outputs', {}).get('precheck', '')}")
            if n["status"] == "succeeded" and "尾帧质检" in parts:
                qc_remediate(parts["尾帧质检"]["id"], f2["id"], f"镜头{i}尾帧")
            elif n["status"] != "succeeded":
                issue(f"镜头{i} 尾帧失败: {str(n.get('outputs', {}).get('error'))[:90]}"
                      " → 改单首帧模式继续")
                v = parts.get("视频")
                if v:
                    vi = {k: x for k, x in v["inputs"].items() if k != "last_frame_node"}
                    req("PATCH", f"/api/nodes/{v['id']}", {"inputs": vi})
        elif f2:
            log("  尾帧: 已完成(复用)")
        v = parts.get("视频")
        if not v:
            issue(f"镜头{i} 缺视频节点")
            continue
        if v["status"] != "succeeded":
            n = run_node(v["id"])
            log(f"  视频: {n['status']}")
            if n["status"] != "succeeded":
                issue(f"镜头{i} 视频失败: {str(n.get('outputs', {}).get('error'))[:110]}")
                continue
        else:
            log("  视频: 已完成(复用)")
        if "视频质检" in parts:
            rq = run_node(parts["视频质检"]["id"])
            vd = rq.get("outputs", {}).get("verdict", "?")
            log(f"  视频质检: {vd}")
            if vd == "reject":
                issue(f"镜头{i} 视频质检不合格(视频不重跑,advisory放行): "
                      f"{str(rq.get('outputs', {}).get('summary', ''))[:80]}")

    # ── 拼接 ──
    comp = by_title.get("拼接成片")
    if not comp:
        issue("找不到拼接节点")
        return
    n = run_node(comp["id"])
    log(f"拼接: {n['status']} | {json.dumps(n.get('outputs', {}), ensure_ascii=False)[:150]}")
    if n["status"] == "succeeded":
        url = n["outputs"].get("asset_url", "")
        src = Path(__file__).parent.parent / "assets" / url.split("/")[-1]
        dst = Path(r"C:\Users\Zhaol\Desktop") / "LED驱动器与智慧照明控制-成片.mp4"
        dst.write_bytes(src.read_bytes())
        log(f"✅ 成片已拷贝到桌面: {dst.name}")
    else:
        issue(f"拼接失败: {str(n.get('outputs', {}).get('error'))[:110]}")

    mins = round((time.time() - t0) / 60, 1)
    log(f"═══ 全部完成 耗时{mins}分钟 问题{len(ISSUES)}条 ═══")
    for k, x in enumerate(ISSUES, 1):
        log(f"{k}. {x}")


if __name__ == "__main__":
    import urllib.error  # noqa: E402
    main()
