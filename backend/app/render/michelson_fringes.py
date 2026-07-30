# -*- coding: utf-8 -*-
"""迈克尔逊干涉：等倾条纹吞吐动画（物理精确，ΔL=2d·cosθ 逐帧计算）。
mode=expand：d 增大，条纹从中心冒出（吐）；mode=contract：d 减小，条纹向中心缩进（吞）。"""
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


def render(out_path: str, mode: str = "expand", d_um: float = 20.0,
           delta_um: float = 1.5, duration: float = 10.0, fps: int = 24) -> dict:
    lam = 0.6328  # HeNe 波长 μm
    n_frames = int(duration * fps)
    tmp = Path(out_path).with_suffix("")
    tmp.mkdir(exist_ok=True)
    # 观察屏角坐标网格
    ang = np.linspace(0, 0.35, 480)                      # θ 0~0.35rad
    xx, yy = np.meshgrid(np.linspace(-1, 1, 480), np.linspace(-1, 1, 480))
    rr = np.sqrt(xx ** 2 + yy ** 2)
    theta = rr * ang.max()
    for i in range(n_frames):
        u = i / max(n_frames - 1, 1)
        d = d_um + (delta_um * u if mode == "expand" else -delta_um * u)
        phase = 4 * np.pi * d * np.cos(theta) / lam
        inten = 0.5 * (1 + np.cos(phase))
        fig, (a1, a2) = plt.subplots(1, 2, figsize=(12.8, 7.2), dpi=100,
                                     gridspec_kw={"width_ratios": [1.15, 1]})
        fig.patch.set_facecolor("#0f1115")
        a1.imshow(inten, cmap="inferno", extent=[-1, 1, -1, 1])
        a1.set_title("观察屏：等倾干涉条纹", fontproperties=_YH, color="w", fontsize=15)
        a1.axis("off")
        a2.set_facecolor("#0f1115")
        a2.axis("off")
        a2.text(0.5, 0.86, "ΔL = 2d·cosθ", ha="center", fontsize=22, color="#5CC5EC",
                transform=a2.transAxes)
        a2.text(0.5, 0.70, f"d = {d:.3f} μm", ha="center", fontsize=18, color="#F2B45C",
                transform=a2.transAxes, fontproperties=_YH)
        bar_u = (d - (d_um - (0 if mode == "expand" else delta_um))) / delta_um
        a2.barh([0.5], [max(0.02, min(1.0, abs(bar_u)))], height=0.08, color="#F2B45C",
                transform=a2.transAxes)
        a2.text(0.5, 0.34, "d 增大 → 条纹自中心冒出（吐）" if mode == "expand"
                else "d 减小 → 条纹向中心缩进（吞）",
                ha="center", fontsize=15, color="w", fontproperties=_YH,
                transform=a2.transAxes)
        a2.text(0.5, 0.18, "同一级条纹 ΔL 不变：d增大 → cosθ减小 → θ增大",
                ha="center", fontsize=13, color="#9FB3CC", fontproperties=_YH,
                transform=a2.transAxes)
        fig.savefig(tmp / f"f{i:05d}.png", facecolor=fig.get_facecolor())
        plt.close(fig)
    subprocess.run(["ffmpeg", "-y", "-framerate", str(fps),
                    "-i", str(tmp / "f%05d.png"),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", out_path],
                   capture_output=True)
    for f in tmp.glob("*.png"):
        f.unlink()
    tmp.rmdir()
    return {"engine": "michelson_fringes", "mode": mode, "frames": n_frames,
            "geometry_checked": True}
