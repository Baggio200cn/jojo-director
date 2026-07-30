"""JOJO Studio 后端 API。

启动：cd backend && python -m uvicorn app.main:app --reload --port 8000
"""
import asyncio
import json
import re as _re
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, db, executors
from .config import ASSETS_DIR

app = FastAPI(title="JOJO Studio API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])
ASSETS_DIR.mkdir(exist_ok=True)
app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")
db.init_db()
auth.init()


@app.middleware("http")
async def auth_guard(request: Request, call_next):
    """会话门禁：/api 与 /assets 需登录；付费执行做邀请级限额+全站熔断。"""
    path = request.url.path
    if path.startswith("/api"):
        if path != "/api/health" and not path.startswith("/api/auth/"):
            s = auth.get_session(request)
            if not s:
                return JSONResponse({"detail": "未登录"}, status_code=401)
            request.state.session = s
            if request.method == "POST":
                m = _re.match(r"^/api/nodes/([^/]+)/(execute|execute_chain)$", path)
                if m:
                    node = db.get("canvas_nodes", m.group(1))
                    if node:
                        try:
                            auth.check_and_log(s, node["type"])
                        except HTTPException as e:
                            return JSONResponse({"detail": e.detail}, status_code=e.status_code)
    elif path.startswith("/assets"):
        if not auth.get_session(request):
            return JSONResponse({"detail": "未登录"}, status_code=401)
    return await call_next(request)


class LoginIn(BaseModel):
    username: str = ""
    password: str = ""
    invite_code: str = ""


class InviteIn(BaseModel):
    label: str = ""
    daily_video_limit: int = 3
    daily_cost_limit_cny: float = 10.0


@app.post("/api/auth/login")
def auth_login(body: LoginIn):
    if body.invite_code:
        token = auth.login_invite(body.invite_code)
        role = "invite"
    else:
        token = auth.login_admin(body.username, body.password)
        role = "admin"
    resp = JSONResponse({"ok": True, "role": role})
    resp.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400,
                    httponly=True, samesite="lax")
    return resp


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    auth.logout(request.cookies.get(auth.COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE)
    return resp


@app.get("/api/auth/me")
def auth_me(request: Request):
    s = auth.get_session(request)
    if not s:
        return JSONResponse({"detail": "未登录"}, status_code=401)
    out = {"role": s["role"], "auth_enabled": auth.enabled()}
    if s["role"] == "invite":
        rows = db.query("invite_codes", "code=?", (s["invite_code"],))
        if rows:
            out["limits"] = {"daily_video_limit": rows[0]["daily_video_limit"],
                             "daily_cost_limit_cny": rows[0]["daily_cost_limit_cny"]}
            out["usage_today"] = auth.usage_today(s["invite_code"])
    return out


@app.get("/api/admin/invites")
def admin_list_invites(request: Request):
    auth.require_admin(request)
    rows = db.query("invite_codes", "1=1 ORDER BY created_at DESC")
    for r in rows:
        r["usage_today"] = auth.usage_today(r["code"])
    return rows


@app.post("/api/admin/invites")
def admin_create_invite(body: InviteIn, request: Request):
    auth.require_admin(request)
    import secrets as _secrets
    code = "JOJO-" + _secrets.token_hex(3).upper()
    db.insert("invite_codes", {
        "id": db.new_id("inv"), "code": code, "label": body.label,
        "daily_video_limit": body.daily_video_limit,
        "daily_cost_limit_cny": body.daily_cost_limit_cny,
        "disabled": 0, "created_at": db.now()})
    return db.query("invite_codes", "code=?", (code,))[0]


@app.patch("/api/admin/invites/{code}")
def admin_toggle_invite(code: str, request: Request):
    auth.require_admin(request)
    rows = db.query("invite_codes", "code=?", (code,))
    if not rows:
        raise HTTPException(404, "邀请码不存在")
    with db._conn() as c:
        c.execute("UPDATE invite_codes SET disabled=1-disabled WHERE code=?", (code,))
    return db.query("invite_codes", "code=?", (code,))[0]

# 项目美术风格锚：新增 style 列（已存在则忽略）
with db._conn() as _c:
    try:
        _c.execute("ALTER TABLE projects ADD COLUMN style TEXT DEFAULT ''")
    except Exception:
        pass
    # MAAO 证据流：台账补 verdict / capability_id / estimated 列（已存在则忽略）
    for _sql in ("ALTER TABLE model_tasks ADD COLUMN verdict TEXT",
                 "ALTER TABLE model_tasks ADD COLUMN capability_id TEXT"):
        try:
            _c.execute(_sql)
        except Exception:
            pass

# 启动自愈：上次进程退出时正在执行的节点会永远卡在 running，复位为 failed 以便重跑
with db._conn() as _c:
    _stuck = _c.execute("SELECT COUNT(*) FROM canvas_nodes WHERE status='running'").fetchone()[0]
    if _stuck:
        _c.execute(
            "UPDATE canvas_nodes SET status='failed', "
            "outputs=json_patch(COALESCE(NULLIF(outputs,''),'{}'), "
            "'{\"error\": \"执行因服务重启而中断，请重新运行本节点\"}') "
            "WHERE status='running'")


class ProjectIn(BaseModel):
    title: str


class NodeIn(BaseModel):
    type: str                     # script / storyboard / image / video / code_render
    title: str = ""
    inputs: dict = {}
    position: dict = {"x": 0, "y": 0}


class EdgeIn(BaseModel):
    source_node_id: str
    target_node_id: str


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/projects")
def create_project(p: ProjectIn):
    pid = db.new_id("proj")
    db.insert("projects", {"id": pid, "title": p.title, "status": "draft",
                           "created_at": db.now(), "updated_at": db.now()})
    return db.get("projects", pid)


@app.get("/api/projects")
def list_projects():
    return db.query("projects")


class ProjectPatch(BaseModel):
    style: str | None = None
    title: str | None = None


@app.patch("/api/projects/{pid}")
def patch_project(pid: str, body: ProjectPatch):
    """项目设置：美术风格锚等。风格会注入分镜/图像/质检/Agent 全链。"""
    if not db.get("projects", pid):
        raise HTTPException(404, "项目不存在")
    fields = {}
    if body.style is not None:
        fields["style"] = body.style
    if body.title is not None:
        fields["title"] = body.title
    if fields:
        fields["updated_at"] = db.now()
        db.update("projects", pid, fields)
    return db.get("projects", pid)


@app.get("/api/render/templates")
def render_templates():
    """代码渲染模板注册表（参数 schema，前端动态表单用）。"""
    import yaml as _yaml
    p = Path(__file__).resolve().parent / "render" / "templates.yaml"
    return (_yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("templates", {})


@app.get("/api/projects/{pid}/stats")
def project_stats(pid: str):
    """MAAO 记账报表：分类成本 + Route Regret（被后续重抽废弃的成功生成）。
    惰性结算：顺手把缺失的 estimated_cost_cny 按单价表回填。"""
    from .config import estimate_cost_cny, PRICING
    with db._conn() as c:
        c.row_factory = __import__("sqlite3").Row
        rows = [dict(r) for r in c.execute(
            "SELECT id, node_id, model, task_type, status, verdict, capability_id,"
            "       COALESCE(input_tokens,0) ti, COALESCE(output_tokens,0) tou,"
            "       estimated_cost_cny, created_at"
            " FROM model_tasks WHERE project_id=?", (pid,))]
        # 惰性结算
        for r in rows:
            if r["status"] == "succeeded" and not r["estimated_cost_cny"]:
                est = estimate_cost_cny(r["model"] or "", r["task_type"],
                                        r["ti"], r["tou"])
                if est:
                    r["estimated_cost_cny"] = est
                    c.execute("UPDATE model_tasks SET estimated_cost_cny=? WHERE id=?",
                              (est, r["id"]))
    by_type: dict = {}
    for r in rows:
        d = by_type.setdefault(r["task_type"], {"count": 0, "failed": 0,
                                                "tokens": 0, "cost_cny": 0.0})
        if r["status"] == "succeeded":
            d["count"] += 1
            d["tokens"] += r["ti"] + r["tou"]
            d["cost_cny"] = round(d["cost_cny"] + (r["estimated_cost_cny"] or 0), 2)
        elif r["status"] == "failed":
            d["failed"] += 1
    # Route Regret：同一节点多次成功生成，只有最后一次进成片——之前的都是白花的钱
    regret = {"superseded_generations": 0, "regret_cost_cny": 0.0, "by_capability": {}}
    gen_rows = [r for r in rows if r["task_type"] in ("image_generation", "video_generation")
                and r["status"] == "succeeded"]
    by_node: dict = {}
    for r in gen_rows:
        by_node.setdefault(r["node_id"], []).append(r)
    for nid_, lst in by_node.items():
        lst.sort(key=lambda x: x["created_at"])
        for r in lst[:-1]:                      # 除最后一次外全部视为被废弃
            regret["superseded_generations"] += 1
            regret["regret_cost_cny"] = round(
                regret["regret_cost_cny"] + (r["estimated_cost_cny"] or 0), 2)
            cap = r["capability_id"] or ("video_gen" if r["task_type"] == "video_generation"
                                         else "image")
            regret["by_capability"][cap] = regret["by_capability"].get(cap, 0) + 1
    total = round(sum(d["cost_cny"] for d in by_type.values()), 2)
    uncal = [m for m, p in PRICING.items() if not p.get("calibrated")]
    return {"project_id": pid, "by_type": by_type, "total_cost_cny": total,
            "route_regret": regret,
            "note": ("单价含未校准估计值(" + ",".join(uncal) + ")，拿到账单后在 providers.yaml 更新")
                    if uncal else "全部单价已账单校准"}


@app.get("/api/stats/capability")
def capability_stats():
    """能力证据台账：按 capability_id 汇总判决，给出档位建议 vs 配置档位。"""
    from .executors import load_capability_map
    with db._conn() as c:
        c.row_factory = __import__("sqlite3").Row
        rows = [dict(r) for r in c.execute(
            "SELECT capability_id, verdict, COUNT(*) n FROM model_tasks"
            " WHERE capability_id IS NOT NULL AND verdict IS NOT NULL"
            " GROUP BY capability_id, verdict")]
    tally: dict = {}
    for r in rows:
        d = tally.setdefault(r["capability_id"], {"pass": 0, "reject": 0, "other": 0})
        key = r["verdict"] if r["verdict"] in ("pass", "reject") else "other"
        d[key] += r["n"]
    configured = (load_capability_map() or {}).get("capabilities") or {}
    out = []
    for cap, d in sorted(tally.items()):
        n = d["pass"] + d["reject"]
        if n < 3:
            suggest = "UNKNOWN"
        else:
            rate = d["pass"] / n
            suggest = "HIGH" if rate >= 0.8 else ("MEDIUM" if rate >= 0.5 else "LOW")
        out.append({"capability_id": cap, **d, "evidence": n,
                    "pass_rate": round(d["pass"] / n, 2) if n else None,
                    "suggested_tier": suggest,
                    "configured_tier": (configured.get(cap) or {}).get("tier", "未配置")})
    return {"capabilities": out,
            "note": "suggested_tier 与 configured_tier 不一致时，人工核对后更新 capability_map.yaml"}


@app.get("/api/projects/{pid}/graph")
def get_graph(pid: str):
    nodes = db.query("canvas_nodes", "project_id=?", (pid,))
    for n in nodes:
        n["inputs"] = db.jloads(n["inputs"])
        n["outputs"] = db.jloads(n["outputs"])
    return {"nodes": nodes,
            "edges": db.query("canvas_edges", "project_id=?", (pid,))}


@app.post("/api/projects/{pid}/nodes")
def create_node(pid: str, n: NodeIn):
    if not db.get("projects", pid):
        raise HTTPException(404, "项目不存在")
    nid = db.new_id("node")
    db.insert("canvas_nodes", {
        "id": nid, "project_id": pid, "type": n.type, "title": n.title,
        "position_x": n.position.get("x", 0), "position_y": n.position.get("y", 0),
        "inputs": json.dumps(n.inputs, ensure_ascii=False),
        "created_at": db.now(), "updated_at": db.now(),
    })
    return db.get("canvas_nodes", nid)


@app.patch("/api/nodes/{nid}")
def update_node(nid: str, body: dict):
    node = db.get("canvas_nodes", nid)
    if not node:
        raise HTTPException(404, "节点不存在")
    fields = {}
    if "inputs" in body:
        fields["inputs"] = json.dumps(body["inputs"], ensure_ascii=False)
    if "title" in body:
        fields["title"] = body["title"]
    if "position" in body:
        fields["position_x"] = body["position"].get("x", 0)
        fields["position_y"] = body["position"].get("y", 0)
    if "outputs" in body:   # 管理/产线工具需要改写成果（如调整分镜路由）
        fields["outputs"] = json.dumps(body["outputs"], ensure_ascii=False)
    if fields:
        fields["updated_at"] = db.now()
        db.update("canvas_nodes", nid, fields)
    return db.get("canvas_nodes", nid)


class ExpandIn(BaseModel):
    domain: str = "general"      # 帧/视频质检使用的领域规则包


@app.post("/api/nodes/{nid}/storyboard_from_ref")
async def storyboard_from_ref(nid: str):
    """按参考视频生成分镜：把参考视频节点的逐段复刻卡转成分镜（镜头数/时长对齐参考，
    每镜携带真实关键帧锚点与路线建议 R1/R2/R3），产出一个已完成的分镜节点。"""
    node = db.get("canvas_nodes", nid)
    if not node or node["type"] != "ref_video":
        raise HTTPException(404, "参考视频节点不存在")
    segs = db.jloads(node["outputs"]).get("segments") or []
    if not segs:
        raise HTTPException(400, "请先执行参考视频节点（需为升级后的切分版）")
    shots = []
    for s in segs:
        card = s.get("card") or {}
        sec = float(s.get("seconds") or 5)
        route_mode = "R3" if card.get("has_faces") else "R2"
        facts = [str(x) for x in (card.get("science_facts") or [])]
        kfs = s.get("keyframes") or []
        shots.append({
            "index": s["index"], "type": "ai_video",
            "first_frame_prompt": card.get("first_frame_desc") or "",
            "last_frame_delta": "",
            "last_frame_prompt": card.get("last_frame_desc") or "",
            "motion": f"{card.get('camera') or ''}；{card.get('action_timeline') or ''}"[:200],
            "seconds": 10 if sec > 5 else 5,
            "frame_elements": (card.get("subjects") or [])[:3],
            "caption": "",
            "assertions": ([{"text": f, "phase": "frame"} for f in facts[:4]]
                           + [{"text": "画面与参考基准帧的科学事实一致", "phase": "frame"}]),
            "route_mode": route_mode,
            "ref_first_url": kfs[0] if kfs else "",
            "ref_last_url": kfs[-1] if len(kfs) > 1 else "",
            "ref_clip_url": s.get("clip_url") or "",
        })
    sb_id = db.new_id("node")
    db.insert("canvas_nodes", {
        "id": sb_id, "project_id": node["project_id"], "type": "storyboard",
        "title": f"分镜·按参考视频（{len(shots)}镜）",
        "position_x": node["position_x"] + 340, "position_y": node["position_y"],
        "inputs": json.dumps({"from_ref_node": nid, "domain": db.jloads(node["inputs"]).get("domain") or "general"}, ensure_ascii=False),
        "outputs": json.dumps({"storyboard": {"shots": shots},
                               "note": "由参考视频复刻卡生成：R1=真实素材增强 R2=真实帧锚定 R3=风格化重演"},
                              ensure_ascii=False),
        "status": "succeeded", "created_at": db.now(), "updated_at": db.now()})
    db.insert("canvas_edges", {"id": db.new_id("edge"), "project_id": node["project_id"],
                               "source_node_id": nid, "target_node_id": sb_id})
    return {"storyboard_node": sb_id, "shots": len(shots)}


@app.post("/api/nodes/{nid}/script_from_ref")
async def script_from_ref(nid: str):
    """R4 内容重演绎：口播讲稿 → 微课脚本节点（去口头语/纠错字/保留知识点，自动生成内容）。"""
    node = db.get("canvas_nodes", nid)
    if not node or node["type"] != "ref_video":
        raise HTTPException(404, "参考视频节点不存在")
    out = db.jloads(node["outputs"])
    transcript = (out.get("transcript") or {}).get("text", "")
    if not transcript:
        raise HTTPException(400, "该参考视频没有可用讲稿（无音轨或未转写）——请先执行参考视频节点")
    domain = db.jloads(node["inputs"]).get("domain") or "general"
    goal = ("将以下教师口播讲稿重演绎为微课脚本（R4 内容重演绎）。要求：\n"
            "1. 保留全部知识点、公式、案例与讲解顺序，一个都不能丢；\n"
            "2. 语音转写存在同音错字，按学科专业术语纠正（如'丹射'应为'干涉'、'光尘差'应为'光程差'）；\n"
            "3. 去掉口头语与重复，压缩到微课节奏；\n"
            "4. 每段解说都要说明配什么可视化画面（严谨图表/公式类标注用代码渲染呈现）。\n"
            f"【学科】{domain}\n【口播讲稿全文】\n{transcript[:4000]}")
    sid = db.new_id("node")
    db.insert("canvas_nodes", {
        "id": sid, "project_id": node["project_id"], "type": "script",
        "title": "脚本·R4重演绎",
        "position_x": node["position_x"] + 340, "position_y": node["position_y"] + 260,
        "inputs": json.dumps({"goal": goal, "duration": 120}, ensure_ascii=False),
        "outputs": "{}", "status": "idle",
        "created_at": db.now(), "updated_at": db.now()})
    db.insert("canvas_edges", {"id": db.new_id("edge"), "project_id": node["project_id"],
                               "source_node_id": nid, "target_node_id": sid})
    await executors.execute_chain(sid)
    return {"script_node": sid, "status": db.get("canvas_nodes", sid)["status"]}


@app.post("/api/nodes/{nid}/expand_storyboard")
def expand_storyboard(nid: str, req: ExpandIn):
    """把已完成的分镜展开为逐镜生产线：
    每镜 = 首帧图(+尾帧图) → 各挂帧质检 → 视频(引用帧节点) → 视频质检 → 汇入拼接。
    code_render 镜头 = 渲染节点 → 质检 → 汇入拼接。
    人工确认关卡由质检节点的人工终裁 + 单步执行承担。"""
    node = db.get("canvas_nodes", nid)
    if not node or node["type"] != "storyboard":
        raise HTTPException(404, "分镜节点不存在")
    sb = db.jloads(node["outputs"]).get("storyboard")
    shots = (sb or {}).get("shots") or []
    # 分镜自带学科（参考视频产线传入）时，作为质检领域缺省
    _sb_domain = db.jloads(node["inputs"]).get("domain")
    if _sb_domain and req.domain == "general":
        req.domain = _sb_domain
    if not shots:
        raise HTTPException(400, "请先执行分镜节点，生成镜头列表")
    pid = node["project_id"]
    x0, y0 = node["position_x"], node["position_y"]

    def mk(type_: str, title: str, inputs: dict, x: float, y: float) -> str:
        nid2 = db.new_id("node")
        db.insert("canvas_nodes", {
            "id": nid2, "project_id": pid, "type": type_, "title": title,
            "position_x": x, "position_y": y,
            "inputs": json.dumps(inputs, ensure_ascii=False),
            "outputs": "{}", "status": "idle",
            "created_at": db.now(), "updated_at": db.now()})
        return nid2

    def link(s: str, t: str) -> None:
        db.insert("canvas_edges", {"id": db.new_id("edge"), "project_id": pid,
                                   "source_node_id": s, "target_node_id": t})

    # 复用项目里已有的拼接节点，没有则新建
    comp = next((n for n in db.query("canvas_nodes", "project_id=?", (pid,))
                 if n["type"] == "compose"), None)
    mid_y = y0 + 300 + (max(len(shots) - 1, 0) * 480) // 2   # 拼接放镜头行的中部
    comp_id = comp["id"] if comp else mk(
        "compose", "拼接成片", {"burn_subtitles": "是"}, x0 + 1450, mid_y)
    created = 0
    for i, shot in enumerate(shots, start=1):
        y = y0 + 300 + (i - 1) * 480
        sec = int(shot.get("seconds") or 5)
        asserts = shot.get("assertions") or []      # 断言直存质检节点，不依赖连线
        # 断言分层：帧质检只考 phase=frame；视频质检考全量（含时间性断言）
        frame_asserts = [a for a in asserts
                         if not (isinstance(a, dict) and a.get("phase") == "video")]
        qc_frame_in = {"domain": req.domain, "shot_index": i,
                       "assertions": frame_asserts,
                       "frame_elements": shot.get("frame_elements") or []}
        qc_in = {"domain": req.domain, "shot_index": i, "assertions": asserts}
        if shot.get("type") == "code_render":
            cr = mk("code_render", f"镜头{i}·代码渲染",
                    {"template": "lens_focus", "focal_length": 2.2,
                     "num_rays": 7, "duration": min(sec, 20)}, x0 + 320, y)
            q = mk("qc", f"镜头{i}·质检", qc_in, x0 + 320, y + 250)
            link(cr, q)
            link(cr, comp_id)
            created += 2
            continue
        # ── 参考视频复刻路线（storyboard_from_ref 生成的分镜携带锚点）──
        route_mode = str(shot.get("route_mode") or "")
        ref_first = str(shot.get("ref_first_url") or "")
        ref_last = str(shot.get("ref_last_url") or "")
        if route_mode in ("R2", "R3") and ref_first:
            qc_frame_in = {**qc_frame_in, "ref_frames": [ref_first]}
            qc_in = {**qc_in, "ref_frames": [u for u in (ref_first, ref_last) if u]}
        if route_mode == "R1" and shot.get("ref_clip_url"):
            # R1 真实素材增强：真实片段直接进产线（慢放/特写/标注在节点上调）
            en = mk("enhance", f"镜头{i}·素材增强",
                    {"source_url": shot["ref_clip_url"], "slow_factor": 1,
                     "caption": shot.get("caption") or ""}, x0 + 320, y)
            q = mk("qc", f"镜头{i}·质检", qc_in, x0 + 320, y + 250)
            link(en, q)
            link(en, comp_id)
            created += 2
            continue
        first_p = (shot.get("first_frame_prompt") or "").strip()
        # 新版分镜输出 last_frame_delta（一句话变化指令）；兼容旧版 last_frame_prompt
        last_delta = (shot.get("last_frame_delta") or "").strip()
        legacy_last = (shot.get("last_frame_prompt") or "").strip()
        STYLIZE = ("Re-render this exact scene in the project's art style: keep the same "
                   "composition, subjects, positions and lighting; stylized character "
                   "faces only, no photorealistic faces. ")
        f1_in: dict = {"prompt": first_p, "shot_index": i, "size": "2560x1440"}
        if route_mode == "R2" and ref_first:
            # 真实帧锚定：关键帧直接作为首帧成品（零成本零幻觉）
            f1_in.update({"ref_asset_url": ref_first, "use_ref_as_output": "是"})
        elif route_mode == "R3" and ref_first:
            # 风格化重演：以真实帧为参考整体重渲（两步法的机制化）
            f1_in.update({"ref_asset_url": ref_first, "prompt": STYLIZE + first_p})
        f1 = mk("image", f"镜头{i}·首帧", f1_in, x0 + 320, y)
        q1 = mk("qc", f"镜头{i}·首帧质检", qc_frame_in, x0 + 320, y + 250)
        link(f1, q1)
        created += 2
        v_inputs: dict = {"prompt": shot.get("motion") or "", "resolution": "720p",
                          "caption": shot.get("caption") or "",
                          "duration": 10 if sec > 5 else 5, "first_frame_node": f1}
        vx = x0 + 640
        make_ref_tail = route_mode in ("R2", "R3") and ref_last and ref_last != ref_first
        if make_ref_tail:
            f2_in: dict = {"shot_index": i, "size": "2560x1440"}
            if route_mode == "R2":
                f2_in.update({"ref_asset_url": ref_last, "use_ref_as_output": "是"})
            else:
                f2_in.update({"ref_asset_url": ref_last,
                              "prompt": STYLIZE + (legacy_last or first_p)})
            f2 = mk("image", f"镜头{i}·尾帧", f2_in, x0 + 640, y)
            q2 = mk("qc", f"镜头{i}·尾帧质检",
                    {**qc_frame_in, "pair_first_node": f1,
                     "ref_frames": [ref_last]}, x0 + 640, y + 250)
            link(f2, q2)
            v_inputs["last_frame_node"] = f2
            vx = x0 + 960
            created += 2
        elif last_delta or (legacy_last and legacy_last != first_p):
            # 编辑式尾帧：运行时取首帧成品图做参考，只执行一句话的最小变化
            delta = last_delta or (f"apply the end-state change described here: {legacy_last}")
            f2 = mk("image", f"镜头{i}·尾帧",
                    {"edit_delta": delta, "ref_node": f1,
                     "shot_index": i, "size": "2560x1440"}, x0 + 640, y)
            q2 = mk("qc", f"镜头{i}·尾帧质检", {**qc_frame_in, "pair_first_node": f1},
                    x0 + 640, y + 250)
            link(f2, q2)
            v_inputs["last_frame_node"] = f2
            vx = x0 + 960
            created += 2
        v = mk("video", f"镜头{i}·视频", v_inputs, vx, y)
        link(f1, v)
        if "last_frame_node" in v_inputs:
            link(str(v_inputs["last_frame_node"]), v)
        qv = mk("qc", f"镜头{i}·视频质检", qc_in, vx, y + 250)
        link(v, qv)
        link(v, comp_id)
        created += 2
    return {"created": created, "shots": len(shots), "compose_id": comp_id}


@app.get("/api/projects/{pid}/review_queue")
def review_queue(pid: str):
    """验收台清单：①质检 reject/needs_human 的被检素材 ②失败节点（含 infra 分类）。"""
    nodes = db.query("canvas_nodes", "project_id=?", (pid,))
    by_id = {n["id"]: n for n in nodes}
    items, failures = [], []
    for n in nodes:
        if n["type"] == "qc":
            out = db.jloads(n["outputs"])
            v = out.get("verdict")
            if v in ("reject", "needs_human") and not out.get("human_override"):
                tgt = by_id.get(out.get("target_node_id", ""))
                if not tgt:
                    continue
                tout = db.jloads(tgt["outputs"])
                fails = (out.get("blockers") or []) + (out.get("uncertains") or [])
                items.append({
                    "qc_node_id": n["id"], "qc_title": n["title"],
                    "target_node_id": tgt["id"], "target_title": tgt["title"],
                    "target_type": tgt["type"],
                    "asset_url": tout.get("asset_url", ""),
                    "verdict": v,
                    "remediation": out.get("remediation", "retry"),
                    "summary": str(out.get("summary", ""))[:200],
                    "fails": [{"id": f.get("id"),
                               "evidence": str(f.get("evidence", ""))[:120]}
                              for f in fails[:5]],
                    "suggested_prompt": str(out.get("suggested_prompt", ""))[:400],
                })
        elif n["status"] == "failed":
            out = db.jloads(n["outputs"])
            failures.append({
                "node_id": n["id"], "title": n["title"], "type": n["type"],
                "error": str(out.get("error", ""))[:160],
                "error_class": out.get("error_class", "content"),
            })
    return {"reviews": items, "failures": failures}


@app.post("/api/projects/{pid}/retry_infra")
async def retry_infra(pid: str, background: BackgroundTasks):
    """一键重试全部基础设施类失败节点。"""
    nodes = db.query("canvas_nodes", "project_id=? AND status='failed'", (pid,))
    targets = [n["id"] for n in nodes
               if db.jloads(n["outputs"]).get("error_class") == "infra"]
    for nid in targets:
        db.update("canvas_nodes", nid, {"status": "running", "updated_at": db.now()})
        background.add_task(_run_async, nid, False)
    return {"retrying": len(targets)}


@app.post("/api/projects/{pid}/resume_line")
async def resume_line(pid: str, background: BackgroundTasks):
    """续跑产线：重新执行所有『帧已就绪（质检通过或人工放行）但视频未产出』的视频节点。"""
    nodes = db.query("canvas_nodes", "project_id=?", (pid,))
    by_id = {n["id"]: n for n in nodes}

    def frame_ok(ref: str) -> bool:
        fn = by_id.get(ref)
        if not fn or fn["status"] != "succeeded":
            return False
        qc = (db.jloads(fn["outputs"]).get("qc") or {})
        return qc.get("verdict") != "reject"

    started = 0
    for n in nodes:
        if n["type"] != "video" or n["status"] == "succeeded":
            continue
        tin = db.jloads(n["inputs"])
        f1 = str(tin.get("first_frame_node") or "").strip()
        f2 = str(tin.get("last_frame_node") or "").strip()
        if f1 and not frame_ok(f1):
            continue
        if f2 and not frame_ok(f2):
            continue
        db.update("canvas_nodes", n["id"], {"status": "running", "updated_at": db.now()})
        background.add_task(_run_async, n["id"], False)
        started += 1
    return {"started": started}


@app.post("/api/projects/{pid}/cleanup_duplicates")
def cleanup_duplicates(pid: str):
    """清理重复节点：同名同类型的节点组里，删掉从未执行过（idle 且无任务记录）的多余副本。
    有成果/有历史的节点永远保留。"""
    nodes = db.query("canvas_nodes", "project_id=?", (pid,))
    groups: dict[tuple, list] = {}
    for n in nodes:
        groups.setdefault((n["title"], n["type"]), []).append(n)
    deleted = 0
    with db._conn() as c:
        for (title, _t), grp in groups.items():
            if len(grp) < 2 or not title:
                continue
            def has_history(n: dict) -> bool:
                cnt = c.execute("SELECT COUNT(*) FROM model_tasks WHERE node_id=?",
                                (n["id"],)).fetchone()[0]
                return n["status"] != "idle" or cnt > 0 or n["outputs"] not in ("{}", "", None)
            keepers = [n for n in grp if has_history(n)] or [max(grp, key=lambda x: x["created_at"] or "")]
            keep_ids = {n["id"] for n in keepers}
            for n in grp:
                if n["id"] not in keep_ids:
                    c.execute("DELETE FROM canvas_nodes WHERE id=?", (n["id"],))
                    c.execute("DELETE FROM canvas_edges WHERE source_node_id=? OR target_node_id=?",
                              (n["id"], n["id"]))
                    deleted += 1
    return {"deleted": deleted}


@app.post("/api/projects/{pid}/migrate_pairs")
def migrate_pairs(pid: str):
    """存量迁移：把旧产线的尾帧节点转换为编辑式（ref_node+edit_delta），
    并给尾帧质检节点补 pair_first_node。以视频节点的 first/last_frame_node 为线索。"""
    nodes = db.query("canvas_nodes", "project_id=?", (pid,))
    by_id = {n["id"]: n for n in nodes}
    edges = db.query("canvas_edges", "project_id=?", (pid,))
    fixed_frames, fixed_qcs = 0, 0
    for v in [n for n in nodes if n["type"] == "video"]:
        tin = db.jloads(v["inputs"])
        f1, f2 = str(tin.get("first_frame_node") or ""), str(tin.get("last_frame_node") or "")
        if not (f1 in by_id and f2 in by_id):
            continue
        n2 = by_id[f2]
        in2 = db.jloads(n2["inputs"])
        changed = False
        if not str(in2.get("ref_node") or "").strip():
            in2["ref_node"] = f1
            changed = True
        if not str(in2.get("edit_delta") or "").strip():
            delta = ""
            # 优先取分镜的变化指令
            idx = int(in2.get("shot_index") or 0)
            for sb_node in nodes:
                sb = db.jloads(sb_node["outputs"]).get("storyboard") if sb_node["type"] == "storyboard" else None
                if sb and idx and idx <= len(sb.get("shots", [])):
                    shot = sb["shots"][idx - 1]
                    delta = (shot.get("last_frame_delta") or "").strip()
                    if not delta:
                        legacy = (shot.get("last_frame_prompt") or "").strip()
                        if legacy:
                            delta = f"apply the end-state change described here: {legacy}"
                    break
            # 退路：旧锚定提示词里截取 End state changes 之后的部分
            if not delta:
                old_p = str(in2.get("prompt") or "")
                if "End state changes:" in old_p:
                    delta = old_p.split("End state changes:", 1)[1].strip()
            if delta:
                in2["edit_delta"] = delta[:600]
                changed = True
        if changed:
            db.update("canvas_nodes", f2, {
                "inputs": json.dumps(in2, ensure_ascii=False), "updated_at": db.now()})
            fixed_frames += 1
        # 尾帧下游的质检节点补配对参数
        for e in edges:
            if e["source_node_id"] == f2:
                qn = by_id.get(e["target_node_id"])
                if qn and qn["type"] == "qc":
                    qin = db.jloads(qn["inputs"])
                    if str(qin.get("pair_first_node") or "") != f1:
                        qin["pair_first_node"] = f1
                        db.update("canvas_nodes", qn["id"], {
                            "inputs": json.dumps(qin, ensure_ascii=False),
                            "updated_at": db.now()})
                        fixed_qcs += 1
    return {"fixed_frames": fixed_frames, "fixed_qcs": fixed_qcs}


@app.post("/api/projects/{pid}/edges")
def create_edge(pid: str, e: EdgeIn):
    eid = db.new_id("edge")
    db.insert("canvas_edges", {"id": eid, "project_id": pid,
                               "source_node_id": e.source_node_id,
                               "target_node_id": e.target_node_id})
    return db.get("canvas_edges", eid)


@app.post("/api/nodes/{nid}/execute")
async def execute_node(nid: str, background: BackgroundTasks):
    node = db.get("canvas_nodes", nid)
    if not node:
        raise HTTPException(404, "节点不存在")
    if node["status"] == "running":
        raise HTTPException(409, "节点正在执行中")
    if node["type"] in ("video", "ref_video", "enhance", "compose"):
        # 长任务后台执行，前端轮询节点状态（同步长请求会被代理掐断且阻塞事件循环）
        background.add_task(_run_async, nid, False)
        db.update("canvas_nodes", nid, {"status": "running", "updated_at": db.now()})
        return {"status": "running", "node_id": nid}
    try:
        await executors.execute_node(nid)
    except Exception:
        pass  # 失败详情已写入节点 outputs
    node = db.get("canvas_nodes", nid)
    node["outputs"] = db.jloads(node["outputs"])
    return node


@app.post("/api/nodes/{nid}/execute_chain")
async def execute_chain(nid: str, background: BackgroundTasks, step: bool = False):
    """运行到此节点。step=false 自动跑完整条链；step=true 单步模式：
    只跑链上第一个未完成的节点，跑完停下等用户确认预览。"""
    node = db.get("canvas_nodes", nid)
    if not node:
        raise HTTPException(404, "节点不存在")
    chain = executors.upstream_chain(nid)
    pending = [x for x in chain
               if (n := db.get("canvas_nodes", x))
               and (x == nid or n["status"] != "succeeded")]
    if step:
        target = pending[0] if pending else nid
        t = db.get("canvas_nodes", target)
        db.update("canvas_nodes", target, {"status": "running", "updated_at": db.now()})
        background.add_task(_run_async, target, False)
        return {"status": "running", "ran": target,
                "ran_title": t.get("title") or t["type"],
                "remaining": max(0, len(pending) - 1)}
    for x in pending:
        db.update("canvas_nodes", x, {"status": "running", "updated_at": db.now()})
    background.add_task(_run_async, nid, True)
    return {"status": "running", "chain": pending}


def _run_async(nid: str, chain: bool):
    try:
        if chain:
            asyncio.run(executors.execute_chain(nid))
        else:
            asyncio.run(executors.execute_node(nid))
    except Exception:
        pass  # 失败状态已写入节点


@app.post("/api/nodes/{nid}/upload")
async def upload_asset(nid: str, file: UploadFile = File(...)):
    """上传本地素材挂到节点：图像节点收图片（作为成果）；
    参考视频节点收 mp4（作为待分析素材，上传后节点保持待执行）。"""
    node = db.get("canvas_nodes", nid)
    if not node:
        raise HTTPException(404, "节点不存在")
    ext = Path(file.filename or "img.png").suffix.lower() or ".png"
    is_ref = node["type"] == "ref_video"
    allowed = (".mp4", ".mov", ".webm") if is_ref else (".png", ".jpg", ".jpeg", ".webp")
    if ext not in allowed:
        raise HTTPException(400, f"该节点仅支持 {'/'.join(allowed)} 文件")
    if ext == ".mov" or ext == ".webm":
        ext = ".mp4"  # 统一容器名，ffmpeg 抽帧不受影响
    asset_id = db.new_id("asset")
    filename = f"{asset_id}{ext}"
    (ASSETS_DIR / filename).write_bytes(await file.read())
    db.insert("assets", {
        "id": asset_id, "project_id": node["project_id"], "node_id": nid,
        "kind": "video" if is_ref else "image", "filename": filename,
        "meta": json.dumps({"source": "uploaded"}), "created_at": db.now(),
    })
    outputs = {"asset_id": asset_id, "asset_url": f"/assets/{filename}",
               "source": "uploaded"}
    db.update("canvas_nodes", nid, {
        # 参考视频：上传只是备料，保持 idle 等待执行分析；图像：上传即成果
        "status": node["status"] if is_ref else "succeeded",
        "outputs": json.dumps(outputs, ensure_ascii=False), "updated_at": db.now(),
    })
    node = db.get("canvas_nodes", nid)
    node["outputs"] = outputs
    node["inputs"] = db.jloads(node["inputs"])
    return node


@app.delete("/api/nodes/{nid}")
def delete_node(nid: str):
    with db._conn() as c:
        c.execute("DELETE FROM canvas_nodes WHERE id=?", (nid,))
        c.execute("DELETE FROM canvas_edges WHERE source_node_id=? OR target_node_id=?",
                  (nid, nid))
    return {"ok": True}


@app.delete("/api/edges/{eid}")
def delete_edge(eid: str):
    with db._conn() as c:
        c.execute("DELETE FROM canvas_edges WHERE id=?", (eid,))
    return {"ok": True}


class AgentReq(BaseModel):
    message: str
    model: str | None = None
    research: bool = False
    history: list[dict] = []


@app.get("/api/agent/models")
def agent_models():
    """Agent 可选模型列表（providers.yaml 的 agent_models）。"""
    from .config import AGENT_MODELS
    return AGENT_MODELS


@app.post("/api/projects/{pid}/agent")
async def agent_plan(pid: str, req: AgentReq):
    """Agent 对话：一句话自动规划整条画布节点链。"""
    if not db.get("projects", pid):
        raise HTTPException(404, "项目不存在")
    try:
        return await executors.run_agent(pid, req.message, req.model, req.research,
                                         req.history)
    except Exception as e:
        raise HTTPException(500, f"Agent 规划失败: {str(e)[:200]}")


def _asset_view(r: dict) -> dict:
    r["url"] = f"/assets/{r['filename']}"
    r["starred"] = bool(db.jloads(r.get("meta")).get("starred"))
    return r


@app.get("/api/projects/{pid}/assets")
def list_assets(pid: str):
    """素材库：项目全部生成/上传的素材，按时间倒序。"""
    return [_asset_view(r) for r in
            db.query("assets", "project_id=? ORDER BY created_at DESC", (pid,))]


@app.get("/api/assets/starred")
def list_starred():
    """个人资产库：跨项目收藏的素材。"""
    rows = db.query("assets", "meta LIKE '%\"starred\": true%' ORDER BY created_at DESC")
    return [_asset_view(r) for r in rows]


@app.patch("/api/assets/{aid}/star")
def star_asset(aid: str, starred: bool = True):
    """收藏/取消收藏素材（收藏 = 存入个人资产库，跨项目可用）。"""
    a = db.get("assets", aid)
    if not a:
        raise HTTPException(404, "素材不存在")
    meta = db.jloads(a.get("meta"))
    meta["starred"] = starred
    db.update("assets", aid, {"meta": json.dumps(meta, ensure_ascii=False)})
    return {"ok": True, "starred": starred}


@app.delete("/api/assets/{aid}")
def delete_asset(aid: str):
    """删除素材（文件 + 记录）。"""
    a = db.get("assets", aid)
    if not a:
        raise HTTPException(404, "素材不存在")
    try:
        (ASSETS_DIR / a["filename"]).unlink(missing_ok=True)
        srt = ASSETS_DIR / (Path(a["filename"]).stem + ".srt")
        srt.unlink(missing_ok=True)
    except OSError:
        pass
    with db._conn() as c:
        c.execute("DELETE FROM assets WHERE id=?", (aid,))
    return {"ok": True}


class QcOverrideReq(BaseModel):
    verdict: str  # pass_human（人工放行）或 reject_human（人工判不合格）


@app.post("/api/nodes/{nid}/qc_override")
def qc_override(nid: str, req: QcOverrideReq):
    """人工终裁：放行权在人。作用于质检节点，同步写回被检节点。"""
    node = db.get("canvas_nodes", nid)
    if not node or node["type"] != "qc":
        raise HTTPException(404, "质检节点不存在")
    if req.verdict not in ("pass_human", "reject_human"):
        raise HTTPException(400, "verdict 只能是 pass_human 或 reject_human")
    out = db.jloads(node["outputs"])
    out["verdict"] = "pass" if req.verdict == "pass_human" else "reject"
    out["human_override"] = req.verdict
    db.update("canvas_nodes", nid,
              {"outputs": json.dumps(out, ensure_ascii=False), "updated_at": db.now()})
    tgt = db.get("canvas_nodes", out.get("target_node_id", ""))
    if tgt:
        tout = db.jloads(tgt["outputs"])
        tout["qc"] = {"verdict": out["verdict"], "qc_node_id": nid,
                      "human_override": req.verdict}
        db.update("canvas_nodes", tgt["id"],
                  {"outputs": json.dumps(tout, ensure_ascii=False)})
    node = db.get("canvas_nodes", nid)
    node["outputs"] = db.jloads(node["outputs"])
    node["inputs"] = db.jloads(node["inputs"])
    return node


class RestoreReq(BaseModel):
    nodes: list[dict] = []
    edges: list[dict] = []


@app.post("/api/projects/{pid}/restore")
def restore(pid: str, req: RestoreReq):
    """撤销删除：按原 id 恢复节点与连线。"""
    with db._conn() as c:
        for n in req.nodes:
            c.execute(
                "INSERT OR REPLACE INTO canvas_nodes "
                "(id, project_id, type, title, position_x, position_y, inputs, outputs, "
                " status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (n["id"], pid, n["type"], n.get("title", ""),
                 n.get("position_x", 100), n.get("position_y", 100),
                 json.dumps(n.get("inputs", {}), ensure_ascii=False),
                 json.dumps(n.get("outputs", {}), ensure_ascii=False),
                 n.get("status", "idle"), db.now(), db.now()))
        for e in req.edges:
            c.execute(
                "INSERT OR REPLACE INTO canvas_edges "
                "(id, project_id, source_node_id, target_node_id, source_handle, target_handle) "
                "VALUES (?,?,?,?,?,?)",
                (e["id"], pid, e["source"], e["target"], "output", "input"))
    return {"ok": True, "nodes": len(req.nodes), "edges": len(req.edges)}


@app.get("/api/nodes/{nid}")
def get_node(nid: str):
    node = db.get("canvas_nodes", nid)
    if not node:
        raise HTTPException(404, "节点不存在")
    node["inputs"] = db.jloads(node["inputs"])
    node["outputs"] = db.jloads(node["outputs"])
    return node


@app.get("/api/projects/{pid}/tasks")
def list_tasks(pid: str):
    return db.query("model_tasks", "project_id=?", (pid,))
