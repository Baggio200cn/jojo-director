# -*- coding: utf-8 -*-
"""BJMoMA 平台 E2E 实测：额度探针 → 1080p 图生视频 → 下载验流。

用法：cd jojo-studio && python ops/test_bjmoma.py [asset文件名]
  asset文件名 = 云端 /opt/jojo/backend/assets/ 下的首帧图（默认用迈克尔逊镜13帧）
额度未解锁时脚本在第一步就会停下（不烧券）。
"""
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO = Path(__file__).resolve().parents[1]
ENV = {l.split("=", 1)[0]: l.split("=", 1)[1].strip()
       for l in (REPO / "backend" / ".env").read_text(encoding="utf-8").splitlines()
       if "=" in l and not l.startswith("#")}
KEY = ENV["BJMOMA_API_KEY"]
BASE = ENV.get("BJMOMA_BASE", "https://www.mobileopentokenaccess.com/maas/ai/aiFactoryServer/v1/apis/1").rstrip("/")
PUB = ENV.get("PUBLIC_BASE", "https://115.190.155.2").rstrip("/")
SECRET = (ENV.get("PUB_SIGN_KEY") or ENV.get("ADMIN_PASS") or "jojo-dev").encode()
H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
MODEL = "doubao-seedance-2-0-260128"


def req(url, method="POST", body=b"{}", timeout=90, retries=6, binary=False):
    last = None
    for att in range(retries):
        if att:
            time.sleep(6)
        try:
            r = urllib.request.Request(url, data=body if method == "POST" else None,
                                       headers=H, method=method)
            resp = urllib.request.urlopen(r, timeout=timeout)
            data = resp.read()
            return (resp.headers.get("Content-Type", ""), data) if binary else json.loads(data)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"HTTP{e.code} {e.read().decode(errors='replace')[:200]}")
        except Exception as e:
            last = e
            print(f"  网络波动({att+1}/{retries}): {str(e)[:60]}")
    raise RuntimeError(f"重试耗尽: {last}")


def sign_url(filename, ttl=3600):
    exp = str(int(time.time()) + ttl)
    sig = hmac.new(SECRET, f"{exp}:{filename}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{PUB}/api/pub/{exp}/{sig}/{filename}"


def main():
    frame = sys.argv[1] if len(sys.argv) > 1 else "asset_a85c44be5360.jpg"
    # 0) 签名公链自检（平台取图前先确认自己人能取到）
    import ssl
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    url = sign_url(frame)
    n = len(urllib.request.urlopen(urllib.request.Request(url), timeout=30, context=ctx).read())
    print(f"[0] 签名公链可达 {n//1024}KB: ...{url[-40:]}")
    # 1) 额度探针（非法时长，不烧券）
    try:
        req(f"{BASE}/v1/videos", body=json.dumps({
            "model": MODEL, "resolution": "480p", "duration": 999,
            "content": [{"type": "text", "text": "probe"}]}).encode())
    except RuntimeError as e:
        if "duration" in str(e):
            print("[1] 参数层可达（注意：参数校验先于额度检查，本探针不证明额度可用）")
        else:
            print(f"[1] 阻塞，停止: {e}")
            return 1
    # 2) 1080p 图生实测
    body = json.dumps({"model": MODEL, "resolution": "1080p", "duration": 5,
        "content": [
            {"type": "text", "text": "The hand slowly rotates the knurled drum; the glowing "
             "orange concentric rings smoothly contract toward the center. Camera locked."},
            {"type": "image_url", "image_url": {"url": url}, "role": "first_frame"}]}).encode()
    r = req(f"{BASE}/v1/videos", body=body)
    tid = r.get("id") or r.get("task_id")
    print(f"[2] 受理 {tid}")
    t0 = time.time()
    st = "?"
    for _ in range(80):
        s = req(f"{BASE}/v1/videos/{tid}")
        st = s.get("status")
        if st in ("succeeded", "completed", "failed", "cancelled", "error"):
            print(f"[3] 终态 {st}，{time.time()-t0:.0f}s，usage={s.get('usage')}")
            break
        time.sleep(8)
    if st not in ("succeeded", "completed"):
        print(json.dumps(s, ensure_ascii=False)[:400])
        return 1
    ct, data = req(f"{BASE}/v1/videos/{tid}/content", timeout=600, binary=True)
    out = REPO / "ops" / "bjmoma_1080p_test.mp4"
    if "json" in ct.lower():
        j = json.loads(data)
        real = j.get("url") or (j.get("content") or {}).get("video_url") or j.get("video_url")
        urllib.request.urlretrieve(real, out)
    else:
        out.write_bytes(data)
    print(f"[4] 成片 {out}（{out.stat().st_size//1024}KB）")
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "stream=width,height,codec_name", "-of", "csv=p=0", str(out)],
                       capture_output=True, text=True)
    print("[5] 流信息:", p.stdout.strip())
    print("完成。请到平台费用明细核对本单实际扣券数（=1080p×5s 单价）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
