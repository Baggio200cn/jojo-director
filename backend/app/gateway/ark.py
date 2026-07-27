"""火山方舟适配器：文本（DeepSeek）、图像（Seedream）、视频（Seedance）、联网调研（Responses+web_search）。

三类调用统一走同一个 base_url + Bearer 鉴权：
  - 文本：POST /chat/completions（OpenAI 兼容，同步）
  - 图像：POST /images/generations（同步，返回临时 URL，需立即落盘）
  - 视频：POST /contents/generations/tasks（异步任务，轮询后落盘）
"""
import asyncio
import json

import httpx

from ..config import provider_conf

TIMEOUT = httpx.Timeout(300.0, connect=15.0)
RETRY_DELAYS = [10, 20, 40]     # 429 限流退避（秒）
INFRA_DELAYS = [5, 15, 45]      # 5xx/网络抖动退避（秒）
_NET_ERRORS = (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError,
               httpx.RemoteProtocolError, httpx.WriteError)

# Seedream RPM 低：图像调用进程内串行，避免自造 429
_image_sem = asyncio.Semaphore(1)


async def _post_retry(client: httpx.AsyncClient, url: str, **kw) -> httpx.Response:
    """统一韧性 POST：429 按限流序列退避；5xx/网络异常按基础设施序列退避；
    4xx 内容类错误带方舟原因立即抛出。"""
    attempt_429 = 0
    attempt_infra = 0
    while True:
        try:
            r = await client.post(url, **kw)
        except _NET_ERRORS as e:
            if attempt_infra < len(INFRA_DELAYS):
                await asyncio.sleep(INFRA_DELAYS[attempt_infra])
                attempt_infra += 1
                continue
            raise RuntimeError(f"网络异常（已重试{len(INFRA_DELAYS)}次）: {type(e).__name__}") from e
        if r.status_code == 429 and attempt_429 < len(RETRY_DELAYS):
            await asyncio.sleep(RETRY_DELAYS[attempt_429])
            attempt_429 += 1
            continue
        if r.status_code >= 500 and attempt_infra < len(INFRA_DELAYS):
            await asyncio.sleep(INFRA_DELAYS[attempt_infra])
            attempt_infra += 1
            continue
        if r.status_code >= 400:
            try:
                detail = r.json().get("error", {})
                msg = f"{detail.get('code', r.status_code)}: {detail.get('message', '')[:200]}"
            except Exception:
                msg = f"HTTP {r.status_code}: {r.text[:200]}"
            raise RuntimeError(f"方舟接口错误 {msg}")
        return r


async def _get_retry(client: httpx.AsyncClient, url: str, **kw) -> httpx.Response:
    """GET 的同款韧性包装（任务轮询/素材下载用）。"""
    attempt = 0
    while True:
        try:
            r = await client.get(url, **kw)
            if r.status_code >= 500 and attempt < len(INFRA_DELAYS):
                await asyncio.sleep(INFRA_DELAYS[attempt])
                attempt += 1
                continue
            r.raise_for_status()
            return r
        except _NET_ERRORS as e:
            if attempt < len(INFRA_DELAYS):
                await asyncio.sleep(INFRA_DELAYS[attempt])
                attempt += 1
                continue
            raise RuntimeError(f"网络异常（已重试{len(INFRA_DELAYS)}次）: {type(e).__name__}") from e


class ArkAdapter:
    def __init__(self) -> None:
        conf = provider_conf("ark")
        self.base = conf["base_url"]
        self.headers = {
            "Authorization": f"Bearer {conf['api_key']}",
            "Content-Type": "application/json",
        }

    async def chat(self, model: str, messages: list[dict],
                   json_mode: bool = False, max_tokens: int = 8192) -> dict:
        """文本生成。返回 {text, reasoning, input_tokens, output_tokens}"""
        payload: dict = {"model": model, "messages": messages, "max_tokens": max_tokens}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
            # 结构化输出不需要长推理，压缩思考长度省钱
            payload["thinking"] = {"type": "disabled"}
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await _post_retry(client, f"{self.base}/chat/completions",
                                  headers=self.headers, json=payload)
            data = r.json()
        msg = data["choices"][0]["message"]
        usage = data.get("usage", {})
        return {
            "text": msg.get("content", ""),
            "reasoning": msg.get("reasoning_content", ""),
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        }

    async def image(self, model: str, prompt: str, size: str = "2560x1440",
                    ref_images: list[str] | None = None) -> dict:
        """图像生成（Seedream 4.5 最小 2560x1440）。返回 {url, output_tokens}

        ref_images: 参考图列表（URL 或 base64 data URI）。Seedream 4.5 支持
        以图编辑/多图融合——传入参考图可在保持原图不变的基础上做增改，
        适合“同一镜头剖面加光路”这类首尾帧一致性需求。
        """
        payload: dict = {"model": model, "prompt": prompt, "size": size,
                         "response_format": "url", "watermark": False}
        if ref_images:
            payload["image"] = ref_images if len(ref_images) > 1 else ref_images[0]
        async with _image_sem:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await _post_retry(client, f"{self.base}/images/generations",
                                      headers=self.headers, json=payload)
                data = r.json()
        return {
            "url": data["data"][0]["url"],
            "output_tokens": data.get("usage", {}).get("output_tokens", 0),
        }

    async def video_create(self, model: str, prompt: str,
                           first_frame_url: str | None = None,
                           last_frame_url: str | None = None,
                           resolution: str = "480p", duration: int = 5,
                           ratio: str = "16:9") -> str:
        """创建视频生成任务，返回方舟任务 id（cgt-...）。"""
        text = f"{prompt} --ratio {ratio} --resolution {resolution} --duration {duration}"
        content: list[dict] = [{"type": "text", "text": text}]
        if first_frame_url:
            content.append({"type": "image_url",
                            "image_url": {"url": first_frame_url},
                            "role": "first_frame"})
        if last_frame_url:
            content.append({"type": "image_url",
                            "image_url": {"url": last_frame_url},
                            "role": "last_frame"})
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await _post_retry(client, f"{self.base}/contents/generations/tasks",
                                  headers=self.headers,
                                  json={"model": model, "content": content})
            return r.json()["id"]

    async def research(self, model: str, query: str, max_keyword: int = 3) -> dict:
        """联网调研：Responses API + web_search 插件。返回 {text, searches}。
        账号需开通联网内容插件，否则抛 ToolNotOpen。"""
        payload = {
            "model": model, "stream": False,
            "tools": [{"type": "web_search", "max_keyword": max_keyword}],
            "input": [{"role": "user", "content":
                       [{"type": "input_text", "text": query}]}],
        }
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await _post_retry(client, f"{self.base}/responses",
                                  headers=self.headers, json=payload)
            data = r.json()
        texts, searches = [], []
        for item in data.get("output", []):
            if item.get("type") == "web_search_call":
                searches.append(json.dumps(item.get("action", {}), ensure_ascii=False)[:120])
            if item.get("type") == "message":
                for c in item.get("content", []):
                    if isinstance(c, dict) and c.get("text"):
                        texts.append(c["text"])
        return {"text": "\n".join(texts), "searches": searches}

    async def video_get(self, provider_task_id: str) -> dict:
        """查询视频任务。返回方舟原始响应（status/content.video_url/usage）。"""
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await _get_retry(
                client, f"{self.base}/contents/generations/tasks/{provider_task_id}",
                headers=self.headers)
            return r.json()

    async def download(self, url: str, dest_path: str) -> None:
        """方舟素材 URL 24 小时过期，生成后立即落盘；网络抖动自动重试。"""
        last: Exception | None = None
        for delay in [0, *INFRA_DELAYS]:
            if delay:
                await asyncio.sleep(delay)
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                    async with client.stream("GET", url) as r:
                        r.raise_for_status()
                        with open(dest_path, "wb") as f:
                            async for chunk in r.aiter_bytes():
                                f.write(chunk)
                return
            except (*_NET_ERRORS, httpx.HTTPStatusError) as e:
                last = e
        raise RuntimeError(f"素材下载失败（已重试{len(INFRA_DELAYS)}次）: {type(last).__name__}")
