"""节点执行器：按节点类型把任务派发到对应适配器，并管理任务记录与素材落盘。

节点状态机：idle -> running -> succeeded / failed
视频任务状态机：created -> provider_running -> downloading -> succeeded / failed
"""
import asyncio
import base64
import json
import os
import re
import subprocess
from pathlib import Path

import yaml

from . import db
from .config import ASSETS_DIR, QC, QC_RULES_DIR, route
from .gateway.ark import ArkAdapter

ark = ArkAdapter()

# ── 提示词外置：backend/prompts/*.md，改文件即生效（mtime 热加载） ──
from .config import BACKEND_DIR
PROMPTS_DIR = BACKEND_DIR / "prompts"
_prompt_cache: dict = {}


def load_prompt(name: str) -> str:
    p = PROMPTS_DIR / f"{name}.md"
    m = p.stat().st_mtime
    cached = _prompt_cache.get(name)
    if not cached or cached[0] != m:
        _prompt_cache[name] = (m, p.read_text(encoding="utf-8").strip())
    return _prompt_cache[name][1]



TIME_WORDS = ("依次", "逐渐", "逐段", "闪烁", "流动", "浮现", "变化", "残影",
              "渐变", "切换", "先后", "动态", "过程", "闪现", "蒙太奇",
              "摆动", "同步", "加速", "自动", "回流", "随人物", "随着")


def _normalize_shots(sb: dict) -> list[str]:
    """分镜代码强制层：断言分层兜底、帧预算(≤3要素)、图内文字剥离进 caption。"""
    notes: list[str] = []
    for shot in sb.get("shots", []):
        # 断言规范化：字符串→对象；含时间词的强制归 video
        norm = []
        for a in shot.get("assertions") or []:
            if isinstance(a, str):
                a = {"text": a, "phase": "frame"}
            text = str(a.get("text", ""))
            phase = a.get("phase", "frame")
            if phase == "frame" and any(w in text for w in TIME_WORDS):
                phase = "video"
                notes.append(f"镜头{shot.get('index')}: 时间性断言归入视频阶段「{text[:20]}…」")
            norm.append({"text": text, "phase": phase})
        shot["assertions"] = norm
        # 帧预算：要素超 3 截断
        fe = [str(x) for x in (shot.get("frame_elements") or [])][:3]
        if len(shot.get("frame_elements") or []) > 3:
            notes.append(f"镜头{shot.get('index')}: 帧要素超预算，截为前3项")
        shot["frame_elements"] = fe
        # 图内文字剥离：提示词中引号内容移入 caption
        cap = str(shot.get("caption") or "")
        for key in ("first_frame_prompt", "last_frame_delta"):
            text = shot.get(key) or ""
            quoted = re.findall(r"['\"‘’“”]([^'\"‘’“”]{2,40})['\"‘’“”]", text)
            for qtxt in quoted:
                if re.search(r"[A-Za-z一-鿿]", qtxt):
                    cap = (cap + " " + qtxt).strip()
                    text = text.replace(f'"{qtxt}"', "").replace(f"'{qtxt}'", "")
                    notes.append(f"镜头{shot.get('index')}: 图内文字「{qtxt[:16]}」移入字幕层")
            for w in ("labeled", "label", "text saying", "with text", "标注", "字样", "写着"):
                if w in text:
                    text = text.replace(w, "")
            shot[key] = text
        shot["caption"] = cap
    return notes


_CAP_CACHE: dict = {"mtime": 0.0, "data": None}


def load_capability_map() -> dict:
    """能力地图热加载（mtime 缓存，与 load_prompt 同款机制）。"""
    f = BACKEND_DIR / "capability_map.yaml"
    if not f.exists():
        return {}
    mt = f.stat().st_mtime
    if _CAP_CACHE["data"] is None or mt > _CAP_CACHE["mtime"]:
        _CAP_CACHE["data"] = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        _CAP_CACHE["mtime"] = mt
    return _CAP_CACHE["data"]


def capability_brief() -> str:
    """能力地图一行摘要（注入 Agent / 分镜上下文用）。"""
    caps = (load_capability_map() or {}).get("capabilities") or {}
    lows = [f"{k}({v.get('note','')[:14]})" for k, v in caps.items()
            if v.get("tier") == "LOW"]
    if not lows:
        return ""
    return "【模型能力地图·低可靠区】" + "；".join(lows[:6]) + \
           "——规划分镜时避开：拓扑图走代码渲染、亮度渐变交视频、画面文字走字幕字卡、精确数量留容差"


def _route_shots(sb: dict) -> list[str]:
    """MAAO 分镜期确定性路由：按 capability_map.yaml 的规则改写/注记分镜。
    在生成发生之前避开已知低可靠路径（Route Regret 归零的关键一步）。"""
    rules = (load_capability_map() or {}).get("routing_rules") or []
    notes: list[str] = []
    for shot in sb.get("shots", []):
        idx = shot.get("index")
        advice_list = list(shot.get("route_advice") or [])
        for rule in rules:
            action = rule.get("action")
            applies = rule.get("applies_to", "")
            hit = False
            if applies == "last_frame_delta":
                text = str(shot.get("last_frame_delta") or "")
                hit = any(w in text for w in (rule.get("detect_any") or []))
            elif applies == "first_frame_prompt":
                text = str(shot.get("first_frame_prompt") or "")
                hit = any(w in text for w in (rule.get("detect_any") or []))
            elif applies == "first_frame_prompt_and_assertions":
                text = str(shot.get("first_frame_prompt") or "") + " " + " ".join(
                    str(a.get("text", a) if isinstance(a, dict) else a)
                    for a in (shot.get("assertions") or []))
                hit = any(w in text for w in (rule.get("detect_any") or []))
            elif applies == "assertions":
                pat = rule.get("detect_regex")
                if pat:
                    for a in shot.get("assertions") or []:
                        at = str(a.get("text", "") if isinstance(a, dict) else a)
                        if re.search(pat, at):
                            hit = True
                            break
            if not hit:
                continue
            if action == "move_delta_to_video":
                delta = str(shot.get("last_frame_delta") or "").strip()
                if delta:
                    # 分镜视频运动字段为 motion；delta 移入后清空 → expand 自然走单帧驱动
                    vp = str(shot.get("motion") or "").strip()
                    shot["motion"] = (vp + ("；" if vp else "") + f"画面渐变过程：{delta}").strip()
                    shot["last_frame_delta"] = ""
                    shot["single_frame"] = True
            elif action == "relax_count":
                pat = rule.get("detect_regex")
                for a in shot.get("assertions") or []:
                    if isinstance(a, dict) and pat and re.search(pat, str(a.get("text", ""))):
                        if "±1" not in a["text"]:
                            a["text"] = a["text"] + "（数量允许±1，构图完整优先）"
            # advise 类只记注记
            msg = f"镜头{idx}: [{rule.get('id')}] {rule.get('advice', '')}"
            if msg not in notes:
                notes.append(msg)
            advice_list.append(rule.get("id"))
        if advice_list:
            shot["route_advice"] = advice_list
    return notes


def _project_style(project_id: str) -> str:
    """项目美术风格锚（projects.style），空串表示未设置。"""
    proj = db.get("projects", project_id)
    return str((proj or {}).get("style") or "").strip()


def _lint_prompts(sb: dict) -> list[str]:
    """图像歧义词代码层替换（词表在 qc_rules/prompt_lint.yaml，不进提示词）。"""
    f = QC_RULES_DIR / "prompt_lint.yaml"
    if not f.exists():
        return []
    rules = (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("replacements", [])
    notes: list[str] = []
    for shot in sb.get("shots", []):
        for key in ("first_frame_prompt", "last_frame_delta"):
            text = shot.get(key) or ""
            for rule in rules:
                pat = r"\b" + re.escape(str(rule["find"])) + r"\b"
                if re.search(pat, text, flags=re.I):
                    text = re.sub(pat, str(rule["use"]), text, flags=re.I)
                    notes.append(f"镜头{shot.get('index')}: {rule['find']}→{rule['use']}")
            shot[key] = text
    return notes


async def _coach(tgt_in: dict, fails: list, is_pair: bool) -> str:
    """裁判与教练分离：修正建议由独立文本调用生成，消除裁判自评偏差。"""
    r = route("script")
    ctx = {"is_pair": is_pair,
           "edit_delta": tgt_in.get("edit_delta") or "",
           "prompt": tgt_in.get("prompt") or "",
           "fails": [{"id": x.get("id"), "evidence": x.get("evidence")} for x in fails[:6]]}
    data, _ = await _chat_json(r["model"], [
        {"role": "system", "content": load_prompt("coach_system")},
        {"role": "user", "content": json.dumps(ctx, ensure_ascii=False)},
    ])
    return str(data.get("suggested_prompt") or "").strip()[:500]





async def execute_node(node_id: str) -> None:
    node = db.get("canvas_nodes", node_id)
    if not node:
        raise ValueError(f"节点不存在: {node_id}")
    db.update("canvas_nodes", node_id, {"status": "running", "updated_at": db.now()})
    try:
        handler = {
            "script": _run_script,
            "storyboard": _run_storyboard,
            "image": _run_image,
            "video": _run_video,
            "code_render": _run_code_render,
            "compose": _run_compose,
            "qc": _run_qc,
            "ref_video": _run_ref_video,
            "enhance": _run_enhance,
        }.get(node["type"])
        if not handler:
            raise ValueError(f"暂不支持的节点类型: {node['type']}")
        outputs = await handler(node)
        db.update("canvas_nodes", node_id, {
            "status": "succeeded", "outputs": json.dumps(outputs, ensure_ascii=False),
            "updated_at": db.now(),
        })
    except Exception as e:
        msg = str(e).strip() or repr(e)  # httpx 超时类异常 str() 可能为空
        infra_kw = ("网络异常", "Timeout", "Connect", "Overloaded", "overloaded",
                    "下载失败", "503", "500", "ServerBusy")
        err_class = "infra" if any(k in msg for k in infra_kw) else "content"
        db.update("canvas_nodes", node_id, {
            "status": "failed",
            "outputs": json.dumps({"error": msg, "error_class": err_class},
                                  ensure_ascii=False),
            "updated_at": db.now(),
        })
        raise


async def _chat_json(model: str, messages: list[dict],
                     max_tokens: int = 8192) -> tuple[dict, dict]:
    """带韧性的 JSON 模式调用：空返回/非法 JSON 自动重试一次，剥 markdown 围栏。
    返回 (解析后的对象, 原始响应)。"""
    last = ""
    for attempt in range(2):
        msgs = [dict(m) for m in messages]
        if attempt > 0:
            note = "（上次输出为空或不是合法 JSON，请严格只输出一个完整的 JSON 对象）"
            c = msgs[-1]["content"]
            msgs[-1]["content"] = (c + [{"type": "text", "text": note}]
                                   if isinstance(c, list) else f"{c}\n\n{note}")
        resp = await ark.chat(model, msgs, json_mode=True, max_tokens=max_tokens)
        text = (resp.get("text") or "").strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        try:
            return json.loads(text), resp
        except Exception as e:
            last = f"{e}；模型返回前100字: {text[:100]!r}"
    raise RuntimeError(f"模型两次未返回有效 JSON（可重跑本节点或换模型）。{last}")


def _record_task(node: dict, task_type: str, r: dict, payload: dict,
                 provider_task_id: str | None = None) -> str:
    task_id = db.new_id("task")
    db.insert("model_tasks", {
        "id": task_id, "project_id": node["project_id"], "node_id": node["id"],
        "provider": r["provider"], "model": r["model"], "task_type": task_type,
        "status": "created", "provider_task_id": provider_task_id,
        "request_payload": json.dumps(payload, ensure_ascii=False),
        "created_at": db.now(),
    })
    return task_id


async def _run_script(node: dict) -> dict:
    inputs = db.jloads(node["inputs"])
    r = route("script")
    payload = {"goal": inputs.get("goal", ""), "duration": inputs.get("duration", 90)}
    task_id = _record_task(node, "text_generation", r, payload)
    data, resp = await _chat_json(r["model"], [
        {"role": "system", "content": load_prompt("script_system")},
        {"role": "user", "content": f"知识点：{payload['goal']}\n目标总时长：约 {payload['duration']} 秒"},
    ])
    db.update("model_tasks", task_id, {
        "status": "succeeded", "finished_at": db.now(),
        "input_tokens": resp["input_tokens"], "output_tokens": resp["output_tokens"],
        "response_payload": json.dumps({"text": resp["text"]}, ensure_ascii=False),
    })
    return {"script": data}


async def _run_storyboard(node: dict) -> dict:
    inputs = db.jloads(node["inputs"])
    script = inputs.get("script")
    if not script:
        script = _upstream_output(node, "script")
    style = _project_style(node["project_id"])
    # 用户硬约束：镜头数 / 总时长（约束是数据不是恳求；每镜上限10秒=视频模型物理限制）
    want_n = int(inputs.get("shot_count") or 0)
    want_t = int(inputs.get("total_duration") or 0)
    constraint = ""
    if want_n or want_t:
        parts = []
        if want_n:
            parts.append(f"镜头数必须恰好为 {want_n} 个")
        if want_t:
            parts.append(f"全片总时长约 {want_t} 秒")
        parts.append("每个镜头时长只能取 5 或 10 秒（视频模型单段上限 10 秒），"
                     "时长不够就增加镜头数量来凑，禁止出现超过 10 秒的镜头")
        constraint = chr(10) + "【硬性要求】" + "；".join(parts)
    r = route("storyboard")
    task_id = _record_task(node, "text_generation", r, {"script": script})
    user_msg = (json.dumps(script, ensure_ascii=False)
                + (chr(10) + "【项目美术风格锚】" + style + "——所有 first_frame_prompt 必须显式包含此风格描述" if style else "")
                + constraint)
    data, resp = await _chat_json(r["model"], [
        {"role": "system", "content": load_prompt("storyboard_system")},
        {"role": "user", "content": user_msg},
    ])
    # 镜头数校验：不符则带着纠错说明重试一次（架构级保障，不指望模型一次听话）
    if want_n and len((data or {}).get("shots", [])) != want_n:
        got = len((data or {}).get("shots", []))
        data, resp = await _chat_json(r["model"], [
            {"role": "system", "content": load_prompt("storyboard_system")},
            {"role": "user", "content": user_msg
             + chr(10) + f"【纠错】上次你生成了 {got} 个镜头，不符合要求，必须恰好 {want_n} 个。"},
        ])
    db.update("model_tasks", task_id, {
        "status": "succeeded", "finished_at": db.now(),
        "input_tokens": resp["input_tokens"], "output_tokens": resp["output_tokens"],
        "response_payload": json.dumps({"text": resp["text"]}, ensure_ascii=False),
    })
    notes = _normalize_shots(data) + _lint_prompts(data) + _route_shots(data)
    if want_n and len(data.get("shots", [])) != want_n:
        notes.append(f"⚠ 两次生成均未达到要求的 {want_n} 镜（实际 {len(data.get('shots', []))} 镜），请人工裁决")
    out = {"storyboard": data}
    if notes:
        out["lint"] = notes
    return out


EDIT_PREFIX = ("Edit the reference image with exactly one change, described below. "
               "The result must be identical to the reference image in every other way: "
               "same scene, same camera angle, same layout, same lighting, same art style, "
               "and the exact same set of elements and text as the reference. The change: ")
EDIT_SUFFIX = (" The ONLY difference from the reference image is this requested change; "
               "everything else remains pixel-faithful to the reference.")


async def _run_image(node: dict) -> dict:
    inputs = db.jloads(node["inputs"])
    # 参考图：显式 URL 优先；否则 ref_node 运行时解析（尾帧引用首帧成品图的关键）
    ref = (inputs.get("ref_asset_url") or "").strip()
    ref_node = str(inputs.get("ref_node") or "").strip()
    if not ref and ref_node:
        rn = db.get("canvas_nodes", ref_node)
        ref = (db.jloads(rn["outputs"]).get("asset_url") or "") if rn else ""
        if not ref:
            raise ValueError("参考帧节点还没有成品图——请先把首帧跑成功再跑本节点")
    # R1/R2 锚定直用：参考帧（真实关键帧）直接作为本节点成品，零生成费、零幻觉
    if str(inputs.get("use_ref_as_output") or "") in ("是", "1", "true") and ref:
        src_p = ASSETS_DIR / ref.split("/assets/")[-1]
        if not src_p.exists():
            raise ValueError("参考帧文件不存在")
        aid = db.new_id("asset")
        fn = f"{aid}{src_p.suffix}"
        import shutil as _sh
        _sh.copyfile(src_p, ASSETS_DIR / fn)
        db.insert("assets", {"id": aid, "project_id": node["project_id"],
                             "node_id": node["id"], "kind": "image",
                             "filename": fn, "created_at": db.now()})
        return {"asset_id": aid, "asset_url": f"/assets/{fn}",
                "engine": "ref_anchor", "note": "真实参考帧直用（零成本锚定）"}
    delta = (inputs.get("edit_delta") or "").strip()
    delta_note = ""
    if delta and not ref:
        # 没显式参考图时，自动找上游已完成的图像节点当参考
        for up in _upstream_nodes(node):
            if up["type"] in ("image", "code_render") and up["status"] == "succeeded":
                au = (db.jloads(up["outputs"]).get("asset_url") or "")
                if au and not au.endswith(".mp4"):
                    ref = au
                    delta_note = f"自动以上游「{up['title'] or up['type']}」的成果为参考图"
                    break
    if delta and ref:
        # 编辑式生成：以参考图为基准做最小变化，短指令，禁复述场景
        prompt = f"{EDIT_PREFIX}{delta}.{EDIT_SUFFIX}"
    elif delta:
        # 无任何参考图：优雅降级为文生图，把变化并入场景描述（不报错拦人）
        base = (inputs.get("prompt") or "").strip()
        prompt = (base + "\n" if base else "") + f"Depict the end state: {delta}"
        delta_note = "无参考图，变化指令已并入文生图提示词"
    else:
        prompt = (inputs.get("prompt") or "").strip()
    if not prompt:
        # 自动接力：从上游分镜节点取第 shot_index 镜的首帧提示词
        sb = _upstream_output(node, "storyboard")
        idx = max(1, int(inputs.get("shot_index") or 1))
        shots = sb.get("shots", [])
        if not shots:
            raise ValueError("上游分镜没有镜头数据")
        shot = shots[min(idx, len(shots)) - 1]
        prompt = shot.get("first_frame_prompt") or shot.get("last_frame_delta", "")
    style = _project_style(node["project_id"])
    if style and not delta and style not in prompt:
        prompt = f"{prompt}, overall art style: {style}"
    size = inputs.get("size", "2560x1440")
    refs = None
    if ref:
        refs = [_asset_to_data_uri(ref) if ref.startswith("/assets/") else ref]
    # 编辑式走专用编辑通道（Seedream 5.0-pro，实测可执行光效编辑）；文生图走生成通道
    from .config import ROUTES
    r = route("image_edit") if (delta and "image_edit" in ROUTES) else route("image")
    task_id = _record_task(node, "image_generation", r,
                           {"prompt": prompt, "size": size, "ref": ref or None})
    # 编辑式：生成后自动一致性预检，不合格自动重抽（机内循环代替人肉循环）
    precheck_on = bool(delta and refs and QC.get("edit_precheck", True))
    rqc = route("qc_vision") if precheck_on else {}
    precheck_on = precheck_on and bool(rqc.get("model"))
    max_attempts = max(1, int(QC.get("edit_max_attempts", 2))) if precheck_on else 1
    total_tokens = 0
    filename = ""
    precheck_note = ""
    for attempt in range(max_attempts):
        resp = await ark.image(r["model"], prompt, size, ref_images=refs)
        total_tokens += resp["output_tokens"]
        asset_id = db.new_id("asset")
        filename = f"{asset_id}.jpg"
        await ark.download(resp["url"], str(ASSETS_DIR / filename))
        if not precheck_on:
            break
        pr, _ = await _chat_json(rqc["model"], [
            {"role": "system", "content":
                load_prompt("edit_precheck_system")},
            {"role": "user", "content": [
                {"type": "text", "text": f"编辑指令：{delta}"},
                {"type": "image_url", "image_url": {"url": refs[0]}},
                {"type": "image_url", "image_url": {"url": _asset_to_data_uri(f'/assets/{filename}')}},
            ]},
        ])
        if pr.get("consistent", True) and pr.get("change_applied", True):
            precheck_note = f"预检通过（第{attempt + 1}次生成）"
            break
        precheck_note = (f"预检第{attempt + 1}次不合格：{str(pr.get('reason', ''))[:80]}"
                         + ("，已自动重抽" if attempt + 1 < max_attempts else "，已达重试上限，保留末次结果"))
        (ASSETS_DIR / filename).unlink(missing_ok=True)
        if attempt + 1 >= max_attempts:
            filename = f"{asset_id}.jpg"  # 重试耗尽：重新落盘末次结果
            await ark.download(resp["url"], str(ASSETS_DIR / filename))
    db.insert("assets", {
        "id": filename[:-4], "project_id": node["project_id"], "node_id": node["id"],
        "kind": "image", "filename": filename, "created_at": db.now(),
    })
    db.update("model_tasks", task_id, {
        "status": "succeeded", "finished_at": db.now(),
        "output_tokens": total_tokens,
    })
    out = {"asset_id": filename[:-4], "asset_url": f"/assets/{filename}"}
    if precheck_note:
        out["precheck"] = precheck_note
    if delta_note:
        out["note"] = delta_note
    return out




async def _run_video(node: dict) -> dict:
    inputs = db.jloads(node["inputs"])
    # ── 首尾帧配对预检：两帧不属于同一世界就拒跑，省下注定失败的生成费 ──
    first_ref = str(inputs.get("first_frame_node") or "").strip()
    last_ref = str(inputs.get("last_frame_node") or "").strip()
    skip_pair = str(inputs.get("skip_pair_check") or "").strip() in ("1", "true", "是")
    if first_ref and last_ref and not skip_pair:
        rqc = route("qc_vision")
        if rqc.get("model"):
            urls = []
            for ref in (first_ref, last_ref):
                fn = db.get("canvas_nodes", ref)
                au = db.jloads(fn["outputs"]).get("asset_url") if fn else None
                if au:
                    urls.append(au)
            if len(urls) == 2:
                # 尾帧的编辑指令属刻意要求的变化，预检不得视为不一致
                _lf = db.get("canvas_nodes", last_ref)
                _delta = str((db.jloads(_lf["inputs"]) if _lf else {}).get("edit_delta") or "").strip()
                _delta_note = (f"图1=首帧，图2=尾帧。尾帧的既定编辑指令如下——凡属该指令要求的变化"
                               f"（含亮度/颜色/状态/新增元素）都是刻意的，一律不算不一致：{_delta}"
                               ) if _delta else "图1=首帧，图2=尾帧。"
                pr, _ = await _chat_json(rqc["model"], [
                    {"role": "system", "content": load_prompt("pair_check_system")},
                    {"role": "user", "content": [
                        {"type": "text", "text": _delta_note},
                        {"type": "image_url", "image_url": {"url": _asset_to_data_uri(urls[0])}},
                        {"type": "image_url", "image_url": {"url": _asset_to_data_uri(urls[1])}},
                    ]},
                ])
                if not pr.get("consistent", True):
                    raise ValueError(
                        "配对预检不通过（首尾帧不属于同一场景/风格，直接生成必然穿帮）："
                        + str(pr.get("reason", ""))[:160]
                        + "。修正建议：" + str(pr.get("fix", ""))[:200]
                        + "。统一两帧并复检后再跑视频。")
    # 提示词缺省时自动接力：上游参考视频节点的运动特征卡
    if not (inputs.get("prompt") or "").strip():
        for up in _upstream_nodes(node):
            card = db.jloads(up["outputs"]).get("motion_card")
            if card and card.get("generation_prompt_en"):
                inputs["prompt"] = card["generation_prompt_en"]
                break
    if not (inputs.get("prompt") or "").strip():
        raise ValueError("缺少运镜提示词（或连接一个已分析完成的参考视频节点自动取）")
    r = route("video")
    payload = {k: inputs.get(k) for k in
               ("prompt", "first_frame_url", "last_frame_url", "resolution", "duration")}
    payload["prompt"] = inputs.get("prompt")
    task_id = _record_task(node, "video_generation", r, payload)
    resolution = inputs.get("resolution", "480p")
    supported = r.get("resolutions")
    if supported and resolution not in supported:
        resolution = supported[-1]  # 超出模型能力时降到最高支持档
    provider_task_id = await ark.video_create(
        r["model"], inputs["prompt"],
        first_frame_url=_resolve_frame(node, inputs, "first_frame_url"),
        last_frame_url=_resolve_frame(node, inputs, "last_frame_url"),
        resolution=resolution,
        duration=int(inputs.get("duration", 5)),
    )
    db.update("model_tasks", task_id,
              {"status": "provider_running", "provider_task_id": provider_task_id})
    # 轮询直至完成（方舟侧一般 1-3 分钟）；瞬时网络错误不算任务失败
    poll_errors = 0
    for _ in range(120):
        try:
            data = await ark.video_get(provider_task_id)
            poll_errors = 0
        except Exception:
            poll_errors += 1
            if poll_errors >= 5:
                raise
            await asyncio.sleep(5)
            continue
        status = data.get("status")
        if status == "succeeded":
            db.update("model_tasks", task_id, {"status": "downloading"})
            asset_id = db.new_id("asset")
            filename = f"{asset_id}.mp4"
            await ark.download(data["content"]["video_url"], str(ASSETS_DIR / filename))
            db.insert("assets", {
                "id": asset_id, "project_id": node["project_id"], "node_id": node["id"],
                "kind": "video", "filename": filename,
                "meta": json.dumps({"resolution": data.get("resolution"),
                                    "duration": data.get("duration")}),
                "created_at": db.now(),
            })
            db.update("model_tasks", task_id, {
                "status": "succeeded", "finished_at": db.now(),
                "output_tokens": data.get("usage", {}).get("completion_tokens", 0),
            })
            return {"asset_id": asset_id, "asset_url": f"/assets/{filename}"}
        if status in ("failed", "cancelled"):
            raise RuntimeError(f"视频任务{status}: {data.get('error', '')}")
        await asyncio.sleep(5)
    raise TimeoutError("视频任务超时（10 分钟）")


async def _run_code_render(node: dict) -> dict:
    """程序化渲染：光路等严格几何动画，本地计算，零 API 费用。"""
    inputs = db.jloads(node["inputs"])
    template = inputs.get("template", "lens_focus")
    if template not in ("lens_focus", "pwm_waveform", "spectrum_recipe",
                        "block_diagram", "rotary_drill_station"):
        raise ValueError(f"暂不支持模板 {template}（可扩展 app/render/）")
    r = {"provider": "local", "model": f"code_render/{template}"}
    task_id = _record_task(node, "code_render", r, inputs)
    asset_id = db.new_id("asset")
    filename = f"{asset_id}.mp4"
    if template == "rotary_drill_station":
        from .render import rotary_drill_station
        meta = await asyncio.to_thread(
            rotary_drill_station.render, str(ASSETS_DIR / filename),
            phase=str(inputs.get("phase") or "cycle"),
            duration=float(inputs.get("duration") or 18),
            fps=int(inputs.get("fps") or 24),
        )
    elif template == "spectrum_recipe":
        from .render import spectrum_recipe
        meta = await asyncio.to_thread(
            spectrum_recipe.render, str(ASSETS_DIR / filename),
            stage=inputs.get("stage") or "育苗期",
            duty_blue=float(inputs.get("duty_blue") or 40),
            duty_red=float(inputs.get("duty_red") or 50),
            duty_farred=float(inputs.get("duty_farred") or 10),
            duration=float(inputs.get("duration") or 11),
            fps=int(inputs.get("fps") or 24),
        )
    elif template == "block_diagram":
        from .render import block_diagram
        meta = await asyncio.to_thread(
            block_diagram.render, str(ASSETS_DIR / filename),
            duration=float(inputs.get("duration") or 12),
            fps=int(inputs.get("fps") or 24),
        )
    elif template == "pwm_waveform":
        from .render import pwm_waveform
        if inputs.get("duty_from") is not None or inputs.get("duty_to") is not None:
            # 占空比阶梯扫描：低→高分档演示，覆盖"占空比增大→更亮"类断言
            meta = await asyncio.to_thread(
                pwm_waveform.render_sweep, str(ASSETS_DIR / filename),
                duty_from=float(inputs.get("duty_from") or 25),
                duty_to=float(inputs.get("duty_to") or 90),
                steps=int(inputs.get("steps") or 3),
                duration=float(inputs.get("duration") or 12),
                fps=int(inputs.get("fps") or 24),
            )
        else:
            meta = await asyncio.to_thread(
                pwm_waveform.render, str(ASSETS_DIR / filename),
                duty=float(inputs.get("duty") or 50),
                duration=float(inputs.get("duration") or 10),
                fps=int(inputs.get("fps") or 24),
            )
    else:
        from .render import lens_focus
        meta = await asyncio.to_thread(
            lens_focus.render, str(ASSETS_DIR / filename),
            focal_length=float(inputs.get("focal_length") or 2.2),
            num_rays=int(inputs.get("num_rays") or 7),
            duration=float(inputs.get("duration") or 6),
        )
    db.insert("assets", {
        "id": asset_id, "project_id": node["project_id"], "node_id": node["id"],
        "kind": "video", "filename": filename,
        "meta": json.dumps(meta), "created_at": db.now(),
    })
    db.update("model_tasks", task_id, {"status": "succeeded", "finished_at": db.now()})
    return {"asset_id": asset_id, "asset_url": f"/assets/{filename}",
            "engine": "code_render", "geometry_checked": True}


def _ancestors(node: dict) -> list[dict]:
    """全部上游祖先节点（BFS）。"""
    out, seen, queue = [], {node["id"]}, [node]
    while queue:
        for up in _upstream_nodes(queue.pop(0)):
            if up["id"] not in seen:
                seen.add(up["id"])
                out.append(up)
                queue.append(up)
    return out


def _has_audio(path: str) -> bool:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a",
                        "-show_entries", "stream=codec_type", "-of", "csv=p=0", path],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return "audio" in r.stdout


def _build_srt(script: dict) -> str:
    def ts(sec: float) -> str:
        h, m = divmod(int(sec), 3600)
        m, s = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d},{int(sec % 1 * 1000):03d}"
    lines, t = [], 0.0
    for seg in script.get("segments", []):
        dur = float(seg.get("seconds", 5))
        lines.append(f"{seg['index']}\n{ts(t)} --> {ts(t + dur)}\n{seg['narration']}\n")
        t += dur
    return "\n".join(lines)


async def _run_compose(node: dict) -> dict:
    """合成节点：按镜头号（回退按画布行序）拼接上游视频，可烧录上游脚本的字幕。本地 ffmpeg，零费用。"""
    inputs = db.jloads(node["inputs"])

    def _clip_order(n: dict):
        # 逐镜产线节点标题带"镜头N"，按镜头号排；否则按行优先(y,x)。
        # 不能按 x 优先：code_render 与视频节点 x 不同，会打乱镜头顺序
        m = re.search(r"镜头(\d+)", n.get("title") or "")
        if m:
            return (0, int(m.group(1)), n["position_y"], n["position_x"])
        return (1, 0, n["position_y"], n["position_x"])

    ups = sorted(_upstream_nodes(node), key=_clip_order)
    videos, captions, qc_flags = [], [], []
    for u in ups:
        out = db.jloads(u["outputs"])
        au = out.get("asset_url", "")
        if u["status"] == "succeeded" and au.endswith(".mp4"):
            videos.append(str(ASSETS_DIR / au.split("/assets/")[-1]))
            captions.append(str(db.jloads(u["inputs"]).get("caption") or "").strip())
            qc = out.get("qc", {})
            if qc.get("verdict") in ("reject", "needs_human"):
                qc_flags.append(f"{u.get('title') or u['type']}: 质检{'不合格' if qc['verdict']=='reject' else '待人工确认'}")
    if not videos:
        raise ValueError("没有可合成的上游视频（先把视频/代码渲染节点连进来并跑成功）")
    # ── 质检控制阀 ──
    if qc_flags and str(QC.get("mode", "advisory")) == "strict":
        raise ValueError("质检阀拦截（strict 模式）：" + "；".join(qc_flags)
                         + "。请修复或在质检节点上人工放行后再合成")

    r = {"provider": "local", "model": "ffmpeg/compose"}
    task_id = _record_task(node, "compose", r, {"videos": len(videos)})
    asset_id = db.new_id("asset")
    filename = f"{asset_id}.mp4"
    out = str(ASSETS_DIR / filename)

    def _concat() -> None:
        n = len(videos)
        with_audio = all(_has_audio(v) for v in videos)
        args = ["ffmpeg", "-y"]
        for v in videos:
            args += ["-i", v]
        # 跨平台中文字体：Windows 用微软雅黑，Linux 用 Noto CJK（apt: fonts-noto-cjk）
        if os.name == "nt":
            _font = "C\\:/Windows/Fonts/msyh.ttc"
        else:
            _font = next((p for p in (
                "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
                "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc") if Path(p).exists()), "")

        def _cap_filter(i: int) -> str:
            cap = captions[i] if i < len(captions) else ""
            if not cap or not _font:
                return ""
            safe = cap.replace(chr(92), "").replace("'", "").replace(":", " ").replace("%", " ")[:40]
            return (",drawtext=fontfile='" + _font + "':text='" + safe
                    + "':x=(w-text_w)/2:y=42:fontsize=44:fontcolor=white:borderw=3:bordercolor=black@0.7")
        filt = "".join(
            f"[{i}:v]scale=1280:720:force_original_aspect_ratio=decrease,"
            f"pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24{_cap_filter(i)}[v{i}];"
            for i in range(n))
        if with_audio:
            filt += "".join(f"[v{i}][{i}:a]" for i in range(n))
            filt += f"concat=n={n}:v=1:a=1[v][a]"
            args += ["-filter_complex", filt, "-map", "[v]", "-map", "[a]"]
        else:
            filt += "".join(f"[v{i}]" for i in range(n))
            filt += f"concat=n={n}:v=1:a=0[v]"
            args += ["-filter_complex", filt, "-map", "[v]"]
        args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", out]
        rr = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if rr.returncode != 0:
            raise RuntimeError(f"ffmpeg 拼接失败: {rr.stderr[-300:]}")

    await asyncio.to_thread(_concat)

    # 字幕：先沿连线找脚本，找不到再查同项目任意已完成的脚本节点
    note = ""
    if str(inputs.get("burn_subtitles", "是")) == "是":
        script = None
        for anc in _ancestors(node):
            s = db.jloads(anc["outputs"]).get("script")
            if s:
                script = s
                break
        if not script:
            for n2 in db.query("canvas_nodes", "project_id=?", (node["project_id"],)):
                if n2["type"] == "script" and n2["status"] == "succeeded":
                    s = db.jloads(n2["outputs"]).get("script")
                    if s:
                        script = s
                        break
        if script:
            srt_name = f"{asset_id}.srt"
            (ASSETS_DIR / srt_name).write_text(_build_srt(script), encoding="utf-8")
            burned = f"{asset_id}_sub.mp4"

            def _burn() -> None:
                rr = subprocess.run(
                    ["ffmpeg", "-y", "-i", filename,
                     "-vf", f"subtitles={srt_name}:force_style="
                            f"'FontName=Microsoft YaHei,FontSize=18,"
                            f"PrimaryColour=&HFFFFFF&,OutlineColour=&H80000000&'",
                     "-c:a", "copy", burned],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ASSETS_DIR))
                if rr.returncode != 0:
                    raise RuntimeError(rr.stderr[-200:])

            try:
                await asyncio.to_thread(_burn)
                (ASSETS_DIR / filename).unlink()
                (ASSETS_DIR / burned).rename(ASSETS_DIR / filename)
                note = "已烧录字幕"
            except Exception:
                note = "字幕烧录失败，输出无字幕版"
        else:
            note = "上游没有脚本节点，未加字幕"

    # BGM：合成舒缓垫乐并混入（本地 ffmpeg，零费用）
    if str(inputs.get("bgm") or "无") != "无":
        def _bgm() -> None:
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(ASSETS_DIR / filename)],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            dur = max(1.0, float(probe.stdout.strip() or 10))
            fade_out = max(0.0, dur - 3)
            bgm_f = str(ASSETS_DIR / f"{asset_id}_bgm.m4a")
            af = ("[0][1][2][3]amix=inputs=4:duration=longest,"
                  "tremolo=f=0.2:d=0.5,lowpass=f=1500,aecho=0.8:0.9:900:0.25,"
                  f"volume=0.4,afade=t=in:st=0:d=2,afade=t=out:st={fade_out}:d=3[a]")
            rr = subprocess.run(
                ["ffmpeg", "-y",
                 "-f", "lavfi", "-i", f"sine=frequency=220:duration={dur}",
                 "-f", "lavfi", "-i", f"sine=frequency=261.63:duration={dur}",
                 "-f", "lavfi", "-i", f"sine=frequency=329.63:duration={dur}",
                 "-f", "lavfi", "-i", f"sine=frequency=110:duration={dur}",
                 "-filter_complex", af, "-map", "[a]",
                 "-c:a", "aac", "-b:a", "160k", bgm_f],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            if rr.returncode != 0:
                raise RuntimeError(rr.stderr[-200:])
            mixed = str(ASSETS_DIR / f"{asset_id}_mix.mp4")
            src = str(ASSETS_DIR / filename)
            if _has_audio(src):
                args = ["ffmpeg", "-y", "-i", src, "-i", bgm_f, "-filter_complex",
                        "[0:a][1:a]amix=inputs=2:duration=first:weights=1 0.6[a]",
                        "-map", "0:v", "-map", "[a]", "-c:v", "copy",
                        "-c:a", "aac", "-b:a", "160k", mixed]
            else:
                args = ["ffmpeg", "-y", "-i", src, "-i", bgm_f,
                        "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                        "-c:a", "aac", "-b:a", "160k", "-shortest", mixed]
            rr = subprocess.run(args, capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
            if rr.returncode != 0:
                raise RuntimeError(rr.stderr[-200:])
            Path(src).unlink()
            Path(mixed).rename(src)
            Path(bgm_f).unlink(missing_ok=True)

        try:
            await asyncio.to_thread(_bgm)
            note = (note + "；" if note else "") + "已混入 BGM"
        except Exception as e:
            note = (note + "；" if note else "") + f"BGM 混入失败:{str(e)[:80]}"

    db.insert("assets", {
        "id": asset_id, "project_id": node["project_id"], "node_id": node["id"],
        "kind": "video", "filename": filename,
        "meta": json.dumps({"clips": len(videos)}), "created_at": db.now(),
    })
    db.update("model_tasks", task_id, {"status": "succeeded", "finished_at": db.now()})
    if qc_flags:
        note = (note + "；" if note else "") + "⚠ 质检警示（advisory 放行）：" + "；".join(qc_flags)
    return {"asset_id": asset_id, "asset_url": f"/assets/{filename}",
            "clips": len(videos), "note": note}




async def _run_ref_video(node: dict) -> dict:
    """参考视频分析：抽帧→视觉模型提取运动特征卡（合规：只学运动，不碰肖像）。
    视频来源：本节点上传的视频优先，否则取上游视频节点成果。"""
    inputs = db.jloads(node["inputs"])
    own = db.jloads(node["outputs"]).get("asset_url", "")
    src = own if own.endswith(".mp4") else ""
    if not src:
        for up in _upstream_nodes(node):
            au = db.jloads(up["outputs"]).get("asset_url", "")
            if up["status"] == "succeeded" and au.endswith(".mp4"):
                src = au
                break
    if not src:
        raise ValueError("请先在本节点上传参考视频（或连接一个已完成的视频节点）")
    r = route("ref_video")
    if not r.get("model"):
        raise ValueError("providers.yaml 未配置 ref_video 视觉模型")
    frames = _extract_qc_frames(node["id"], str(ASSETS_DIR / src.split("/assets/")[-1]), 8)
    task_id = _record_task(node, "ref_video_analysis", r, {"frames": len(frames)})
    focus = inputs.get("focus") or "整体运动轨迹与镜头语言"
    content: list[dict] = [{"type": "text", "text":
        f"分析重点：{focus}。以下是参考视频按时间顺序的 {len(frames)} 张抽帧："}]
    for f in frames:
        content.append({"type": "image_url",
                        "image_url": {"url": _asset_to_data_uri(f'/assets/{f}')}})
    card, resp = await _chat_json(r["model"], [
        {"role": "system", "content": load_prompt("ref_video_system")},
        {"role": "user", "content": content},
    ])
    db.update("model_tasks", task_id, {
        "status": "succeeded", "finished_at": db.now(),
        "input_tokens": resp["input_tokens"], "output_tokens": resp["output_tokens"]})
    out = {"motion_card": card, "frames": [f"/assets/{f}" for f in frames]}
    if own:
        out["asset_url"] = own  # 保留已上传的参考视频

    # ── 阶段1：场景切分 → 每段关键帧+片段素材 → 逐段复刻卡 ──
    src_path = str(ASSETS_DIR / src.split("/assets/")[-1])
    segs = _scene_segments(src_path)
    seg_out = []
    for si, (t0, t1) in enumerate(segs, start=1):
        dur = t1 - t0
        # 关键帧：段首/段中/段末
        kf_urls = []
        for tag, ts in (("a", t0 + min(0.3, dur / 4)), ("b", (t0 + t1) / 2),
                        ("c", max(t0, t1 - min(0.3, dur / 4)))):
            aid = db.new_id("asset")
            fn = f"{aid}.jpg"
            subprocess.run(["ffmpeg", "-y", "-ss", f"{ts:.2f}", "-i", src_path,
                            "-frames:v", "1", "-q:v", "3", str(ASSETS_DIR / fn)],
                           capture_output=True)
            if (ASSETS_DIR / fn).exists():
                db.insert("assets", {"id": aid, "project_id": node["project_id"],
                                     "node_id": node["id"], "kind": "image",
                                     "filename": fn, "created_at": db.now()})
                kf_urls.append(f"/assets/{fn}")
        # 段片段（R1 真实素材增强的原料）
        cid = db.new_id("asset")
        cfn = f"{cid}.mp4"
        subprocess.run(["ffmpeg", "-y", "-ss", f"{t0:.2f}", "-t", f"{dur:.2f}",
                        "-i", src_path, "-c:v", "libx264", "-preset", "veryfast",
                        "-crf", "22", "-an", str(ASSETS_DIR / cfn)], capture_output=True)
        clip_url = ""
        if (ASSETS_DIR / cfn).exists():
            db.insert("assets", {"id": cid, "project_id": node["project_id"],
                                 "node_id": node["id"], "kind": "video",
                                 "filename": cfn, "created_at": db.now()})
            clip_url = f"/assets/{cfn}"
        # 逐段复刻卡（科学事实断言是核心产出；按学科提取指引抓重点）
        focus_lines = _load_extract_focus(str(inputs.get("domain") or "general"))
        focus_txt = ("\n【本学科重点提取】\n- " + "\n- ".join(focus_lines)) if focus_lines else ""
        card_content: list[dict] = [{"type": "text", "text":
            f"这是参考视频第 {si} 段（{dur:.1f} 秒）的首/中/末三帧：{focus_txt}"}]
        for u in kf_urls:
            card_content.append({"type": "image_url",
                                 "image_url": {"url": _asset_to_data_uri(u)}})
        seg_card, seg_resp = await _chat_json(r["model"], [
            {"role": "system", "content": load_prompt("ref_replica_system")},
            {"role": "user", "content": card_content},
        ])
        seg_out.append({"index": si, "start": round(t0, 2), "end": round(t1, 2),
                        "seconds": round(dur, 1), "clip_url": clip_url,
                        "keyframes": kf_urls, "card": seg_card})
    out["segments"] = seg_out
    # ── 音频转写（本地 Whisper，零费用）+ 素材体检报告 ──
    transcript = await asyncio.to_thread(_transcribe, src_path)
    if transcript:
        for s in seg_out:   # 讲稿按时间窗切给各段
            s["speech"] = "".join(x["text"] for x in transcript["lines"]
                                  if x["start"] < s["end"] and x["end"] > s["start"])
        out["transcript"] = transcript
    out["intake"] = _intake_report(seg_out, transcript)
    return out


_WHISPER_MODEL = None


def _transcribe(path: str) -> dict | None:
    """本地 Whisper 转写（零 API 费）。无音轨或未装 faster-whisper 时返回 None。"""
    pr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a",
                         "-show_entries", "stream=codec_type", "-of", "csv=p=0", path],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if "audio" not in (pr.stdout or ""):
        return None
    try:
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    global _WHISPER_MODEL
    if _WHISPER_MODEL is None:
        _WHISPER_MODEL = WhisperModel("small", device="cpu", compute_type="int8")
    segments, info = _WHISPER_MODEL.transcribe(path, language="zh", vad_filter=True)
    lines = [{"start": round(s.start, 1), "end": round(s.end, 1), "text": s.text.strip()}
             for s in segments]
    return {"language": info.language, "lines": lines,
            "text": "".join(x["text"] for x in lines)}


def _load_playbook() -> dict:
    p = QC_RULES_DIR / "replication_playbook.yaml"
    if p.exists():
        return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("kinds", {})
    return {}


def _intake_report(seg_out: list[dict], transcript: dict | None) -> dict:
    """素材体检：定性素材类型并给出打法库建议（讨论产出制度化的机器端）。"""
    n = max(1, len(seg_out))
    faces = sum(1 for s in seg_out if (s.get("card") or {}).get("has_faces"))
    words = len((transcript or {}).get("text") or "")
    if faces / n >= 0.6 and words > 150:
        kind = "talking_head"
    elif faces / n <= 0.2 and words > 150:
        kind = "narrated_demo"
    elif faces / n <= 0.2:
        kind = "demo_experiment"
    else:
        kind = "mixed"
    entry = _load_playbook().get(kind, {})
    return {"kind": kind, "label": entry.get("label", kind),
            "faces_ratio": round(faces / n, 2), "speech_chars": words,
            "default_route": entry.get("default_route", ""),
            "rationale": entry.get("rationale", ""),
            "moves": entry.get("moves", []), "rights": entry.get("rights", [])}


def _scene_segments(path: str, max_segs: int = 10) -> list[tuple[float, float]]:
    """ffmpeg 场景切分：scdet 找切点；无明显切点时按 8 秒等分。段长下限 2 秒。"""
    dur = 0.0
    pr = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                         "-of", "csv=p=0", path], capture_output=True, text=True,
                        encoding="utf-8", errors="replace")
    try:
        dur = float((pr.stdout or "0").strip())
    except ValueError:
        dur = 0.0
    if dur <= 0:
        raise ValueError("无法读取参考视频时长")
    sc = subprocess.run(["ffmpeg", "-i", path, "-vf",
                         "select='gt(scene,0.30)',showinfo", "-f", "null", "-"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    cuts = [float(m) for m in re.findall(r"pts_time:([0-9.]+)", sc.stderr or "")]
    bounds = [0.0] + [c for c in cuts if 0.5 < c < dur - 0.5] + [dur]
    segs: list[tuple[float, float]] = []
    for a, b in zip(bounds, bounds[1:]):
        if segs and b - segs[-1][0] < 2.0:      # 过短并入前段
            segs[-1] = (segs[-1][0], b)
        elif b - a < 2.0 and segs:
            segs[-1] = (segs[-1][0], b)
        else:
            segs.append((a, b))
    if len(segs) <= 1 and dur > 12:             # 无切点：按 8 秒等分
        n = min(max_segs, max(2, int(dur // 8)))
        step = dur / n
        segs = [(i * step, (i + 1) * step) for i in range(n)]
    return segs[:max_segs]


async def _run_enhance(node: dict) -> dict:
    """真实素材增强（R1 路线）：慢放/区域特写/标注框/画中画，本地 ffmpeg 零 API 费。
    素材来源：inputs.source_url，否则取上游节点（参考视频段/视频/代码渲染）的 mp4 成果。"""
    inputs = db.jloads(node["inputs"])
    src = str(inputs.get("source_url") or "").strip()
    if not src:
        for up in _upstream_nodes(node):
            out_u = db.jloads(up["outputs"])
            au = out_u.get("asset_url", "")
            if up["status"] == "succeeded" and au.endswith(".mp4"):
                src = au
                break
            segs = out_u.get("segments") or []
            si = int(inputs.get("segment_index") or 1)
            if up["type"] == "ref_video" and segs:
                src = segs[min(si, len(segs)) - 1].get("clip_url", "")
                if src:
                    break
    if not src:
        raise ValueError("请填 source_url 或连接参考视频/视频/代码渲染节点（可用 segment_index 选段）")
    src_path = str(ASSETS_DIR / src.split("/assets/")[-1])
    task_id = _record_task(node, "enhance", {"provider": "local", "model": "ffmpeg/enhance"}, inputs)

    vf: list[str] = []
    zoom = str(inputs.get("zoom_region") or "").strip()     # "x,y,w,h" 百分比
    if zoom:
        try:
            zx, zy, zw, zh = [max(0.0, min(100.0, float(v))) / 100 for v in zoom.split(",")]
            vf.append(f"crop=iw*{zw:.3f}:ih*{zh:.3f}:iw*{zx:.3f}:ih*{zy:.3f},scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2")
        except (ValueError, IndexError):
            raise ValueError("zoom_region 格式应为 x,y,w,h 百分比，例：25,25,50,50")
    slow = float(inputs.get("slow_factor") or 1)
    if slow > 1:
        vf.append(f"setpts={slow:.2f}*PTS")
    box = str(inputs.get("label_box") or "").strip()
    label = str(inputs.get("label_text") or "").strip()
    if box:
        try:
            bx, by, bw, bh = [max(0.0, min(100.0, float(v))) / 100 for v in box.split(",")]
            vf.append(f"drawbox=x=iw*{bx:.3f}:y=ih*{by:.3f}:w=iw*{bw:.3f}:h=ih*{bh:.3f}:color=yellow@0.9:t=4")
        except (ValueError, IndexError):
            raise ValueError("label_box 格式应为 x,y,w,h 百分比")
    if label:
        if os.name == "nt":
            _font = "C\\:/Windows/Fonts/msyh.ttc"
        else:
            _font = next((p for p in (
                "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc") if Path(p).exists()), "")
        safe = label.replace(chr(92), "").replace("'", "").replace(":", " ")[:40]
        if _font:
            vf.append(f"drawtext=fontfile='{_font}':text='{safe}':x=(w-text_w)/2:y=36:"
                      f"fontsize=40:fontcolor=yellow:borderw=3:bordercolor=black@0.7")

    asset_id = db.new_id("asset")
    filename = f"{asset_id}.mp4"
    out_path = str(ASSETS_DIR / filename)
    args = ["ffmpeg", "-y", "-i", src_path]
    pip = str(inputs.get("pip_url") or "").strip()
    if pip:
        pip_path = str(ASSETS_DIR / pip.split("/assets/")[-1])
        chain = ",".join(vf) if vf else "null"
        args += ["-i", pip_path, "-filter_complex",
                 f"[0:v]{chain}[base];[1:v]scale=iw*0.3:-1[pip];"
                 f"[base][pip]overlay=W-w-24:H-h-24[v]",
                 "-map", "[v]"]
    elif vf:
        args += ["-vf", ",".join(vf)]
    args += ["-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", out_path]
    pr = subprocess.run(args, capture_output=True, text=True,
                        encoding="utf-8", errors="replace")
    if pr.returncode != 0 or not Path(out_path).exists():
        raise ValueError("增强处理失败：" + (pr.stderr or "")[-300:])
    db.insert("assets", {"id": asset_id, "project_id": node["project_id"],
                         "node_id": node["id"], "kind": "video",
                         "filename": filename, "created_at": db.now()})
    db.update("model_tasks", task_id, {"status": "succeeded", "finished_at": db.now()})
    return {"asset_id": asset_id, "asset_url": f"/assets/{filename}",
            "engine": "enhance", "source": src}




def _load_rules(domain: str) -> list[dict]:
    rules: list[dict] = []
    for name in dict.fromkeys(["general", domain]):
        p = QC_RULES_DIR / f"{name}.yaml"
        if p.exists():
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            rules += data.get("rules", [])
    return rules


def _load_ref_rules(domain: str, cap: int = 9) -> list[dict]:
    """保真对照规则：通用层+学科层，从规则包读取（判卷注意力有限，总量封顶）。"""
    rules: list[dict] = []
    for name in dict.fromkeys(["general", domain]):
        p = QC_RULES_DIR / f"{name}.yaml"
        if p.exists():
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            rules += data.get("ref_rules", [])
    return rules[:cap]


def _load_extract_focus(domain: str) -> list[str]:
    """复刻卡提取指引：分析参考视频时按学科清单抓科学事实。"""
    p = QC_RULES_DIR / f"{domain}.yaml"
    if p.exists():
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return [str(x) for x in data.get("ref_extract_focus", [])]
    return []


def _extract_qc_frames(node_id: str, asset_path: str, n: int) -> list[str]:
    """从视频均匀抽 n 帧存入素材目录，返回文件名列表；图片直接返回自身。"""
    if not asset_path.endswith(".mp4"):
        return [asset_path.split("/")[-1].split("\\")[-1]]
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", asset_path], capture_output=True, text=True, encoding="utf-8", errors="replace")
    dur = float(probe.stdout.strip() or 5)
    names = []
    for i in range(n):
        t = dur * (i + 0.5) / n
        name = f"qcframe_{node_id}_{i}.jpg"
        subprocess.run(["ffmpeg", "-y", "-ss", str(t), "-i", asset_path,
                        "-frames:v", "1", "-q:v", "3", str(ASSETS_DIR / name)],
                       capture_output=True)
        names.append(name)
    return names


async def _run_qc(node: dict) -> dict:
    """质检节点：抽帧 → 规则+断言组卷 → 视觉裁判逐条裁决 → 控制阀分流。
    未配置视觉模型时降级为人工验收模式（出帧出卷，等人裁决）。"""
    inputs = db.jloads(node["inputs"])
    domain = inputs.get("domain", "optics")
    # 1) 被检对象：最近的已完成上游素材节点
    target = next((u for u in _upstream_nodes(node)
                   if u["type"] in ("image", "video", "code_render", "compose")
                   and u["status"] == "succeeded"), None)
    if not target:
        raise ValueError("请把质检节点连到已完成的图像/视频节点后面")
    asset_url = db.jloads(target["outputs"]).get("asset_url", "")
    asset_path = str(ASSETS_DIR / asset_url.split("/assets/")[-1])
    # 2) 抽帧
    frames = _extract_qc_frames(node["id"], asset_path,
                                int(QC.get("frames_per_video", 5)))
    frame_urls = [f"/assets/{f}" for f in frames]
    # 3) 组卷：领域规则 + 镜头断言（inputs 直存优先——分镜展开产线写入，
    #    不依赖连线拓扑；缺省时回退为沿连线上溯分镜节点提取）
    rules = _load_rules(domain)
    raw_asserts = list(inputs.get("assertions") or [])
    # 首帧质检（image 目标且非配对模式）不考"最后帧/尾帧必须…"类断言
    _is_first_frame = (target["type"] == "image"
                       and not str(inputs.get("pair_first_node") or "").strip())
    # 兼容新旧格式：字符串或 {text, phase}；帧类被检对象只考 phase=frame 的断言
    assertions: list[str] = []
    for a in raw_asserts:
        text = str(a.get("text", "")) if isinstance(a, dict) else str(a)
        if isinstance(a, dict):
            if target["type"] in ("image",) and a.get("phase") == "video":
                continue
        else:
            # 旧格式字符串断言无 phase 字段：含时间词且被检对象是静态图时同样跳过
            if target["type"] in ("image",) and any(w in text for w in TIME_WORDS):
                continue
        if _is_first_frame and ("最后帧" in text or "尾帧" in text or "最终" in text):
            continue
        assertions.append(text)
    if not assertions:
        for anc in _ancestors(node):
            sb = db.jloads(anc["outputs"]).get("storyboard")
            if sb:
                idx = max(1, int(inputs.get("shot_index") or 1))
                shots = sb.get("shots", [])
                if shots:
                    assertions = shots[min(idx, len(shots)) - 1].get("assertions", []) or []
                break
    pair_first = str(inputs.get("pair_first_node") or "").strip()
    is_pair = bool(pair_first) and target["type"] == "image"
    if is_pair:
        # ── 配对模式（尾帧）：专用判卷，聚焦三件事；装饰细节一律降级为 warning ──
        checklist = [
            {"id": "PAIR-01", "name": "与首帧同一世界", "severity": "blocker",
             "check": "尾帧与首帧必须是同一场景、同一机位（不得镜像/换边）、同一美术风格、"
                      "同一批主体与布局（家具/窗户/灯具的位置和数量一致）",
             "on_fail": "用编辑式短指令以首帧为基准重新生成"},
            {"id": "PAIR-02", "name": "要求的变化已发生", "severity": "blocker",
             "check": "尾帧相对首帧应发生的指定变化（见断言/原始指令）确实出现且方向正确",
             "on_fail": "变化指令更具体一句话，仍以首帧为基准"},
            {"id": "PAIR-03", "name": "无破坏性瑕疵", "severity": "blocker",
             "check": "无结构崩坏、穿模、肢体畸形、大面积涂抹感等破坏画面可用性的生成瑕疵",
             "on_fail": "重抽或人工放行"},
            {"id": "GEN-02", "name": "文字与首帧一致且零错字", "severity": "blocker",
             "check": "本条只审画面内的【文字内容】：首帧已有的文字原样保留（不算违规）、"
                      "不得新增首帧没有的文字、出现的文字无错字乱码。"
                      "灯光、颜色、明暗、材质等一切非文字差异不属于本条范围（由 PAIR 条款负责）",
             "on_fail": "以首帧文字为准重新编辑"},
            {"id": "GEN-04", "name": "无真实人脸", "severity": "blocker",
             "check": "不得出现照片级真实人脸", "on_fail": "改示意/剪影/背影"},
        ]
        checklist += [{"id": f"AST-{i+1}", "name": a, "severity": "warning",
                       "check": a, "on_fail": ""} for i, a in enumerate(assertions)]
    else:
        checklist = [{"id": r["id"], "name": r["name"], "severity": r["severity"],
                      "check": r["check"], "on_fail": r.get("on_fail", "")}
                     for r in rules]
        checklist += [{"id": f"AST-{i+1}", "name": a, "severity": "blocker",
                       "check": a, "on_fail": "改提示词重跑或转 code_render"}
                      for i, a in enumerate(assertions)]

    r = route("qc_vision")
    if not r.get("model"):
        # ── 人工验收模式 ──
        outputs = {"mode": "manual", "verdict": "needs_human",
                   "frames": frame_urls, "checklist": checklist,
                   "note": "未配置视觉裁判模型（providers.yaml 的 qc_vision），请对照帧图逐条人工验收后点『人工放行/判不合格』"}
    else:
        # ── 自动裁决模式 ──
        task_id = _record_task(node, "qc_vision", r, {"domain": domain, "frames": len(frames)})
        tgt_in = db.jloads(target["inputs"])
        orig_prompt = str(tgt_in.get("prompt") or "").strip()
        tgt_delta = str(tgt_in.get("edit_delta") or "").strip()
        # 保真对照模式：附参考基准帧，保真规则从规则包读取（通用层+学科层，老师可改 yaml）
        ref_frames = [str(u) for u in (inputs.get("ref_frames") or []) if u]
        if ref_frames:
            checklist.extend(_load_ref_rules(domain))
        ask = ("被检素材的抽帧图如下（按时间顺序）。请对以下每条规则/断言逐条裁决：\n"
               + json.dumps(checklist, ensure_ascii=False))
        style = _project_style(node["project_id"])
        if style and target["type"] != "code_render":
            # 代码渲染是刻意的 2D 工程图表通道，不受美术风格锚约束
            ask += "\n【项目美术风格锚】" + style + "——风格一致性按此判定，与该风格相符即为合规"
        elif target["type"] == "code_render":
            ask += "\n【说明】被检素材是程序化渲染的 2D 工程图表（刻意选择的表现形式），不适用美术风格类要求，风格类规则一律判 pass"
        fe = [str(x) for x in (inputs.get("frame_elements") or [])]
        if fe and target["type"] == "image":
            ask += ("\n\n【帧要素清单——判定『画面与要求一致』只按此清单核验】\n"
                    + json.dumps(fe, ensure_ascii=False)
                    + "\n画面包含清单要素即视为一致，不要求逐字匹配提示词的其他描述。")
        if is_pair and tgt_delta:
            ask += (f"\n\n【本次编辑指令——PAIR-02『变化到位』的唯一判定标准】\n{tgt_delta}\n"
                    "注意：只按这条指令判定变化是否到位；镜头断言仅作参考信息；"
                    "任何时间性/顺序性效果（如'依次点亮''逐渐浮现''流动'）不适用于静态帧，"
                    "不得据此判 fail。")
        elif orig_prompt and target["type"] in ("image", "video"):
            ask += f"\n\n【原始生成提示词】\n{orig_prompt}"
        # 尾帧质检时附同镜首帧：建议提示词必须以首帧世界为基准，否则修出来也配不上对
        first_node_ref = str(inputs.get("pair_first_node") or "").strip()
        if target["type"] == "image" and first_node_ref:
            fn = db.get("canvas_nodes", first_node_ref)
            fau = db.jloads(fn["outputs"]).get("asset_url") if fn else None
            if fau:
                ask += ("\n\n【参考】最后一张附图是同一镜头【已确认的首帧】。被检素材是【配对尾帧】，"
                        "请对照首帧实图逐条裁决 PAIR 规则（同一世界/变化到位/无破坏性瑕疵）。")
        # 视频由首尾帧插值生成时，把两帧也交给裁判：便于区分"视频的错"与"帧的错"
        pair_imgs: list[str] = []
        if target["type"] == "image" and first_node_ref:
            fn = db.get("canvas_nodes", first_node_ref)
            fau = db.jloads(fn["outputs"]).get("asset_url") if fn else None
            if fau:
                pair_imgs.append(fau)
        if target["type"] == "video":
            for k in ("first_frame_node", "last_frame_node"):
                ref = str(tgt_in.get(k) or "").strip()
                fn = db.get("canvas_nodes", ref) if ref else None
                au = db.jloads(fn["outputs"]).get("asset_url") if fn else None
                if au:
                    pair_imgs.append(au)
            if len(pair_imgs) == 2:
                ask += ("\n\n【参考】该视频由随后附上的最后两张图（首帧、尾帧）插值生成。"
                        "若问题源于首尾帧本身场景/风格不一致，请在 summary 中明确指出"
                        "'病根在帧'并说明应统一哪张帧；suggested_prompt 仍针对视频提示词，"
                        "且不得与首尾帧的实际画面相矛盾。")
        if ref_frames:
            ask += (f"\n\n【保真对照】最后 {len(ref_frames)} 张图是参考视频的基准帧"
                    "——REF 系列规则以它们为准绳裁决")
        content: list[dict] = [{"type": "text", "text": ask}]
        for f in frames:
            content.append({"type": "image_url", "image_url":
                            {"url": _asset_to_data_uri(f"/assets/{f}")}})
        for pu in pair_imgs:
            content.append({"type": "image_url", "image_url":
                            {"url": _asset_to_data_uri(pu)}})
        for ru in ref_frames:
            content.append({"type": "image_url", "image_url":
                            {"url": _asset_to_data_uri(ru)}})
        report, resp = await _chat_json(r["model"], [
            {"role": "system", "content": load_prompt("qc_judge_system")},
            {"role": "user", "content": content},
        ])
        results = report.get("results", [])
        thr = float(QC.get("confidence_threshold", 0.7))
        by_id = {c["id"]: c for c in checklist}
        blockers, warnings, uncertains = [], [], []
        for it in results:
            c = by_id.get(it.get("id"), {})
            sev = c.get("severity", "warning")
            v = it.get("verdict")
            if v == "uncertain" or (v == "fail" and float(it.get("confidence", 1)) < thr):
                uncertains.append(it)
            elif v == "fail" and sev == "blocker":
                it["on_fail"] = c.get("on_fail", "")
                blockers.append(it)
            elif v == "fail":
                warnings.append(it)
        verdict = ("reject" if blockers else
                   "needs_human" if uncertains else "pass")
        suggested = ""
        remediation = "retry"
        if verdict == "reject":
            fcf = QC_RULES_DIR / "fail_classes.yaml"
            words = ((yaml.safe_load(fcf.read_text(encoding="utf-8")) or {})
                     .get("cosmetic_words", []) if fcf.exists() else [])

            def _cosmetic(it: dict) -> bool:
                blob = str(it.get("evidence", "")) + str(it.get("id", ""))
                return any(w in blob for w in words)

            if blockers and all(_cosmetic(b) for b in blockers):
                remediation = "human"  # 纯光效/颜色类：不烧重抽钱，直接人工目测
        if verdict == "reject" and remediation == "retry" and target["type"] in ("image", "video"):
            try:
                suggested = await _coach(tgt_in, blockers + warnings, is_pair)
            except Exception:
                suggested = ""
        db.update("model_tasks", task_id, {
            "status": "succeeded", "finished_at": db.now(),
            "input_tokens": resp["input_tokens"], "output_tokens": resp["output_tokens"]})
        outputs = {"mode": "auto", "verdict": verdict, "frames": frame_urls,
                   "results": results, "blockers": blockers, "warnings": warnings,
                   "uncertains": uncertains, "summary": report.get("summary", ""),
                   "suggested_prompt": suggested,
                   "remediation": remediation,
                   "checklist": checklist}
    # 4) 熔断：同一被检节点连续不合格 2 次即停，给出三条出路，不再无限烧钱
    tgt_out = db.jloads(target["outputs"])
    prev_fails = int((tgt_out.get("qc") or {}).get("fail_count") or 0)
    fail_count = prev_fails + 1 if outputs["verdict"] == "reject" else 0
    if outputs["verdict"] == "reject" and fail_count >= 2:
        outputs["circuit_breaker"] = True
        outputs["summary"] = (str(outputs.get("summary", ""))
            + f"\n⛔ 已连续 {fail_count} 次质检不合格，熔断建议（三选一）："
              "①改用编辑式短指令（edit_delta，以首帧为基准只写变化）重试；"
              "②瑕疵可接受则点『人工放行』；"
              "③该画面属于图表/严格几何类，转 code_render 或后期叠加，放弃 AI 重绘。")
    # 5) 结论写回被检节点（合成阀门在那里查）
    tgt_out["qc"] = {"verdict": outputs["verdict"], "qc_node_id": node["id"],
                     "fail_count": fail_count}
    db.update("canvas_nodes", target["id"],
              {"outputs": json.dumps(tgt_out, ensure_ascii=False)})
    outputs["target_node_id"] = target["id"]
    # 6) MAAO 证据流：verdict + capability_id 回写被检节点最近一次生成任务台账
    try:
        tgt_in = db.jloads(target["inputs"])
        if target["type"] == "image":
            cap = "image_edit" if str(tgt_in.get("edit_delta") or "").strip() else "image_t2i"
        elif target["type"] == "video":
            cap = "video_gen"
        elif target["type"] == "code_render":
            cap = "code_render"
        else:
            cap = target["type"]
        rows = db.query("model_tasks",
                        "node_id=? AND status='succeeded' ORDER BY created_at DESC LIMIT 1",
                        (target["id"],))
        if rows:
            db.update("model_tasks", rows[0]["id"],
                      {"verdict": outputs["verdict"], "capability_id": cap})
    except Exception:
        pass  # 证据回写失败不影响质检主流程
    return outputs




async def run_agent(project_id: str, message: str, model: str | None = None,
                    research: bool = False,
                    history: list[dict] | None = None) -> dict:
    """Agent 对话：增量式协作搭画布。带对话历史与画布现状上下文；research=True 先联网核实事实。"""
    from .config import AGENT_MODELS
    allowed_models = {m["model"] for m in AGENT_MODELS}
    if model and model in allowed_models:
        r = {"provider": "ark", "model": model}
    else:
        r = ({"provider": "ark", "model": AGENT_MODELS[0]["model"]}
             if AGENT_MODELS else route("script"))

    # ── 画布现状（增量协作的关键上下文）──
    existing = db.query("canvas_nodes", "project_id=? ORDER BY position_x", (project_id,))
    proj_style = _project_style(project_id)
    canvas_ctx = ([{"id": n["id"], "type": n["type"], "title": n["title"],
                    "status": n["status"]} for n in existing]
                  if existing else "（画布为空）")

    # ── 对话历史 ──
    hist_lines = []
    for h in (history or [])[-8:]:
        role = "用户" if h.get("role") == "user" else "助理"
        hist_lines.append(f"{role}: {str(h.get('text', ''))[:300]}")
    hist_ctx = "\n".join(hist_lines) or "（无）"

    # ── 联网调研（仅核实真实事件；无可核实内容时明确跳过）──
    research_note = ""
    research_block = ""
    if research:
        try:
            rr = route("research")
            facts = await ark.research(
                rr["model"],
                "下面是一条微课创作对话中的用户消息。若其中涉及需要联网核实的真实事件/人物/数据，"
                "请联网调研并输出简洁事实要点（时间、对象、关键动作、数据）；"
                "若不涉及任何真实事件（只是操作指令或一般知识点），只输出四个字：无需调研。"
                f"\n用户消息：{message}")
            text = facts["text"].strip()
            if text and not text.startswith("无需调研") and "无需调研" not in text[:20]:
                research_note = text[:1500]
                research_block = f"\n\n【调研材料（已联网核实）】\n{research_note}"
        except Exception as e:
            research_note = f"联网调研失败：{str(e)[:120]}"

    style_ctx = f"【项目美术风格锚】{proj_style}" + chr(10) + chr(10) if proj_style else ""
    cap_brief = capability_brief()
    cap_ctx = cap_brief + chr(10) + chr(10) if cap_brief else ""
    user_content = (style_ctx + cap_ctx
                    + f"【画布现状】{json.dumps(canvas_ctx, ensure_ascii=False)}\n\n"
                    f"【对话历史】\n{hist_ctx}\n\n"
                    f"【用户本轮消息】{message}{research_block}")

    plan = None
    last_err = ""
    for attempt in range(2):  # 模型偶发输出非 JSON 时自动重试一次
        resp = await ark.chat(r["model"], [
            {"role": "system", "content": load_prompt("agent_system")},
            {"role": "user", "content": user_content if attempt == 0 else
             user_content + "\n\n（上次输出不是合法 JSON，请严格只输出 JSON 对象，不要任何其他文字）"},
        ], json_mode=True)
        try:
            text = resp["text"].strip()
            # 容错：剥掉可能的 markdown 代码块包裹
            if text.startswith("```"):
                text = text.split("```")[1]
                text = text[4:] if text.startswith("json") else text
            plan = json.loads(text)
            break
        except Exception as e:
            last_err = f"{e}: {resp['text'][:100]}"
    if plan is None:
        raise RuntimeError(f"规划模型两次都未返回有效 JSON，请换个说法或换个模型重试。{last_err}")

    allowed = {"enhance", "script", "storyboard", "image", "video", "code_render",
               "compose", "qc", "ref_video"}
    existing_ids = {n["id"] for n in existing}
    base_x = max([n["position_x"] or 0 for n in existing], default=-220) + 300
    key2id: dict[str, str] = {}
    created = 0
    created_meta: list[tuple[str, str, str, dict]] = []  # (nid, type, title, inputs)
    lint_notes: list[str] = []
    for i, nd in enumerate(plan.get("nodes", [])):
        if nd.get("type") not in allowed:
            lint_notes.append(f"忽略未知节点类型「{nd.get('type')}」")
            continue
        nid = db.new_id("node")
        key2id[str(nd.get("key", f"n{i}"))] = nid
        nd_inputs = nd.get("inputs", {}) or {}
        db.insert("canvas_nodes", {
            "id": nid, "project_id": project_id, "type": nd["type"],
            "title": nd.get("title", ""),
            "position_x": base_x + i * 300, "position_y": 220 + (i % 2) * 40,
            "inputs": json.dumps(nd_inputs, ensure_ascii=False),
            "outputs": "{}", "status": "idle",
            "created_at": db.now(), "updated_at": db.now(),
        })
        created_meta.append((nid, nd["type"], nd.get("title", ""), nd_inputs))
        created += 1

    def _resolve(ref: str) -> str | None:
        return key2id.get(ref) or (ref if ref in existing_ids else None)

    edge_pairs: list[tuple[str, str]] = []
    for e in plan.get("edges", []):
        if len(e) == 2:
            src, dst = _resolve(str(e[0])), _resolve(str(e[1]))
            if src and dst and src != dst:
                db.insert("canvas_edges", {
                    "id": db.new_id("edge"), "project_id": project_id,
                    "source_node_id": src, "target_node_id": dst,
                    "source_handle": "output", "target_handle": "input",
                })
                edge_pairs.append((src, dst))
            else:
                lint_notes.append(f"忽略无法解析的连线 {e}")

    # ── 计划校验器（代码级断言，替代提示词铁律）──
    all_types = {n["id"]: n["type"] for n in existing}
    all_types.update({nid: t for nid, t, _, _ in created_meta})
    old_edges = db.query("canvas_edges", "project_id=?", (project_id,))
    all_edges = [(x["source_node_id"], x["target_node_id"]) for x in old_edges]
    if sum(1 for _, t, _, _ in created_meta if t == "script") > 1:
        lint_notes.append("一次创建了多个脚本节点——通常一部视频只需一个脚本（含多段落），分镜负责拆镜头")
    for nid, t, title, nd_inputs in created_meta:
        ups = [s for s, d in all_edges if d == nid]
        label = title or t
        if t == "storyboard" and not any(all_types.get(s) == "script" for s in ups):
            lint_notes.append(f"「{label}」缺少脚本上游，执行会失败")
        if t == "video" and not str(nd_inputs.get("prompt") or "").strip() \
                and not any(all_types.get(s) == "ref_video" for s in ups):
            lint_notes.append(f"「{label}」没有运镜提示词也没有参考视频上游")
        if t == "qc" and not any(all_types.get(s) in ("image", "video", "code_render", "compose")
                                 for s in ups):
            lint_notes.append(f"「{label}」质检节点未连接被检素材")
        if t == "compose" and not any(all_types.get(s) in ("video", "code_render") for s in ups):
            lint_notes.append(f"「{label}」拼接节点没有视频上游")

    # ── 立即执行用户要求出结果的节点 ──
    ran = 0
    async def _bg_run(nid: str) -> None:
        try:
            await execute_chain(nid)
        except Exception:
            pass  # 失败详情已写入节点
    ran_ids: set[str] = set()
    for ref in plan.get("run", []) or []:
        nid = _resolve(str(ref))
        if nid:
            asyncio.get_event_loop().create_task(_bg_run(nid))
            ran_ids.add(nid)
            ran += 1
        else:
            lint_notes.append(f"忽略无法解析的执行目标「{ref}」")

    # 工作流铁律（代码级保障）：新建的脚本节点必须立即生成内容——
    # 只建壳不出内容，用户在画布上看到的是空节点，无从审阅确认
    for nid, t, _, _ in created_meta:
        if t == "script" and nid not in ran_ids:
            asyncio.get_event_loop().create_task(_bg_run(nid))
            ran += 1

    reply = plan.get("reply", "已处理")
    if lint_notes:
        reply += "\n（系统校验：" + "；".join(lint_notes[:5]) + "）"
    return {"reply": reply, "created": created, "ran": ran,
            "created_ids": [nid for nid, _, _, _ in created_meta],
            "research": research_note,
            "tokens": resp["input_tokens"] + resp["output_tokens"]}


def _upstream_nodes(node: dict) -> list[dict]:
    edges = db.query("canvas_edges", "target_node_id=?", (node["id"],))
    ups = [db.get("canvas_nodes", e["source_node_id"]) for e in edges]
    return [u for u in ups if u]


def _upstream_output(node: dict, key: str):
    """沿画布连线取上游节点的输出（自动串联节点用）。"""
    for up in _upstream_nodes(node):
        out = db.jloads(up["outputs"])
        if key in out:
            return out[key]
    raise ValueError(f"输入缺少 {key}，且上游节点没有该输出")


def _asset_to_data_uri(asset_url: str) -> str:
    """本地素材转 base64 data URI（方舟无法访问 localhost URL）。"""
    filename = asset_url.split("/assets/")[-1]
    data = (ASSETS_DIR / filename).read_bytes()
    mime = "image/png" if filename.endswith(".png") else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def _resolve_frame(node: dict, inputs: dict, key: str) -> str | None:
    """解析首/尾帧：显式 URL 优先；其次是帧节点引用（first_frame_node /
    last_frame_node，由分镜展开产线写入）——引用的帧必须已生成且未被质检
    判死，否则拒绝执行（帧关卡）；最后回退为自动取上游图像节点成果。"""
    v = (inputs.get(key) or "").strip()
    if v:
        return _asset_to_data_uri(v) if v.startswith("/assets/") else v
    ref = str(inputs.get(key.replace("_url", "_node")) or "").strip()
    if ref:
        fn = db.get("canvas_nodes", ref)
        if not fn or fn["status"] != "succeeded":
            raise ValueError(f"帧关卡：{key} 引用的帧节点尚未生成，"
                             "请先跑通并确认该帧（建议用⏭单步）")
        fout = db.jloads(fn["outputs"])
        if (fout.get("qc") or {}).get("verdict") == "reject":
            raise ValueError("帧关卡：引用的帧被质检判不合格，"
                             "请重生成该帧或在质检节点人工放行后再跑视频")
        au = fout.get("asset_url")
        if au:
            return _asset_to_data_uri(au)
        raise ValueError(f"帧关卡：{key} 引用的帧节点没有图像成果")
    if key == "first_frame_url":
        for up in _upstream_nodes(node):
            if up["type"] == "image" and up["status"] == "succeeded":
                au = db.jloads(up["outputs"]).get("asset_url")
                if au:
                    return _asset_to_data_uri(au)
    return None


def upstream_chain(node_id: str) -> list[str]:
    """返回含自身的拓扑序节点列表（上游在前），用于“运行到此节点”。"""
    order: list[str] = []
    seen: set[str] = set()

    def visit(nid: str) -> None:
        if nid in seen:
            return
        seen.add(nid)
        for e in db.query("canvas_edges", "target_node_id=?", (nid,)):
            visit(e["source_node_id"])
        order.append(nid)

    visit(node_id)
    return order


async def execute_chain(node_id: str) -> None:
    """自上游起依次执行：已成功的上游跳过，目标节点总是重跑。"""
    for nid in upstream_chain(node_id):
        n = db.get("canvas_nodes", nid)
        if not n:
            continue
        if nid != node_id and n["status"] == "succeeded":
            continue
        await execute_node(nid)
