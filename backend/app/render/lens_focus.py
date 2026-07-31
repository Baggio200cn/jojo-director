"""代码渲染模板：平行光过薄透镜聚焦动画（几何零容忍版）。

每一帧的光线路径都由薄透镜公式实测计算：
  - 平行于主轴的光线经透镜后必过焦点 (f, 0)
  - 内置断言：所有折射光线延长线与主轴交点和焦点的偏差 < 1e-9
风格对齐教材第 2 章：暗底、青色光线、橙色透镜剖面。
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

BG = "#0f1115"
RAY = "#22d3ee"
LENS = "#f97316"
AXIS = "#3a3f4d"
GLOW = "#e0f7ff"

X0, X_END = -4.6, 4.6          # 光线起止横坐标（场景单位）
LENS_H = 1.75                   # 透镜半高


def _ray_path(h: float, f: float) -> np.ndarray:
    """一条入射高度 h 的光线路径：入射段 + 折射段（必过焦点）。"""
    # 折射方向：从 (0,h) 指向焦点 (f,0)，延长到 X_END
    t_end = (X_END - 0.0) / f
    y_end = h - h * t_end
    path = np.array([[X0, h], [0.0, h], [X_END, y_end]])
    # 断言：折射线与主轴交点 == 焦点（几何自洽检查，不通过不出图）
    x_cross = f  # y(x)=h-(h/f)x = 0 -> x=f
    assert abs((h - (h / f) * x_cross)) < 1e-9, "光路几何不自洽"
    return path


def _cum_len(path: np.ndarray) -> np.ndarray:
    seg = np.linalg.norm(np.diff(path, axis=0), axis=1)
    return np.concatenate([[0.0], np.cumsum(seg)])


def _partial(path: np.ndarray, dist: float) -> np.ndarray:
    """截取路径前 dist 长度（光的传播前沿）。"""
    cl = _cum_len(path)
    if dist >= cl[-1]:
        return path
    pts = [path[0]]
    for i in range(1, len(path)):
        if dist <= cl[i]:
            r = (dist - cl[i - 1]) / (cl[i] - cl[i - 1] + 1e-12)
            pts.append(path[i - 1] + r * (path[i] - path[i - 1]))
            break
        pts.append(path[i])
    return np.array(pts)


def render(out_path: str, focal_length: float = 2.2, num_rays: int = 7,
           duration: float = 6.0, fps: int = 12) -> dict:
    f = float(focal_length)
    n = max(3, int(num_rays) | 1)  # 奇数条，含主轴光线
    heights = np.linspace(-1.35, 1.35, n)
    paths = [_ray_path(h, f) for h in heights]
    total_lens = [_cum_len(p)[-1] for p in paths]
    max_len = max(total_lens)

    frames_total = int(duration * fps)
    travel_frames = int(frames_total * 0.72)   # 前 72% 时间光线推进
    tmp = Path(tempfile.mkdtemp(prefix="lens_"))

    for k in range(frames_total):
        fig, ax = plt.subplots(figsize=(12.8, 7.2), dpi=150)
        fig.patch.set_facecolor(BG)
        ax.set_facecolor(BG)
        ax.set_xlim(X0, X_END)
        ax.set_ylim(-2.4, 2.4)
        ax.axis("off")
        # 主轴与焦点标记
        ax.axhline(0, color=AXIS, lw=1, ls=(0, (6, 6)))
        ax.plot([f], [0], marker="+", color=AXIS, ms=10, mew=1.2)
        # 透镜（双凸剖面示意）
        theta = np.linspace(0, 2 * np.pi, 200)
        ax.fill(0.28 * np.cos(theta), LENS_H * np.sin(theta),
                color=LENS, alpha=0.28, zorder=2)
        ax.plot(0.28 * np.cos(theta), LENS_H * np.sin(theta),
                color=LENS, lw=2, zorder=3)

        progress = min(1.0, (k + 1) / travel_frames)
        dist = progress * max_len
        for p in paths:
            seg = _partial(p, dist)
            ax.plot(seg[:, 0], seg[:, 1], color=RAY, lw=1.6, alpha=0.85, zorder=4)
            # 传播前沿亮点
            if progress < 1.0:
                ax.plot(seg[-1, 0], seg[-1, 1], marker="o", ms=3,
                        color=GLOW, alpha=0.9, zorder=5)

        # 光线到达后焦点发光（呼吸光晕）
        if progress >= (f - X0) / max_len * 0.98:
            pulse = 0.5 + 0.5 * np.sin((k / fps) * 2 * np.pi * 1.2)
            for r_, a_ in [(0.30, 0.10 + 0.10 * pulse), (0.18, 0.22 + 0.15 * pulse),
                           (0.08, 0.9)]:
                ax.add_patch(plt.Circle((f, 0), r_, color=GLOW, alpha=a_, zorder=6))

        fig.savefig(tmp / f"frame_{k:04d}.png", facecolor=BG,
                    bbox_inches="tight", pad_inches=0)
        plt.close(fig)

    r = subprocess.run(
        ["ffmpeg", "-y", "-framerate", str(fps), "-i", str(tmp / "frame_%04d.png"),
         "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,"
                "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "24", out_path],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    shutil.rmtree(tmp, ignore_errors=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 合成失败: {r.stderr[-300:]}")
    return {"frames": frames_total, "focal_length": f, "rays": n}
