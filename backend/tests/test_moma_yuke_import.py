"""MoMA 路由与驭课 ZIP 导入的离线契约测试。"""
import asyncio
import gc
import io
import json
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

from fastapi.testclient import TestClient

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from app import db, executors
from app.config import AGENT_MODELS
from app.gateway import bjmoma as moma_gateway
from app.main import app

PASS, FAIL = [], []


def check(name, ok):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}")


def package_zip():
    plan = {
        "title": "PWM 调光原理",
        "goal": "解释占空比与亮度的关系",
        "shots": [
            {"id": 1, "seconds": 10, "method": "slide", "visual": "PWM 波形逐渐变化",
             "narration": "占空比改变，平均功率随之改变。", "prompt": "展示 PWM 波形",
             "sources": ["ppt-1"], "review": "核对占空比方向"},
            {"id": 2, "seconds": 5, "method": "teacher", "visual": "教师调节灯光",
             "narration": "观察灯光变化。", "prompt": "", "sources": ["video-1"],
             "review": "使用教师自有录屏"},
        ],
    }
    handoff = {
        "format": "yuke-micro-intro/v1", "course": "LED 驱动器", "lessonNumber": 1,
        "sourceVersion": "blueprint-v7", "sources": [
            {"id": "ppt-1", "type": "ppt_outline", "label": "课件", "version": "ppt-v3", "usable": True,
             "content": {"shouldNotBeDuplicated": True}},
        ],
        "plan": plan, "referenceFrames": [{"shot": 1}], "videoGenerated": False,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("第1节/JOJO交接/JOJO-handoff.json", json.dumps(handoff, ensure_ascii=False))
    return buf.getvalue()


async def test_moma_adapter():
    calls = []

    async def fake_request(method, url, body=None, stream_to=None):
        calls.append((method, url, body))
        if url.endswith("/chat/completions"):
            return {"model": "kimi-k3", "choices": [{"message": {"content": '{"ok":true}'}}],
                    "usage": {"prompt_tokens": 8, "completion_tokens": 3}}
        return {"data": [{"url": "https://example.invalid/image.png"}], "usage": {}}

    original = moma_gateway._request
    moma_gateway._request = fake_request
    try:
        adapter = moma_gateway.BjmomaAdapter()
        chat = await adapter.chat("kimi-k3", [{"role": "user", "content": "test"}], json_mode=True)
        image = await adapter.image("doubao-seedream-5-0-260128", "PWM waveform")
    finally:
        moma_gateway._request = original
    check("MoMA chat OpenAI 兼容协议", chat["text"] == '{"ok":true}' and chat["reported_model"] == "kimi-k3")
    check("MoMA chat 使用 /v1 路径", calls[0][1].endswith("/v1/chat/completions"))
    check("MoMA image 使用独立图像路径", image["url"].endswith("image.png")
          and calls[1][1].endswith("/v1/images/generations"))


def main():
    with tempfile.TemporaryDirectory() as tmp:
        db.DB_PATH = str(Path(tmp) / "test.db")
        db.init_db()
        client = TestClient(app)
        content = package_zip()
        r = client.post("/api/projects/import/yuke", files={"file": ("lesson.zip", content, "application/zip")})
        check("ZIP 导入返回 200", r.status_code == 200)
        if r.status_code != 200:
            print(r.text)
            client.close()
            return
        result = r.json()
        pid = result["project_id"]
        check("导入两镜且不发起模型任务", result["shots"] == 2 and db.query("model_tasks") == [])
        graph = client.get(f"/api/projects/{pid}/graph").json()
        check("创建脚本、分镜与连线", len(graph["nodes"]) == 2 and len(graph["edges"]) == 1)
        storyboard = next(n for n in graph["nodes"] if n["type"] == "storyboard")
        shots = storyboard["outputs"]["storyboard"]["shots"]
        check("来源版本绑定", storyboard["outputs"]["storyboard"]["sourceVersion"] == "blueprint-v7")
        check("制作方式保留", shots[0]["sourceMethod"] == "slide" and shots[1]["sourceMethod"] == "teacher")
        check("路线与断言待复核", all(s["route_status"] == "needs_review" and s["assertion_status"] == "needs_review"
                                      for s in shots))
        expanded = client.post(f"/api/nodes/{storyboard['id']}/expand_storyboard", json={"domain": "general"})
        check("待复核镜头不能展开付费产线", expanded.status_code == 400)
        project = db.get("projects", pid)
        manifest = json.loads(project["source_manifest"])
        check("项目保存上游清单", manifest["sourceVersion"] == "blueprint-v7"
              and manifest["sources"][0]["id"] == "ppt-1")
        check("不重复存储上游正文", "content" not in manifest["sources"][0])
        check("执行器支持 ark 与 moma", executors._gw({"provider": "ark"}) is executors.ark
              and executors._gw({"provider": "moma"}) is executors.bjmoma)
        check("旧 bjmoma 路由兼容", executors._gw({"provider": "bjmoma"}) is executors.bjmoma)
        check("Agent 提供双供应商选项", {m["provider"] for m in AGENT_MODELS} == {"ark", "moma"})
        bad = client.post("/api/projects/import/yuke", files={"file": ("bad.zip", b"not-a-zip", "application/zip")})
        check("非法 ZIP 明确拒绝", bad.status_code == 400)
        asyncio.run(test_moma_adapter())
        client.close()
        gc.collect()


if __name__ == "__main__":
    main()
    print(f"\n通过 {len(PASS)}/{len(PASS) + len(FAIL)}")
    sys.exit(1 if FAIL else 0)
