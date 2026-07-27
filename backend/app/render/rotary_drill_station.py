"""代码渲染模板：LF13 自动化钻孔工作站（1920x1080，书页蓝白风格）。

phase="cycle"     四步循环俯视示意：装料→夹紧→钻孔→抛出，工件沿转台流转
phase="interlock" 安全联锁侧视演示：夹紧完成→钻缸方可下行；反例被联锁拦截
断言：cycle 工位角均分（构造）；interlock 时序上"夹紧完成时刻 < 钻缸下行时刻"，
反例段钻缸位移被锁死在上终端（逐帧校验，不通过不出图）。
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

BG = "#f4f6fa"
STEEL = "#7b86c8"      # 书页蓝紫
STEEL_D = "#4a5490"
DISK = "#7ec87e"       # 工件绿
DISK_D = "#3f8f3f"
RED = "#e05555"
INK = "#22263a"
DIM = "#6a7086"
OK = "#2f9e44"
WARN = "#e03131"

STEPS = ["① 装料", "② 夹紧", "③ 钻孔", "④ 抛出"]


def _step_bar(ax, active: int):
    for i, s in enumerate(STEPS):
        x = 5.0 + i * 2.6
        on = i == active
        ax.add_patch(plt.Rectangle((x, 0.55), 2.3, 0.85,
                     fc="#ffffff" if not on else STEEL,
                     ec=STEEL_D, lw=1.6, zorder=6))
        ax.text(x + 1.15, 0.97, s, ha="center", va="center", fontsize=17,
                color=INK if not on else "#ffffff",
                fontweight="bold" if on else "normal", zorder=7)


def _title(ax, main: str, sub: str):
    ax.text(0.9, 10.15, main, color=INK, fontsize=30, fontweight="bold", va="center")
    ax.text(0.9, 9.5, sub, color=DIM, fontsize=15.5, va="center")


def _render_cycle_frame(ax, u: float):
    """u∈[0,1)：一轮四步循环。转台圆心(9.6,5.6) 半径3.1，装料位0°(右)，
    加工位90°(上)，抛出位180°(左)。工件按步骤在位与位之间步进。"""
    cx, cy, R = 9.6, 5.6, 3.1
    ang = {"load": 0.0, "work": np.pi / 2, "eject": np.pi}
    # 断言：工位角均分 90°
    assert abs((ang["work"] - ang["load"]) - np.pi / 2) < 1e-9
    assert abs((ang["eject"] - ang["work"]) - np.pi / 2) < 1e-9

    # 转台
    ax.add_patch(plt.Circle((cx, cy), R + 0.55, fc="#e2e6f2", ec=STEEL_D, lw=2, zorder=1))
    ax.add_patch(plt.Circle((cx, cy), 0.42, fc=STEEL, ec=STEEL_D, lw=2, zorder=2))
    # 六个盘位浅槽
    for k in range(6):
        a = k * np.pi / 3
        ax.add_patch(plt.Circle((cx + R * np.cos(a), cy + R * np.sin(a)), 0.55,
                     fc="#eef1f8", ec="#c3c9de", lw=1.2, zorder=2))
    # 料仓（右）；装料段不画仓内盘——它正是滑出中的工件，避免重叠穿模
    ax.add_patch(plt.Circle((cx + R + 1.5, cy), 0.75, fc="#d9def0", ec=STEEL_D, lw=2, zorder=3))
    if u >= 0.25:
        ax.add_patch(plt.Circle((cx + R + 1.5, cy), 0.5, fc=DISK, ec=DISK_D, lw=1.6, zorder=4))
    ax.text(cx + R + 1.5, cy - 1.25, "料仓", ha="center", fontsize=14, color=DIM)
    # 钻机（上）
    ax.add_patch(plt.Rectangle((cx - 0.55, cy + R + 0.75), 1.1, 0.95,
                 fc=STEEL, ec=STEEL_D, lw=2, zorder=3))
    ax.text(cx + 0.85, cy + R + 1.22, "钻机", ha="left", va="center",
            fontsize=14, color=DIM)
    # 抛出滑道（左）
    ax.add_patch(plt.Rectangle((cx - R - 2.3, cy - 0.35), 1.6, 0.7,
                 fc="#e2e6f2", ec=STEEL_D, lw=1.6, zorder=2))
    ax.text(cx - R - 1.5, cy - 0.95, "成品滑道", ha="center", fontsize=13, color=DIM)

    # 四步时间片：0-0.25装料 0.25-0.5转位+夹紧 0.5-0.75钻孔 0.75-1抛出
    if u < 0.25:                       # 装料：盘从料仓滑入 0° 位
        step = 0
        t = u / 0.25
        px = cx + R + 1.5 - t * 1.5
        py = cy
        disk_a = None
    elif u < 0.5:                      # 前60%转位到加工位，后40%夹紧
        step = 1
        t = (u - 0.25) / 0.25
        a = (min(t, 0.6) / 0.6) * (np.pi / 2)
        px, py = cx + R * np.cos(a), cy + R * np.sin(a)
        disk_a = a
    elif u < 0.75:                     # 钻孔（盘停在 90°）
        step = 2
        px, py = cx, cy + R
        disk_a = np.pi / 2
    else:                              # 抛出：转到 180° 后滑出
        step = 3
        t = (u - 0.75) / 0.25
        if t < 0.5:
            a = np.pi / 2 + (t / 0.5) * (np.pi / 2)
            px, py = cx + R * np.cos(a), cy + R * np.sin(a)
        else:
            px = cx - R - (t - 0.5) / 0.5 * 1.7
            py = cy + 0.0 if False else cy
            # 出料段沿滑道水平移动
            px = cx - R - (t - 0.5) / 0.5 * 1.7
            py = cy
        disk_a = None

    # 工件盘
    ax.add_patch(plt.Circle((px, py), 0.52, fc=DISK, ec=DISK_D, lw=2, zorder=5))
    if step >= 2 and u < 0.75:
        # 钻孔中：中心孔+闪烁十字
        ax.add_patch(plt.Circle((px, py), 0.12, fc="#ffffff", ec=DISK_D, lw=1.5, zorder=6))
        blink = 0.5 + 0.5 * np.sin(u * 80)
        ax.plot([px - 0.3, px + 0.3], [py, py], color=RED, lw=2, alpha=blink, zorder=6)
        ax.plot([px, px], [py - 0.3, py + 0.3], color=RED, lw=2, alpha=blink, zorder=6)
    if step == 3 and u >= 0.875:
        ax.add_patch(plt.Circle((px, py), 0.12, fc="#ffffff", ec=DISK_D, lw=1.5, zorder=6))
    # 夹紧爪：工件到位后才出现并合拢（转位途中不画，避免动作与位置脱节）
    t1 = (u - 0.25) / 0.25 if 0.25 <= u < 0.5 else None
    show_claw = (step == 2) or (step == 1 and t1 is not None and t1 >= 0.6)
    if show_claw:
        closed = 1.0 if step == 2 else min(1.0, (t1 - 0.6) / 0.4)
        gap = 0.95 - 0.28 * closed
        for s_ in (-1, 1):
            th1, th2 = (100, 170) if s_ < 0 else (10, 80)
            arc = matplotlib.patches.Arc((cx, cy + R), gap * 2, gap * 2,
                                         theta1=th1, theta2=th2,
                                         color=RED, lw=5, zorder=6)
            ax.add_patch(arc)
    _step_bar(ax, step)


def _interlock_positions(u: float):
    """返回 (clamp_ext 0..1, drill_pos 0..1[0=上终端], violation, blocked)
    正例 0~0.62：夹紧完成(0.28)后钻缸才下行(0.32起)——断言校验时序。
    反例 0.62~1：夹紧中途(未完成)钻缸请求下行→联锁锁死在上终端。"""
    if u < 0.62:
        t = u / 0.62
        clamp = min(1.0, t / 0.45)
        if t < 0.52:
            drill = 0.0
        elif t < 0.78:
            drill = (t - 0.52) / 0.26
        else:
            drill = max(0.0, 1.0 - (t - 0.78) / 0.22)
        return clamp, drill, False, False
    t = (u - 0.62) / 0.38
    clamp = min(0.55, t * 1.3)          # 夹紧只走到 55%，未完成
    return clamp, 0.0, t > 0.35, t > 0.35   # 请求下钻但被锁


def _render_interlock_frame(ax, u: float):
    clamp, drill, violation, blocked = _interlock_positions(u)
    # 工作台与工件
    ax.add_patch(plt.Rectangle((7.6, 2.6), 4.2, 0.5, fc="#d9def0", ec=STEEL_D, lw=2))
    ax.add_patch(plt.Circle((9.7, 3.55), 0.62, fc=DISK, ec=DISK_D, lw=2, zorder=4))
    # 夹紧气缸（左，水平）
    ax.add_patch(plt.Rectangle((3.2, 3.1), 2.6, 0.9, fc="#ffffff", ec=STEEL_D, lw=2))
    rod = 2.2 * clamp
    ax.add_patch(plt.Rectangle((5.8, 3.4), 0.9 + rod, 0.3, fc=STEEL, ec=STEEL_D, lw=1.5))
    ax.add_patch(plt.Rectangle((6.7 + rod, 3.15), 0.35, 0.8, fc=RED, ec="#8f2f2f", lw=1.5))
    ax.text(4.5, 4.55, "夹紧气缸（单作用）", fontsize=14, color=DIM, ha="center")
    ax.text(4.5, 2.55, f"夹紧行程 {clamp*100:.0f}%", fontsize=13,
            color=OK if clamp >= 1.0 else DIM, ha="center")
    # 钻孔气缸（右上，垂直）
    ax.add_patch(plt.Rectangle((9.25, 7.6), 0.9, 2.2, fc="#ffffff", ec=STEEL_D, lw=2))
    dtravel = 2.1 * drill
    ax.add_patch(plt.Rectangle((9.55, 5.6 - dtravel + 2.0 - 2.0), 0.3, 2.0 - dtravel if False else 0.3 + 0, fc=STEEL, ec=STEEL_D, lw=1.2))
    # 活塞杆+钻机体
    ax.add_patch(plt.Rectangle((9.55, 7.6 - dtravel), 0.3, dtravel + 0.001 + 0.0, fc=STEEL, ec=STEEL_D, lw=1.2))
    ax.add_patch(plt.Rectangle((9.05, 6.6 - dtravel), 1.3, 1.0, fc=STEEL, ec=STEEL_D, lw=2, zorder=3))
    drill_tip_y = 6.6 - dtravel - 0.75
    ax.add_patch(plt.Polygon([[9.6, 6.6 - dtravel], [9.8, 6.6 - dtravel],
                              [9.7, drill_tip_y]], closed=True, fc="#555b66", zorder=3))
    # 上/下终端标线
    ax.plot([8.6, 11.2], [7.55, 7.55], color=OK, lw=1.6, ls=(0, (5, 4)))
    ax.text(11.35, 7.55, "上侧终端", fontsize=12.5, color=OK, va="center")
    ax.plot([8.6, 11.2], [4.7, 4.7], color=DIM, lw=1.4, ls=(0, (5, 4)))
    ax.text(11.35, 4.7, "下侧终端", fontsize=12.5, color=DIM, va="center")
    ax.text(10.0, 10.1, "钻孔气缸（双作用）", fontsize=14, color=DIM, ha="center")
    # 钻孔火花
    if drill > 0.92:
        for a in np.linspace(0, 2 * np.pi, 8, endpoint=False):
            ax.plot([9.7 + 0.2 * np.cos(a), 9.7 + 0.45 * np.cos(a)],
                    [3.9 + 0.2 * np.sin(a), 3.9 + 0.45 * np.sin(a)],
                    color="#f2a02c", lw=2)
    # 联锁状态牌
    ok_state = not violation
    ax.add_patch(plt.Rectangle((14.0, 6.3), 4.3, 2.6, fc="#ffffff",
                 ec=OK if ok_state else WARN, lw=3))
    ax.text(16.15, 8.35, "联锁状态", fontsize=15, color=INK, ha="center", fontweight="bold")
    if ok_state:
        msg = "夹紧完成 → 允许下钻" if clamp >= 1.0 else "待机：钻缸保持上侧终端"
        ax.text(16.15, 7.3, "√ 正常", fontsize=20, color=OK, ha="center", fontweight="bold")
        ax.text(16.15, 6.7, msg, fontsize=12.5, color=DIM, ha="center")
    else:
        ax.text(16.15, 7.3, "× 联锁拦截", fontsize=20, color=WARN, ha="center", fontweight="bold")
        ax.text(16.15, 6.7, "夹紧未完成，禁止下钻！", fontsize=12.5, color=WARN, ha="center")
        if blocked:
            ax.plot([9.0, 10.4], [7.0, 8.2], color=WARN, lw=5, alpha=0.85, zorder=7)
            ax.plot([9.0, 10.4], [8.2, 7.0], color=WARN, lw=5, alpha=0.85, zorder=7)
    ax.text(9.6, 1.35, "安全规则：夹紧毛坯件时，钻孔气缸必须位于上侧终端（联锁保护）",
            fontsize=15.5, color=INK, ha="center",
            bbox=dict(boxstyle="round,pad=0.45", fc="#ffffff", ec=STEEL_D, lw=1.5))


def render(out_path: str, phase: str = "cycle", duration: float = 18.0,
           fps: int = 24) -> dict:
    assert phase in ("cycle", "interlock"), "phase 取 cycle 或 interlock"
    # interlock 时序断言：夹紧完成时刻必须早于钻缸下行时刻
    if phase == "interlock":
        clamp_done = next(u for u in np.linspace(0, 0.62, 400)
                          if _interlock_positions(u)[0] >= 1.0)
        drill_start = next(u for u in np.linspace(0, 0.62, 400)
                           if _interlock_positions(u)[1] > 0.0)
        assert clamp_done < drill_start, "联锁时序不自洽：夹紧未完成钻缸已下行"
        for u in np.linspace(0.62, 0.999, 60):     # 反例段钻缸必须锁死上终端
            assert _interlock_positions(u)[1] == 0.0, "反例段钻缸未被锁死"

    frames_total = int(duration * fps)
    # 长片单循环（节奏舒缓、抽帧时序单调）；短片双循环
    loops = 1 if (phase != "cycle" or duration >= 20) else 2
    tmp = Path(tempfile.mkdtemp(prefix="lf13_"))
    for k in range(frames_total):
        u = (k / max(frames_total - 1, 1)) * loops % 1.0
        fig = plt.figure(figsize=(19.2, 10.8), dpi=100)
        fig.patch.set_facecolor(BG)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, 19.2); ax.set_ylim(0, 10.8)
        ax.set_facecolor(BG); ax.axis("off")
        if phase == "cycle":
            _title(ax, "自动钻孔工作站 · 四步循环",
                   "装料 → 夹紧 → 钻孔 → 抛出（旋转台流转，电气动控制）")
            _render_cycle_frame(ax, u)
        else:
            _title(ax, "安全联锁 · 夹紧与下钻的时序",
                   "正例：夹紧完成后钻缸才下行；反例：夹紧未完成，联锁禁止下钻")
            _render_interlock_frame(ax, u)
        fig.savefig(tmp / f"frame_{k:04d}.png", facecolor=BG)
        plt.close(fig)

    r = subprocess.run(
        ["ffmpeg", "-y", "-framerate", str(fps),
         "-i", str(tmp / "frame_%04d.png"),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "24",
         "-movflags", "+faststart", out_path],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    shutil.rmtree(tmp, ignore_errors=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 合成失败: {r.stderr[-300:]}")
    return {"frames": frames_total, "phase": phase,
            "resolution": "1920x1080", "physics_checked": True}
