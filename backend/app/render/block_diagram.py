"""代码渲染模板：菊花LED补光装置系统框图·信号流动画（1920x1080）。

对应说明书图1：A环境光传感器 / C图像采集 → B微控制器 → D PWM驱动 → E1/E2/E3。
信号脉冲沿连线分五个阶段流动（ADC→I2C→MCU处理→SPI→三路输出）。
断言：所有连线端点必须精确落在框边缘（构造校验），不通过不出图。
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
import numpy as np

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

BG = "#0f1115"
INK = "#ecebe6"
DIM = "#9aa0a8"
BOX = "#22272e"
EDGE = "#4a5160"
PULSE = "#22d3ee"
AMBER = "#f2a02c"

# 框：key -> (cx, cy, w, h, 标题, 副标, 边色)
BOXES = {
    "A": (2.6, 7.3, 3.2, 1.5, "环境光传感器", "实时光强 · ADC", EDGE),
    "C": (2.6, 3.7, 3.2, 1.5, "图像采集单元", "OV2640 · 生长阶段识别", EDGE),
    "B": (8.3, 5.5, 3.4, 2.0, "微控制器", "查 EEPROM 光配方", AMBER),
    "D": (13.2, 5.5, 2.9, 1.6, "PWM 调光驱动", "A4950 · 三通道", EDGE),
    "E1": (17.3, 7.6, 2.6, 1.25, "450nm 蓝光", "促叶绿素合成", "#3b82f6"),
    "E2": (17.3, 5.5, 2.6, 1.25, "660nm 红光", "光合作用峰值", "#ef4444"),
    "E3": (17.3, 3.4, 2.6, 1.25, "730nm 远红光", "调控开花诱导", "#c2255c"),
}
# 连线：起框右缘 -> 终框左缘，(起, 终, 标签)
WIRES = [
    ("A", "B", "ADC"), ("C", "B", "I2C"),
    ("B", "D", "SPI"),
    ("D", "E1", ""), ("D", "E2", ""), ("D", "E3", ""),
]
# 阶段（各占动画的时间窗）：激活哪些线/框 + 底部说明
PHASES = [
    (0.00, 0.20, ["A->B"], "① 环境光传感器实时检测光强，经 ADC 送入微控制器"),
    (0.20, 0.40, ["C->B"], "② OV2640 定时采集菊花图像，I2C 传输，识别生长阶段"),
    (0.40, 0.60, [], "③ 微控制器从 EEPROM 读取对应生长阶段的光配方"),
    (0.60, 0.80, ["B->D"], "④ SPI 配置 PWM 驱动电路的三通道占空比"),
    (0.80, 1.00, ["D->E1", "D->E2", "D->E3"], "⑤ 三路独立驱动 LED 阵列，输出配方光谱"),
]


def _edge_pt(key, side):
    cx, cy, w, h, *_ = BOXES[key]
    return (cx + w / 2, cy) if side == "R" else (cx - w / 2, cy)


def render(out_path: str, duration: float = 12.0, fps: int = 24) -> dict:
    # 断言：连线端点在框边缘（构造校验）
    for s, t, _ in WIRES:
        p0, p1 = _edge_pt(s, "R"), _edge_pt(t, "L")
        assert abs(p0[0] - (BOXES[s][0] + BOXES[s][2] / 2)) < 1e-9
        assert abs(p1[0] - (BOXES[t][0] - BOXES[t][2] / 2)) < 1e-9

    frames_total = int(duration * fps)
    tmp = Path(tempfile.mkdtemp(prefix="blk_"))

    for k in range(frames_total):
        u = k / max(frames_total - 1, 1)
        phase = next((p for p in PHASES if p[0] <= u < p[1]), PHASES[-1])
        active = phase[2]
        caption = phase[3]
        # 阶段内进度 0..1
        pp = (u - phase[0]) / max(phase[1] - phase[0], 1e-9)

        fig = plt.figure(figsize=(19.2, 10.8), dpi=100)
        fig.patch.set_facecolor(BG)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, 19.2); ax.set_ylim(0, 10.8)
        ax.set_facecolor(BG); ax.axis("off")

        ax.text(1.0, 10.05, "菊花LED补光装置 · 系统框图与信号流",
                color=INK, fontsize=32, fontweight="bold", va="center")
        ax.text(1.0, 9.4, "闭环控制：感知 → 识别 → 查配方 → PWM 调光 → 多光谱输出",
                color=DIM, fontsize=15, va="center")

        # 连线
        for s, t, lab in WIRES:
            p0, p1 = _edge_pt(s, "R"), _edge_pt(t, "L")
            key = f"{s}->{t}"
            on = key in active
            ax.annotate("", xy=p1, xytext=p0,
                        arrowprops=dict(arrowstyle="-|>", lw=2.2 if on else 1.4,
                                        color=PULSE if on else EDGE,
                                        alpha=0.95 if on else 0.55))
            if lab:
                mx_, my_ = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2 + 0.28
                ax.text(mx_, my_, lab, color=PULSE if on else DIM,
                        fontsize=13, ha="center",
                        fontweight="bold" if on else "normal")
            # 脉冲点沿线移动
            if on:
                px = p0[0] + (p1[0] - p0[0]) * pp
                py = p0[1] + (p1[1] - p0[1]) * pp
                ax.plot(px, py, marker="o", ms=11, color=PULSE, zorder=8)
                ax.plot(px, py, marker="o", ms=20, color=PULSE, alpha=0.25, zorder=7)

        # 框
        mcu_busy = phase[3].startswith("③")
        for key, (cx, cy, w, h, t1, t2, ec) in BOXES.items():
            lit = any(key in a.split("->") for a in active) or (key == "B" and mcu_busy)
            face = BOX
            if key.startswith("E") and active and f"D->{key}" in active:
                face = matplotlib.colors.to_rgb(ec) + (0.10 + 0.12 * np.sin(pp * np.pi),)
            box = FancyBboxPatch((cx - w / 2, cy - h / 2), w, h,
                                 boxstyle="round,pad=0.06,rounding_size=0.12",
                                 fc=face, ec=ec,
                                 lw=2.6 if lit else 1.5, zorder=3,
                                 alpha=1.0)
            ax.add_patch(box)
            ax.text(cx, cy + 0.22, t1, color=INK, fontsize=16.5,
                    ha="center", fontweight="bold", zorder=4)
            ax.text(cx, cy - 0.32, t2, color=DIM, fontsize=12, ha="center", zorder=4)
            if key == "B" and mcu_busy:
                gl = 0.25 + 0.2 * np.sin(pp * 2 * np.pi * 2)
                ax.add_patch(FancyBboxPatch((cx - w / 2, cy - h / 2), w, h,
                             boxstyle="round,pad=0.06,rounding_size=0.12",
                             fc="none", ec=AMBER, lw=4, alpha=gl, zorder=5))

        # 底部阶段说明条
        ax.add_patch(plt.Rectangle((0, 0.55), 19.2, 1.05, color="black", alpha=0.45))
        ax.text(9.6, 1.07, caption, color=INK, fontsize=18, ha="center",
                va="center", fontweight="bold")

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
    return {"frames": frames_total, "phases": len(PHASES),
            "resolution": "1920x1080", "geometry_checked": True}
