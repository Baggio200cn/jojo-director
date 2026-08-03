# -*- coding: utf-8 -*-
"""JOJO Director 每日巡检（本机运行，SSH 到云端）。

检查项：云端健康 / 磁盘水位 / SQLite 热备 / 当日成本 / Seedance 2.5 开放探针。
输出：stdout 报告，异常行以 "ALERT:" 开头（供上层会话或人眼快速定位）。
周一附加：跑回归基线（backend/tests/regression.py，约 0.15 元）。
"""
import datetime
import json
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")   # Windows 控制台默认 GBK，统一 UTF-8

REPO = Path(__file__).resolve().parents[1]
KEY = str(Path.home() / ".ssh" / "jojo-deploy.pem")
HOST = "root@115.190.155.2"
BASE = "https://115.190.155.2"

ok, alerts = [], []


def ssh(cmd: str, timeout=60) -> str:
    r = subprocess.run(["ssh", "-i", KEY, "-o", "ConnectTimeout=15", HOST, cmd],
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:200])
    return r.stdout.strip()


def check(name):
    def deco(fn):
        def run():
            try:
                msg = fn()
                ok.append(f"  ✓ {name}: {msg}")
            except Exception as e:
                alerts.append(f"ALERT: {name} 失败 — {str(e)[:200]}")
        return run
    return deco


@check("云端健康")
def _health():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(BASE + "/api/health", timeout=20, context=ctx) as r:
        data = json.loads(r.read())
    assert data.get("ok"), f"响应异常 {data}"
    return "ok"


@check("磁盘水位")
def _disk():
    out = ssh("df -h / | tail -1")
    pct = int(out.split()[4].rstrip("%"))
    assert pct < 85, f"根分区已用 {pct}%（阈值85%）"
    return f"根分区 {pct}%"


@check("SQLite 热备")
def _backup():
    # backup API 出快照（热库直拷会损坏——2026-07-28 迁移教训），保留最近 7 份
    ssh("cd /opt/jojo/backend && mkdir -p backups && "
        "sqlite3 jojo.db \".backup 'backups/jojo_$(date +%Y%m%d).db'\" && "
        "ls -t backups/jojo_*.db | tail -n +8 | xargs -r rm --")
    n = ssh("ls /opt/jojo/backend/backups/ | wc -l")
    return f"快照留存 {n} 份"


@check("当日成本")
def _cost():
    today = datetime.date.today().isoformat()
    out = ssh("sqlite3 /opt/jojo/backend/jojo.db "
              f"\"SELECT COALESCE(SUM(estimated_cost_cny),0) FROM model_tasks "
              f"WHERE created_at LIKE '{today}%'\"")
    cost = float(out or 0)
    assert cost < 40, f"当日已花 ¥{cost:.1f}（预警线40，熔断线50）"
    return f"¥{cost:.2f}"


@check("Seedance 2.5 探针")
def _probe25():
    # 无成本探针：故意带非法参数。NotFound=仍未开放；参数错误=已开放（值得升级！）
    env = {l.split("=", 1)[0]: l.split("=", 1)[1].strip()
           for l in (REPO / "backend" / ".env").read_text(encoding="utf-8").splitlines()
           if "=" in l and not l.startswith("#")}
    key = env["ARK_API_KEY"]
    body = json.dumps({"model": "doubao-seedance-2-5-pro",
                       "content": [{"type": "text", "text": "probe --resolution 9999p"}]}).encode()
    req = urllib.request.Request(
        "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks",
        data=body, headers={"Authorization": f"Bearer {key}",
                            "Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=20)
        raise AssertionError("探针请求居然受理了——立即人工确认")
    except urllib.error.HTTPError as e:
        code = json.loads(e.read()).get("error", {}).get("code", "")
        if code == "InvalidEndpointOrModel.NotFound":
            return "尚未开放（个人账号）"
        raise AssertionError(f"错误码变化：{code} —— 2.5 可能已开放，值得人工验证并切 video_hd 路由")


def main():
    print(f"=== JOJO 每日巡检 {datetime.datetime.now():%Y-%m-%d %H:%M} ===")
    for fn in (_health, _disk, _backup, _cost, _probe25):
        fn()
    if datetime.date.today().weekday() == 0:
        print("  (周一) 回归基线请人工跑：cd backend && python tests/regression.py")
    print("\n".join(ok))
    if alerts:
        print("\n".join(alerts))
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
