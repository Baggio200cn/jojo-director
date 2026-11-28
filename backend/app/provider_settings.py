"""管理员供应商配置：密钥服务端保存、模型路由热更新与目录预检。"""
import json
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from . import config, executors

SETTINGS_PATH = config.BACKEND_DIR / "runtime_provider_settings.json"
SECRET_KEYS = {"ARK_API_KEY", "BJMOMA_API_KEY", "PUB_SIGN_KEY", "TTS_TOKEN"}
ENV_KEYS = SECRET_KEYS | {"BJMOMA_BASE", "PUBLIC_BASE", "TTS_APPID", "TTS_VOICE", "WHISPER_MODEL_DIR"}
ROUTE_KEYS = {"script", "storyboard", "image", "image_edit", "video", "video_hd", "qc_vision", "ref_video", "research", "embedding"}
ROUTE_LABELS = {
    "script": "口播脚本", "storyboard": "分镜规划", "image": "图片生成", "image_edit": "图片编辑",
    "video": "视频生成", "video_hd": "高清视频", "qc_vision": "视觉质检", "ref_video": "参考视频理解",
    "research": "联网调研", "embedding": "向量检索",
}


def _url(value: str, name: str) -> str:
    value = value.strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError(f"{name} 必须是不含账号信息的 HTTPS 地址")
    return value


def _mask(value: str) -> str:
    return "••••" + value[-4:] if value else ""


def apply_saved() -> None:
    if not SETTINGS_PATH.exists():
        return
    try:
        saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    for key in ENV_KEYS:
        value = saved.get("env", {}).get(key)
        if value is not None:
            os.environ[key] = str(value)
    for key, override in saved.get("routes", {}).items():
        if key in ROUTE_KEYS and key in config.ROUTES:
            config.ROUTES[key].update({k: v for k, v in override.items() if k in ("provider", "model")})


def refresh_adapters() -> None:
    executors.ark = executors.ArkAdapter()


def settings_view() -> dict:
    apply_saved()
    return {
        "providers": {
            "moma": {"label": "MoMA", "baseUrl": os.getenv("BJMOMA_BASE", ""),
                     "apiKeyConfigured": bool(os.getenv("BJMOMA_API_KEY", "").strip()),
                     "apiKeyMask": _mask(os.getenv("BJMOMA_API_KEY", "").strip())},
            "ark": {"label": "火山引擎", "baseUrl": config.PROVIDERS["ark"]["base_url"],
                    "apiKeyConfigured": bool(os.getenv("ARK_API_KEY", "").strip()),
                    "apiKeyMask": _mask(os.getenv("ARK_API_KEY", "").strip())},
        },
        "runtime": {"publicBase": os.getenv("PUBLIC_BASE", ""),
                    "pubSignConfigured": bool(os.getenv("PUB_SIGN_KEY", "").strip()),
                    "ttsAppId": os.getenv("TTS_APPID", ""), "ttsVoice": os.getenv("TTS_VOICE", ""),
                    "ttsTokenConfigured": bool(os.getenv("TTS_TOKEN", "").strip()),
                    "whisperModelDir": os.getenv("WHISPER_MODEL_DIR", "")},
        "routes": [{"key": key, "label": ROUTE_LABELS[key], "provider": config.ROUTES[key]["provider"],
                    "model": config.ROUTES[key]["model"]}
                   for key in ROUTE_LABELS if key in config.ROUTES],
        "secretStorage": "server_only_masked",
    }


def save_settings(payload: dict) -> dict:
    env_updates = payload.get("env") or {}
    clean_env = {}
    for key in ENV_KEYS:
        value = env_updates.get(key)
        if value is None or value == "":
            continue
        value = str(value).strip()
        if key in ("BJMOMA_BASE", "PUBLIC_BASE"):
            value = _url(value, key)
        clean_env[key] = value
    if not os.getenv("PUB_SIGN_KEY") and "PUB_SIGN_KEY" not in clean_env:
        clean_env["PUB_SIGN_KEY"] = secrets.token_hex(24)
    route_updates = payload.get("routes") or {}
    clean_routes = {}
    for key, update in route_updates.items():
        if key not in ROUTE_KEYS or key not in config.ROUTES:
            raise ValueError("不支持的模型路由")
        provider = str(update.get("provider") or config.ROUTES[key]["provider"])
        model = str(update.get("model") or "").strip()
        if provider not in ("moma", "ark") or not model or len(model) > 120:
            raise ValueError("供应商或模型标识无效")
        if key in ("research", "embedding") and provider != "ark":
            raise ValueError(f"{ROUTE_LABELS[key]}当前必须使用火山引擎")
        clean_routes[key] = {"provider": provider, "model": model}
    saved = {}
    if SETTINGS_PATH.exists():
        try:
            saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            saved = {}
    saved_env = saved.get("env", {})
    saved_env.update(clean_env)
    saved_routes = saved.get("routes", {})
    saved_routes.update(clean_routes)
    saved = {"env": saved_env, "routes": saved_routes}
    temp = SETTINGS_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(SETTINGS_PATH)
    os.chmod(SETTINGS_PATH, 0o600)
    apply_saved()
    refresh_adapters()
    return settings_view()


async def catalog(provider: str) -> dict:
    if provider == "moma":
        base = os.getenv("BJMOMA_BASE", "").rstrip("/")
        key = os.getenv("BJMOMA_API_KEY", "").strip()
        url = f"{base}/v1/models"
    elif provider == "ark":
        base = config.PROVIDERS["ark"]["base_url"].rstrip("/")
        key = os.getenv("ARK_API_KEY", "").strip()
        url = f"{base}/models"
    else:
        raise ValueError("不支持的供应商")
    if not base or not key:
        raise ValueError("请先保存供应商地址和 API Key")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as exc:
        raise ValueError("无法连接供应商，请检查网络与地址") from exc
    if response.status_code >= 400:
        try:
            message = response.json().get("message") or response.json().get("error", {}).get("message")
        except Exception:
            message = ""
        raise ValueError(message or f"供应商返回 HTTP {response.status_code}")
    data = response.json().get("data", [])
    models = sorted(str(item.get("id")) for item in data if item.get("id"))
    return {"ok": True, "provider": provider, "count": len(models), "models": models,
            "message": "模型目录连通成功；图片/视频生成仍需最小生成预检"}
