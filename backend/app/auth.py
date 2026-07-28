"""双通道鉴权：管理员（账号+密码）与邀请码（限额+熔断）。

- 管理员凭证放 .env（ADMIN_USER / ADMIN_PASS）；两者都未配置时鉴权整体关闭（本机开发模式）。
- 会话存 DB（auth_sessions），cookie=jojo_session（httpOnly）。
- 邀请码（invite_codes）：每码每日视频条数上限 + 每日成本上限；
  全局熔断：当日全站 model_tasks 估算成本超 DAILY_BUDGET_CNY 时拒绝邀请级生成。
"""
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request

from . import db

COOKIE = "jojo_session"
SESSION_DAYS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS auth_sessions (
  id TEXT PRIMARY KEY, role TEXT NOT NULL, invite_code TEXT,
  created_at TEXT, expires_at TEXT
);
CREATE TABLE IF NOT EXISTS invite_codes (
  id TEXT PRIMARY KEY, code TEXT UNIQUE NOT NULL, label TEXT DEFAULT '',
  daily_video_limit INTEGER DEFAULT 3, daily_cost_limit_cny REAL DEFAULT 10,
  disabled INTEGER DEFAULT 0, created_at TEXT
);
CREATE TABLE IF NOT EXISTS usage_log (
  id TEXT PRIMARY KEY, invite_code TEXT, day TEXT, kind TEXT,
  est_cost_cny REAL DEFAULT 0, created_at TEXT
);
"""

# 邀请级执行的粗粒度成本估算（元/次），用于限额判断；精确成本仍以 model_tasks 台账为准
EST_COST = {"video": 2.0, "image": 0.5, "qc": 0.15, "script": 0.05,
            "storyboard": 0.05, "agent": 0.05, "compose": 0.0, "code_render": 0.0}


def init() -> None:
    with db._conn() as c:
        c.executescript(SCHEMA)


def _admin_creds() -> tuple[str, str] | None:
    u, p = os.getenv("ADMIN_USER", "").strip(), os.getenv("ADMIN_PASS", "").strip()
    return (u, p) if u and p else None


def enabled() -> bool:
    return _admin_creds() is not None


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def create_session(role: str, invite_code: str | None = None) -> str:
    token = secrets.token_urlsafe(32)
    db.insert("auth_sessions", {
        "id": token, "role": role, "invite_code": invite_code,
        "created_at": db.now(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat(),
    })
    return token


def get_session(request: Request) -> dict | None:
    if not enabled():
        return {"role": "admin", "invite_code": None, "dev": True}
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    s = db.get("auth_sessions", token)
    if not s or s["expires_at"] < db.now():
        return None
    return s


def login_admin(username: str, password: str) -> str:
    creds = _admin_creds()
    if not creds:
        raise HTTPException(400, "服务端未配置管理员账号（开发模式无需登录）")
    if not (secrets.compare_digest(username, creds[0]) and secrets.compare_digest(password, creds[1])):
        raise HTTPException(401, "账号或密码不正确")
    return create_session("admin")


def login_invite(code: str) -> str:
    rows = db.query("invite_codes", "code=? AND disabled=0", (code.strip(),))
    if not rows:
        raise HTTPException(401, "邀请码无效或已停用")
    return create_session("invite", rows[0]["code"])


def logout(token: str | None) -> None:
    if token:
        with db._conn() as c:
            c.execute("DELETE FROM auth_sessions WHERE id=?", (token,))


def usage_today(invite_code: str) -> dict:
    with db._conn() as c:
        r = c.execute(
            "SELECT COUNT(CASE WHEN kind='video' THEN 1 END) v, COALESCE(SUM(est_cost_cny),0) cost "
            "FROM usage_log WHERE invite_code=? AND day=?", (invite_code, _today())).fetchone()
    return {"videos": r["v"], "cost": round(r["cost"], 2)}


def site_cost_today() -> float:
    with db._conn() as c:
        r = c.execute(
            "SELECT COALESCE(SUM(estimated_cost_cny),0) FROM model_tasks WHERE created_at LIKE ?",
            (_today() + "%",)).fetchone()
    return float(r[0] or 0)


def check_and_log(session: dict, node_type: str) -> None:
    """邀请级会话在执行付费节点前的限额检查；通过则记一笔用量。管理员直通。"""
    if session.get("role") == "admin":
        return
    code = session.get("invite_code")
    rows = db.query("invite_codes", "code=? AND disabled=0", (code,))
    if not rows:
        raise HTTPException(403, "邀请码已停用")
    inv = rows[0]
    budget = float(os.getenv("DAILY_BUDGET_CNY", "50"))
    if site_cost_today() >= budget:
        raise HTTPException(429, f"今日全站生成预算（¥{budget:.0f}）已用完，明天再来或联系管理员")
    u = usage_today(code)
    if node_type == "video" and u["videos"] >= int(inv["daily_video_limit"]):
        raise HTTPException(429, f"该邀请码今日视频额度（{inv['daily_video_limit']}条）已用完")
    est = EST_COST.get(node_type, 0.1)
    if u["cost"] + est > float(inv["daily_cost_limit_cny"]):
        raise HTTPException(429, f"该邀请码今日成本额度（¥{inv['daily_cost_limit_cny']:.0f}）已用完")
    if est > 0:
        db.insert("usage_log", {"id": db.new_id("use"), "invite_code": code, "day": _today(),
                                "kind": node_type, "est_cost_cny": est, "created_at": db.now()})


def require(request: Request) -> dict:
    s = get_session(request)
    if not s:
        raise HTTPException(401, "未登录")
    return s


def require_admin(request: Request) -> dict:
    s = require(request)
    if s.get("role") != "admin":
        raise HTTPException(403, "需要管理员权限")
    return s
