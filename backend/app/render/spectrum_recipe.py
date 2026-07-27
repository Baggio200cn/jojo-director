"""代码渲染模板：菊花多光谱光配方动画（三通道 PWM，1920x1080）。

对应《菊花LED补光电路装置》说明书表2：每个生长阶段一段动画，
三条 PWM 波形（450nm蓝 / 660nm红 / 730nm远红）占空比即配方值。
断言：每通道波形均值 == 占空比（偏差 < 1e-3），不通过不出图。
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

BG = "#0f1115"
INK = "#ecebe6"
DIM = "#9aa0a8"
AXIS = "#3a3f4d"
AMBER = "#f2a02c"
CH = [  # 通道：名称 / 波长 / 颜色 / 作用
    ("蓝光", "450nm", "#3b82f6", "促进叶绿素合成"),
    ("红光", "660nm", "#ef4444", "光合作用峰值"),
    ("远红光", "730nm", "#c2255c", "调控开花诱导"),
]
N_PERIODS = 3


def _wave(ts, duty):
    return (np.mod(ts, 1.0) < duty).astype(float)


def render(out_path: str, stage: str = "育苗期",
           duty_blue: float = 40.0, duty_red: float = 50.0,
           duty_farred: float = 10.0, duration: float = 11.0,
           fps: int = 24) -> dict:
    duties = [float(duty_blue) / 100, float(duty_red) / 100, float(duty_farred) / 100]
    for d in duties:
        assert 0.02 <= d <= 0.98, "占空比取 2%~98%"
        ts_chk = np.linspace(0, N_PERIODS, 120_000, endpoint=False)
        assert abs(_wave(ts_chk, d).mean() - d) < 1e-3, "波形均值 != 占空比"

    frames_total = int(duration * fps)
    tmp = Path(tempfile.mkdtemp(prefix="spec_"))

    wx0, wx1 = 1.2, 9.6                      # 波形区横向
    rows_y = [7.05, 4.85, 2.65]              # 三条波形的低电平基线
    wh = 1.35                                 # 波形高度
    ts = np.linspace(0.0, N_PERIODS, 2400, endpoint=False)
    xs = wx0 + (ts / N_PERIODS) * (wx1 - wx0)
    waves = [_wave(ts, d) for d in duties]
    # 混合光颜色 = 各通道颜色按占空比加权
    rgb = np.zeros(3)
    for (_, _, c, _), d in zip(CH, duties):
        rgb += np.array(matplotlib.colors.to_rgb(c)) * d
    rgb = np.clip(rgb / max(rgb.max(), 1e-6) * 0.95, 0, 1)

    for k in range(frames_total):
        fig = plt.figure(figsize=(19.2, 10.8), dpi=100)
        fig.patch.set_facecolor(BG)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, 19.2); ax.set_ylim(0, 10.8)
        ax.set_facecolor(BG); ax.axis("off")

        ax.text(1.2, 10.05, f"菊花LED补光 · 光配方 · {stage}",
                color=INK, fontsize=33, fontweight="bold", va="center")
        ax.text(1.2, 9.35, "三通道 PWM 占空比 = 光谱配比（数据取自装置光配方存储单元 · 表2）",
                color=DIM, fontsize=15.5, va="center")

        prog = (k / max(frames_total - 1, 1)) * 2.0 % 1.0
        cx = wx0 + prog * (wx1 - wx0)
        t_now = prog * N_PERIODS

        for i, ((name, wl, color, effect), d, y0, wv) in enumerate(
                zip(CH, duties, rows_y, waves)):
            y1 = y0 + wh
            # 轴
            ax.plot([wx0 - 0.1, wx1 + 0.15], [y0, y0], color=AXIS, lw=1)
            # 波形
            wy = y0 + wv * wh
            ax.fill_between(xs, y0, wy, where=wv > 0.5, color=color,
                            alpha=0.13, step="pre")
            ax.plot(xs, wy, color=color, lw=2.4, drawstyle="steps-pre")
            # 占空比虚线
            ax.plot([wx0, wx1], [y0 + d * wh] * 2, color=color, lw=1.2,
                    ls=(0, (5, 5)), alpha=0.65)
            # 通道标签
            ax.text(wx1 + 0.3, y1 - 0.12, f"{name} {wl}",
                    color=color, fontsize=17, fontweight="bold", va="center")
            ax.text(wx1 + 0.3, y1 - 0.62,
                    f"占空比 {d*100:.0f}%  · {effect}",
                    color=DIM, fontsize=13, va="center")
            # 该通道 LED（亮度=占空比，指针处通断闪烁叠加）
            on_now = _wave(np.array([t_now]), d)[0] > 0.5
            lx, ly = 14.35, y0 + wh / 2
            glow = d * (1.0 if on_now else 0.72)
            for r_, a_ in [(0.62, 0.18 * glow), (0.46, 0.3 * glow)]:
                ax.add_patch(plt.Circle((lx, ly), r_, color=color, alpha=a_, zorder=4))
            ax.add_patch(plt.Circle((lx, ly), 0.3, color=color,
                                    alpha=max(glow, 0.12), zorder=5))
            ax.add_patch(plt.Circle((lx, ly), 0.3, fill=False, color="#555b66",
                                    lw=1.6, zorder=6))
        # 扫描指针（贯穿三条波形）
        ax.plot([cx, cx], [rows_y[2] - 0.2, rows_y[0] + wh + 0.2],
                color=INK, lw=1.2, alpha=0.6)

        # 混合光大圆
        mx, my = 17.0, 5.5
        pulse = 0.85 + 0.15 * np.sin(k / fps * 2 * np.pi)
        for r_, a_ in [(1.55, 0.10), (1.2, 0.20), (0.95, 0.35)]:
            ax.add_patch(plt.Circle((mx, my), r_, color=rgb, alpha=a_ * pulse, zorder=4))
        ax.add_patch(plt.Circle((mx, my), 0.7, color=rgb, alpha=0.95, zorder=5))
        ax.text(mx, my - 2.1, "混合补光光谱", color=INK, fontsize=16,
                ha="center", fontweight="bold")
        ax.text(mx, my - 2.55, "三通道按配方叠加", color=DIM, fontsize=12.5, ha="center")

        ax.text(1.2, 1.15,
                f"{stage}配方：蓝 {duties[0]*100:.0f}%  ·  红 {duties[1]*100:.0f}%"
                f"  ·  远红 {duties[2]*100:.0f}%      "
                f"（微控制器读取 EEPROM 光配方 → SPI 配置 PWM 驱动 → 三路独立输出）",
                color=DIM, fontsize=15.5, va="center")

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
    return {"frames": frames_total, "stage": stage,
            "recipe": [d * 100 for d in duties], "resolution": "1920x1080",
            "physics_checked": True}
