# -*- coding: utf-8 -*-
"""回归基线：3 组标准用例，改提示词/换模型前后各跑一遍对比。
用法：cd backend && python tests/regression.py [标签]
输出：tests/report_<标签>.json + 控制台表格
"""
import asyncio
import io
import json
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, executors  # noqa: E402

TAG = sys.argv[1] if len(sys.argv) > 1 else str(int(time.time()))

FIXED_SCRIPT = {
    "title": "PWM调光原理", "duration_seconds": 60,
    "segments": [
        {"index": 1, "narration": "同学们，LED灯是怎么调亮调暗的？答案是PWM。",
         "visual": "教室顶灯特写，亮度变化", "seconds": 20},
        {"index": 2, "narration": "PWM靠占空比控制亮度，占空比越大灯越亮。",
         "visual": "波形图与灯亮度对照", "seconds": 20},
        {"index": 3, "narration": "记住：占空比与亮度成正比，这就是数字调光的核心。",
         "visual": "总结画面", "seconds": 20},
    ],
}


def new_project(name: str) -> str:
    pid = db.new_id("proj")
    db.insert("projects", {"id": pid, "title": f"回归测试-{name}-{TAG}",
                           "status": "draft", "created_at": db.now(),
                           "updated_at": db.now()})
    return pid


def mk_node(pid: str, type_: str, inputs: dict, outputs: dict | None = None,
            status: str = "idle") -> str:
    nid = db.new_id("node")
    db.insert("canvas_nodes", {
        "id": nid, "project_id": pid, "type": type_, "title": "",
        "position_x": 0, "position_y": 0,
        "inputs": json.dumps(inputs, ensure_ascii=False),
        "outputs": json.dumps(outputs or {}, ensure_ascii=False),
        "status": status, "created_at": db.now(), "updated_at": db.now()})
    return nid


def tokens_of(pid: str) -> int:
    rows = db.query("model_tasks", "project_id=?", (pid,))
    return sum((r["input_tokens"] or 0) + (r["output_tokens"] or 0) for r in rows)


async def case_script() -> dict:
    pid = new_project("脚本")
    nid = mk_node(pid, "script", {"goal": "PWM调光原理：占空比与亮度的关系", "duration": 60})
    t0 = time.time()
    await executors.execute_node(nid)
    n = db.get("canvas_nodes", nid)
    s = db.jloads(n["outputs"]).get("script") or {}
    segs = s.get("segments") or []
    total = sum(int(x.get("seconds") or 0) for x in segs)
    checks = {
        "结构完整": bool(s.get("title")) and len(segs) >= 2,
        "时长贴合(60±30%)": 42 <= total <= 78,
        "内容相关(含PWM/占空比)": any(k in json.dumps(s, ensure_ascii=False)
                                        for k in ("PWM", "占空比")),
    }
    return {"case": "脚本生成", "pass": all(checks.values()), "checks": checks,
            "tokens": tokens_of(pid), "seconds": round(time.time() - t0, 1)}


async def case_storyboard() -> dict:
    pid = new_project("分镜")
    src = mk_node(pid, "script", {}, {"script": FIXED_SCRIPT}, status="succeeded")
    nid = mk_node(pid, "storyboard", {})
    db.insert("canvas_edges", {"id": db.new_id("edge"), "project_id": pid,
                               "source_node_id": src, "target_node_id": nid,
                               "source_handle": "output", "target_handle": "input"})
    t0 = time.time()
    await executors.execute_node(nid)
    n = db.get("canvas_nodes", nid)
    shots = (db.jloads(n["outputs"]).get("storyboard") or {}).get("shots") or []
    ok_fields = all(sh.get("first_frame_prompt") and sh.get("motion")
                    and sh.get("type") in ("ai_video", "code_render")
                    and isinstance(sh.get("assertions"), list) and sh["assertions"]
                    for sh in shots)
    no_text_kw = not any(kw in (sh.get("first_frame_prompt") or "").lower()
                         for sh in shots for kw in ("title text", "chinese characters"))
    checks = {
        "镜头数>=2": len(shots) >= 2,
        "字段与断言齐全": bool(shots) and ok_fields,
        "帧提示词不要求画文字": no_text_kw,
    }
    return {"case": "分镜拆解", "pass": all(checks.values()), "checks": checks,
            "tokens": tokens_of(pid), "seconds": round(time.time() - t0, 1)}


async def case_agent() -> dict:
    pid = new_project("Agent")
    t0 = time.time()
    # 3a 最小动作：只要一个脚本节点并执行
    r1 = await executors.run_agent(
        pid, "只生成一个脚本节点：主题是LED驱动器的作用，时长45秒，并立即执行", None, False, [])
    nodes1 = db.query("canvas_nodes", "project_id=?", (pid,))
    # 3b 信息不足：应提问而非建节点
    pid2 = new_project("Agent2")
    r2 = await executors.run_agent(pid2, "帮我做个微课", None, False, [])
    nodes2 = db.query("canvas_nodes", "project_id=?", (pid2,))
    checks = {
        "最小动作(恰好1个script)": len(nodes1) == 1 and nodes1[0]["type"] == "script",
        "要求执行时ran=1": r1.get("ran") == 1,
        "信息不足时0节点先提问": len(nodes2) == 0 and len(r2.get("reply", "")) > 5,
    }
    await asyncio.sleep(12)  # 等 3a 的后台执行落定，避免清理时冲突
    return {"case": "Agent指令遵循", "pass": all(checks.values()), "checks": checks,
            "tokens": (r1.get("tokens") or 0) + (r2.get("tokens") or 0),
            "seconds": round(time.time() - t0, 1)}


def cleanup() -> None:
    with db._conn() as c:
        pids = [r["id"] for r in c.execute(
            "SELECT id FROM projects WHERE title LIKE '回归测试-%'").fetchall()]
        for pid in pids:
            for tbl in ("canvas_nodes", "canvas_edges", "model_tasks", "assets"):
                c.execute(f"DELETE FROM {tbl} WHERE project_id=?", (pid,))
            c.execute("DELETE FROM projects WHERE id=?", (pid,))


async def main() -> None:
    results = []
    for fn in (case_script, case_storyboard, case_agent):
        try:
            results.append(await fn())
        except Exception as e:
            results.append({"case": fn.__name__, "pass": False,
                            "checks": {"异常": str(e)[:120]}, "tokens": 0, "seconds": 0})
    cleanup()
    report = {"tag": TAG, "time": db.now(),
              "total_pass": sum(1 for r in results if r["pass"]),
              "total_tokens": sum(r["tokens"] for r in results),
              "results": results}
    out = Path(__file__).parent / f"report_{TAG}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n═══ 回归报告 [{TAG}] ═══")
    for r in results:
        mark = "✅" if r["pass"] else "❌"
        print(f"{mark} {r['case']}  tokens={r['tokens']}  {r['seconds']}s")
        for k, v in r["checks"].items():
            print(f"    {'✓' if v is True else '✗' if v is False else '·'} {k}"
                  + ("" if isinstance(v, bool) else f": {v}"))
    print(f"通过 {report['total_pass']}/{len(results)} | 总tokens {report['total_tokens']}")
    print(f"已存 {out.name}")


if __name__ == "__main__":
    asyncio.run(main())
