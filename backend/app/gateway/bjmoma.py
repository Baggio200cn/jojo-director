"""北京移动 Token 平台（BJMoMA）适配器：视频生成（Seedance 2.0 正式版）。

与 ArkAdapter 同签名（video_create / video_get / download），executors 按
providers.yaml 的 provider 字段分发。三个实测坑的处理都在这里：
  1. 状态查询是 POST /v1/videos/{id}（GET 会被平台 WAF 403/掐断）
  2. 图生只收 http/https 图片 URL，不收 base64 data URI——
     调用方须传公网可达 URL（executors 用 sign_pub_url 生成本站签名短链）
  3. 平台网关 SSL EOF 频发——所有请求带重试（LOOP-L3）
计价：通用券，100 券 ≈ ¥1（2026-08 口径，正式接入前以费用明细核实单价）。
"""
import asyncio
import hashlib
import hmac
import json
import os
import time

import httpx

TIMEOUT = httpx.Timeout(300.0, connect=20.0)
RETRIES = 6
RETRY_SLEEP = 6

# 终态映射：平台叫法 → 产线统一叫法（与方舟对齐）
_STATUS_MAP = {"completed": "succeeded", "success": "succeeded", "error": "failed"}


def _base() -> str:
    return os.getenv("BJMOMA_BASE",
                     "https://www.mobileopentokenaccess.com/maas/ai/aiFactoryServer/v1/apis/1").rstrip("/")


def _headers() -> dict:
    key = os.getenv("BJMOMA_API_KEY", "").strip()
    if not key:
        raise RuntimeError("未配置 BJMOMA_API_KEY（backend/.env），无法调用北京移动平台")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def sign_pub_url(asset_filename: str, ttl_s: int = 3600) -> str:
    """给 /assets/ 下的素材生成免登录签名公链（平台图生要求公网可取图）。
    对应 main.py 的 /api/pub/{exp}/{sig}/{filename} 路由。"""
    base = os.getenv("PUBLIC_BASE", "").rstrip("/")
    if not base:
        raise RuntimeError("未配置 PUBLIC_BASE（如 https://<服务器IP>），图生视频需要素材公网可达")
    exp = str(int(time.time()) + ttl_s)
    sig = pub_sig(asset_filename, exp)
    return f"{base}/api/pub/{exp}/{sig}/{asset_filename}"


def pub_sig(filename: str, exp: str) -> str:
    secret = (os.getenv("PUB_SIGN_KEY") or os.getenv("ADMIN_PASS") or "jojo-dev").encode()
    return hmac.new(secret, f"{exp}:{filename}".encode(), hashlib.sha256).hexdigest()[:32]


async def _request(method: str, url: str, body: dict | None = None,
                   stream_to: str | None = None):
    """带重试的请求。平台网关 SSL EOF 频发，网络类异常一律重试。"""
    last: Exception | None = None
    for att in range(RETRIES):
        if att:
            await asyncio.sleep(RETRY_SLEEP)
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, verify=True) as client:
                if stream_to:
                    async with client.stream(method, url, headers=_headers(),
                                             json=body) as r:
                        r.raise_for_status()
                        ct = r.headers.get("content-type", "")
                        if "json" in ct.lower():
                            data = json.loads(await r.aread())
                            return data
                        with open(stream_to, "wb") as f:
                            async for chunk in r.aiter_bytes():
                                f.write(chunk)
                        return None
                r = await client.request(method, url, headers=_headers(), json=body)
                if r.status_code >= 400:
                    # 4xx 业务错误直接抛（额度不足/参数错，重试无意义）
                    try:
                        msg = r.json().get("message", r.text[:150])
                    except Exception:
                        msg = r.text[:150]
                    raise RuntimeError(f"BJMoMA 接口错误 HTTP{r.status_code}: {msg}")
                return r.json()
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout,
                httpx.RemoteProtocolError, httpx.WriteError) as e:
            last = e
            continue
    raise RuntimeError(f"BJMoMA 网络重试耗尽({RETRIES}次): {type(last).__name__}")


class BjmomaAdapter:
    async def video_create(self, model: str, prompt: str,
                           first_frame_url: str | None = None,
                           last_frame_url: str | None = None,
                           resolution: str = "480p", duration: int = 5,
                           ratio: str = "16:9") -> str:
        """创建视频任务，返回平台任务 id（task_...）。
        帧 URL 必须是 http/https 公链（data URI 会被平台拒）。"""
        for u in (first_frame_url, last_frame_url):
            if u and u.startswith("data:"):
                raise ValueError("BJMoMA 图生只收 http/https 图片链接——"
                                 "请用 sign_pub_url 生成素材签名公链")
        content: list[dict] = [{"type": "text", "text": prompt}]
        if first_frame_url:
            content.append({"type": "image_url", "image_url": {"url": first_frame_url},
                            "role": "first_frame"})
        if last_frame_url:
            content.append({"type": "image_url", "image_url": {"url": last_frame_url},
                            "role": "last_frame"})
        # 平台时长范围 4-15 秒；产线常用 3 秒补拍夹到下限
        dur = max(4, min(15, int(duration)))
        r = await _request("POST", f"{_base()}/v1/videos", {
            "model": model, "resolution": resolution,
            "duration": dur, "content": content})
        return r.get("id") or r.get("task_id")

    async def video_get(self, provider_task_id: str) -> dict:
        """查询任务（注意：平台此接口是 POST）。返回对齐方舟的结构：
        status / content.video_url / usage。video_url 用 bjmoma:// 记号，
        由本适配器的 download 解析成 content 接口拉流。"""
        r = await _request("POST", f"{_base()}/v1/videos/{provider_task_id}", {})
        status = _STATUS_MAP.get(str(r.get("status", "")).lower(), r.get("status"))
        out = dict(r)
        out["status"] = status
        if status == "succeeded":
            out.setdefault("content", {})
            out["content"].setdefault("video_url", f"bjmoma://{provider_task_id}")
        return out

    async def download(self, url: str, dest_path: str) -> None:
        """下载成片。bjmoma:// 记号走平台 content 接口；普通 URL 直接拉流。"""
        if url.startswith("bjmoma://"):
            tid = url.split("://", 1)[1]
            r = await _request("POST", f"{_base()}/v1/videos/{tid}/content",
                               {}, stream_to=dest_path)
            if isinstance(r, dict):
                # content 接口返回的是 JSON（内含真实 URL）而非流
                real = (r.get("url") or (r.get("content") or {}).get("video_url")
                        or r.get("video_url"))
                if not real:
                    raise RuntimeError(f"BJMoMA content 响应无视频地址: {str(r)[:150]}")
                url = real
            else:
                return
        # 普通 URL：裸客户端拉流（不带平台密钥头，防泄漏）
        for att in range(RETRIES):
            if att:
                await asyncio.sleep(RETRY_SLEEP)
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                    async with client.stream("GET", url) as r:
                        r.raise_for_status()
                        with open(dest_path, "wb") as f:
                            async for chunk in r.aiter_bytes():
                                f.write(chunk)
                return
            except Exception as e:
                last = e
        raise RuntimeError(f"BJMoMA 成片下载失败: {type(last).__name__}")
