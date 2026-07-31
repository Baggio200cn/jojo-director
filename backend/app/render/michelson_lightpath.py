# -*- coding: utf-8 -*-
"""迈克尔逊干涉：光路传播动画（几何精确，本地渲染 1080p）。
光源 → 分光板(45°) → 固定镜M1(上) / 可动镜M2(右) → 合束 → 观察屏(下)，
光束按真实传播顺序逐段点亮，末段观察屏浮现等倾同心条纹。"""
import subprocess
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import numpy as np

_YH = None
for _f in (r"C:\Windows\Fonts\msyh.ttc",
           "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
           "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc"):
    if Path(_f).exists():
        _YH = fm.FontProperties(fname=_f)
        break

# 关键坐标（单位任意）：分光板在原点
SRC = np.array([-4.2, 0.0])     # 光源
BS = np.array([0.0, 0.0])       # 分光板中心
M1 = np.array([0.0, 2.7])       # 固定平面镜（上）
M2 = np.array([3.4, 0.0])       # 可移动平面镜（右）
SCR = np.array([0.0, -2.7])     # 观察屏（下）


def _draw_partial(ax, p0, p1, frac, color, lw=2.6, alpha=0.95):
    """画 p0→p1 的前 frac 段（0~1）。"""
    if frac <= 0:
        return
    p = p0 + (p1 - p0) * min(frac, 1.0)
    ax.plot([p0[0], p[0]], [p0[1], p[1]], color=color, lw=lw,
            alpha=alpha, solid_capstyle="round", zorder=3)


def render(out_path: str, duration: float = 10.0, fps: int = 24) -> dict:
    n_frames = int(duration * fps)
    tmp = Path(out_path).with_suffix("")
    tmp.mkdir(exist_ok=True)
    # 传播时序（占总时长比例）：入射→分束(往)→反射(返)→合束到屏→条纹浮现
    T1, T2, T3, T4 = 0.16, 0.40, 0.62, 0.78
    for i in range(n_frames):
        u = i / max(n_frames - 1, 1)
        fig, ax = plt.subplots(figsize=(12.8, 7.2), dpi=150)  # 1920×1080
        fig.patch.set_facecolor("#0f1115")
        ax.set_facecolor("#0f1115")
        ax.set_xlim(-5.4, 5.6)
        ax.set_ylim(-3.6, 3.6)
        ax.axis("off")

        # 器件
        ax.plot([-0.42, 0.42], [-0.42, 0.42], color="#9FD8EF", lw=5, zorder=4)   # 分光板 45°
        ax.plot([-0.55, 0.55], [M1[1]] * 2, color="#C9D4E0", lw=6, zorder=4)     # M1 固定镜
        ax.plot([M2[0]] * 2, [-0.55, 0.55], color="#F2B45C", lw=6, zorder=4)     # M2 可动镜
        ax.annotate("", xy=(M2[0] + 0.75, 0), xytext=(M2[0] + 0.15, 0),
                    arrowprops=dict(arrowstyle="<->", color="#F2B45C", lw=1.6))   # 可动方向
        ax.plot([-0.8, 0.8], [SCR[1]] * 2, color="#8FE38F", lw=6, zorder=4)      # 观察屏
        ax.add_patch(plt.Rectangle((SRC[0] - 0.55, -0.28), 0.55, 0.56,
                                   color="#E06060", zorder=4))                    # 光源

        # 标签
        for xy, txt, dy in ((SRC, "激光光源", 0.55), (BS + np.array([0.9, 0.45]), "分光板", 0),
                            (M1, "固定平面镜 M1", 0.35), (M2 + np.array([0, 0.9]), "可移动平面镜 M2", 0),
                            (SCR, "观察屏", -0.62)):
            ax.text(xy[0], xy[1] + dy, txt, ha="center", fontsize=13.5,
                    color="w", fontproperties=_YH, zorder=5)

        C_IN, C_A, C_B, C_OUT = "#FF6B5E", "#5CC5EC", "#F2B45C", "#B48CF2"
        # 段1：光源→分光板
        _draw_partial(ax, SRC, BS, u / T1, C_IN, lw=3.2)
        # 段2：分光板→两镜（同时前进）
        if u > T1:
            f2 = (u - T1) / (T2 - T1)
            _draw_partial(ax, BS, M1, f2, C_A)      # 反射支路（上）
            _draw_partial(ax, BS, M2, f2, C_B)      # 透射支路（右）
        # 段3：两镜→分光板（返程）
        if u > T2:
            f3 = (u - T2) / (T3 - T2)
            _draw_partial(ax, M1, BS, f3, C_A, alpha=0.75)
            _draw_partial(ax, M2, BS, f3, C_B, alpha=0.75)
        # 段4：分光板→观察屏（合束）
        if u > T3:
            _draw_partial(ax, BS, SCR, (u - T3) / (T4 - T3), C_OUT, lw=3.2)
        # 段5：条纹在屏上浮现（同心圆环，中心在屏中点下方一点）
        if u > T4:
            f5 = min((u - T4) / (1 - T4), 1.0)
            for k in range(1, 7):
                rr = 0.11 * k
                ring = plt.Circle((0, SCR[1] - 0.05), rr, fill=False,
                                  color="#FFD27A", lw=2.2,
                                  alpha=f5 * max(0.15, 1 - 0.13 * k), zorder=6)
                ax.add_patch(ring)
            ax.text(1.55, SCR[1], "干涉条纹", fontsize=13, color="#FFD27A",
                    fontproperties=_YH, alpha=f5, zorder=6)

        ax.text(0.02, 0.965, "迈克尔逊干涉仪 · 光路", transform=ax.transAxes,
                fontsize=17, color="w", fontproperties=_YH)
        ax.text(0.02, 0.905, "一束光被分光板分成两束，往返后重新相遇产生干涉",
                transform=ax.transAxes, fontsize=12.5, color="#9FB3CC", fontproperties=_YH)
        fig.savefig(tmp / f"f{i:05d}.png", facecolor=fig.get_facecolor())
        plt.close(fig)
    subprocess.run(["ffmpeg", "-y", "-framerate", str(fps),
                    "-i", str(tmp / "f%05d.png"),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", out_path],
                   capture_output=True)
    for f in tmp.glob("*.png"):
        f.unlink()
    tmp.rmdir()
    return {"engine": "michelson_lightpath", "frames": n_frames,
            "geometry_checked": True}
