"""代码渲染模板：PWM 调光原理动画（波形零容忍版，1920x1080）。

每一帧的方波都由占空比参数解析生成：
  - 高电平时间 = duty × 周期（构造保证，断言偏差 < 1e-9）
  - 波形采样均值 == duty × I_peak（断言偏差 < 1e-3）
画面三要素：滚动扫描的 PWM 方波 + "实际(慢放)"闪烁 LED + "人眼所见"恒亮 LED，
底部给出 平均电流 = 占空比 × 峰值电流 的实时算式。
风格对齐教材：暗底、青色波形、橙色 LED、雅黑中文。
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
WAVE = "#22d3ee"      # 青色方波
AVG = "#f2a02c"       # 琥珀平均线
LED_ON = "#ffb14d"
LED_OFF = "#2a2e36"
AXIS = "#3a3f4d"
INK = "#ecebe6"
DIM = "#9aa0a8"

I_PEAK_MA = 350.0      # 峰值电流示例值 (mA)
N_PERIODS = 4          # 窗口内显示的周期数


def _square_wave(ts: np.ndarray, duty: float) -> np.ndarray:
    """周期归一化方波：t∈[0,1) 内 t<duty 为 1，否则 0。"""
    return (np.mod(ts, 1.0) < duty).astype(float)


def render(out_path: str, duty: float = 50.0, duration: float = 10.0,
           fps: int = 24) -> dict:
    d = float(duty) / 100.0
    assert 0.02 <= d <= 0.98, "占空比取 2%~98%"
    # ── 物理断言（不通过不出图）──
    period, high_time = 1.0, d * 1.0
    assert abs(high_time / period - d) < 1e-9, "高电平占比 != 占空比"
    ts_check = np.linspace(0, N_PERIODS, 200_000, endpoint=False)
    assert abs(_square_wave(ts_check, d).mean() - d) < 1e-3, "波形均值 != 占空比"

    i_avg = d * I_PEAK_MA
    frames_total = int(duration * fps)
    sweeps = 2.0                          # 全片扫描指针走 2 遍窗口
    tmp = Path(tempfile.mkdtemp(prefix="pwm_"))

    # 波形几何（画布坐标：x 0~19.2, y 0~10.8）
    wx0, wx1 = 1.3, 12.3                  # 波形区横向
    y_lo, y_hi = 3.2, 6.6                 # 低/高电平纵坐标（上移填补留白）
    y_avg = y_lo + d * (y_hi - y_lo)      # 平均电流线（几何=物理）
    ts = np.linspace(0.0, N_PERIODS, 3000, endpoint=False)
    wave = _square_wave(ts, d)
    wave_x = wx0 + (ts / N_PERIODS) * (wx1 - wx0)
    wave_y = y_lo + wave * (y_hi - y_lo)

    for k in range(frames_total):
        fig = plt.figure(figsize=(19.2, 10.8), dpi=100)
        fig.patch.set_facecolor(BG)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, 19.2); ax.set_ylim(0, 10.8)
        ax.set_facecolor(BG); ax.axis("off")

        # 标题与副标
        ax.text(1.3, 9.7, f"PWM 调光原理 · 占空比 {duty:.0f}%",
                color=INK, fontsize=34, fontweight="bold", va="center")
        ax.text(1.3, 8.9, "脉冲宽度调制：快速通断，用“亮的时间占比”控制亮度",
                color=DIM, fontsize=17, va="center")

        # 波形坐标轴
        ax.plot([wx0 - 0.15, wx1 + 0.3], [y_lo, y_lo], color=AXIS, lw=1.2)
        ax.plot([wx0 - 0.15, wx0 - 0.15], [y_lo - 0.15, y_hi + 0.5],
                color=AXIS, lw=1.2)
        ax.text(wx0 - 0.35, y_hi, "I_peak", color=DIM, fontsize=14,
                ha="right", va="center")
        ax.text(wx0 - 0.35, y_lo, "0", color=DIM, fontsize=14,
                ha="right", va="center")
        ax.text(wx1 + 0.35, y_lo - 0.05, "t", color=DIM, fontsize=15, va="top")

        # PWM 方波（青色）+ 高电平区间填充
        ax.fill_between(wave_x, y_lo, wave_y, where=wave > 0.5,
                        color=WAVE, alpha=0.10, step="pre")
        ax.plot(wave_x, wave_y, color=WAVE, lw=2.6, drawstyle="steps-pre")

        # 平均电流虚线（位置由 duty 解析决定）
        ax.plot([wx0, wx1], [y_avg, y_avg], color=AVG, lw=2.2,
                ls=(0, (7, 5)))
        ax.text(wx1 + 0.25, y_avg, f"I_avg = {i_avg:.0f} mA",
                color=AVG, fontsize=16, va="center", fontweight="bold")

        # 高电平宽度标注（第一个周期上方）+ 周期 T 标注
        seg_w = (wx1 - wx0) / N_PERIODS
        ax.annotate("", xy=(wx0 + d * seg_w, y_hi + 0.32),
                    xytext=(wx0, y_hi + 0.32),
                    arrowprops=dict(arrowstyle="<->", color=INK, lw=1.4))
        ax.text(wx0 + d * seg_w / 2, y_hi + 0.62, f"导通 {duty:.0f}%",
                color=INK, fontsize=14, ha="center")
        ax.annotate("", xy=(wx0 + seg_w, y_lo - 0.55),
                    xytext=(wx0, y_lo - 0.55),
                    arrowprops=dict(arrowstyle="<->", color=DIM, lw=1.3))
        ax.text(wx0 + seg_w / 2, y_lo - 0.95, "周期 T（频率>100Hz，人眼不见闪烁）",
                color=DIM, fontsize=13, ha="center")

        # 扫描指针 + 当前通断状态
        prog = (k / max(frames_total - 1, 1)) * sweeps % 1.0
        cx = wx0 + prog * (wx1 - wx0)
        t_now = prog * N_PERIODS
        on_now = _square_wave(np.array([t_now]), d)[0] > 0.5
        ax.plot([cx, cx], [y_lo - 0.25, y_hi + 0.25], color=INK,
                lw=1.4, alpha=0.75)

        # 右侧两颗 LED：上=实际(慢放) 随指针闪烁；下=人眼所见 恒亮 duty
        def led(cx_, cy_, bright, label, sub):
            glow = LED_ON if bright > 0.03 else LED_OFF
            for r_, a_ in [(1.15, 0.16 * bright), (0.85, 0.30 * bright)]:
                ax.add_patch(plt.Circle((cx_, cy_), r_, color=LED_ON,
                                        alpha=max(a_, 0.0), zorder=4))
            ax.add_patch(plt.Circle((cx_, cy_), 0.58,
                                    color=glow, alpha=max(bright, 0.10) if bright > 0.03 else 1.0,
                                    zorder=5))
            ax.add_patch(plt.Circle((cx_, cy_), 0.58, fill=False,
                                    color="#555b66", lw=2, zorder=6))
            ax.text(cx_, cy_ - 1.15, label, color=INK, fontsize=17,
                    ha="center", fontweight="bold")
            ax.text(cx_, cy_ - 1.62, sub, color=DIM, fontsize=13, ha="center")

        led(15.6, 6.6, 1.0 if on_now else 0.0, "实际状态（慢放）",
            "通 / 断 跟随方波")
        led(15.6, 2.9, d, "人眼所见（>100Hz）", f"稳定亮度 ≈ {duty:.0f}%")

        # 底部算式
        ax.text(1.3, 1.2,
                f"平均电流  I_avg = D × I_peak = {d:.2f} × {I_PEAK_MA:.0f} mA = {i_avg:.0f} mA"
                f"      （亮度由导通时间占比决定，电流峰值不变、颜色不漂移）",
                color=DIM, fontsize=16, va="center")

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
    return {"frames": frames_total, "duty": duty, "i_avg_ma": round(i_avg, 1),
            "resolution": "1920x1080", "physics_checked": True}


def render_sweep(out_path: str, duty_from: float = 25.0, duty_to: float = 90.0,
                 steps: int = 3, duration: float = 12.0, fps: int = 24) -> dict:
    """占空比阶梯扫描：duty_from→duty_to 分档渲染后拼接；每段断言照常生效。"""
    import shutil as _sh
    import subprocess as _sp
    import tempfile as _tf
    duties = np.linspace(float(duty_from), float(duty_to), max(2, int(steps)))
    tmp = Path(_tf.mkdtemp(prefix="pwmsweep_"))
    seg_dur = max(3.0, duration / len(duties))
    parts = []
    for j, d in enumerate(duties):
        part = tmp / f"part{j}.mp4"
        render(str(part), duty=float(d), duration=seg_dur, fps=fps)
        parts.append(part)
    lst = tmp / "list.txt"
    lines = "".join("file " + repr(x.as_posix()) + chr(10) for x in parts)
    lst.write_text(lines, encoding="utf-8")
    r = _sp.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                 "-c", "copy", out_path],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
    _sh.rmtree(tmp, ignore_errors=True)
    if r.returncode != 0:
        raise RuntimeError(f"扫描拼接失败: {r.stderr[-200:]}")
    return {"duties": [round(float(d), 1) for d in duties], "seg_seconds": seg_dur}
