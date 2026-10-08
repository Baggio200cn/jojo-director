"""配置加载：.env（密钥）+ providers.yaml（路由规则）"""
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = BACKEND_DIR / "assets"
DB_PATH = BACKEND_DIR / "jojo.db"

load_dotenv(BACKEND_DIR / ".env")

_cfg_path = BACKEND_DIR / "providers.yaml"
try:
    with open(_cfg_path, encoding="utf-8") as f:
        _cfg = yaml.safe_load(f)
except FileNotFoundError:
    raise RuntimeError(
        f"缺少路由配置文件 {_cfg_path}——从仓库根复制 providers.yaml 到 backend/ 目录") from None
except yaml.YAMLError as e:
    raise RuntimeError(f"{_cfg_path} 不是合法的 YAML：{e}") from None
if not isinstance(_cfg, dict) or "providers" not in _cfg or "routes" not in _cfg:
    raise RuntimeError(f"{_cfg_path} 格式不正确：顶层必须包含 providers 和 routes 两个键")

# Whisper 模型下载走镜像（服务器到 HF 直连不可靠）。显式声明在配置层，
# 环境变量已设置时以环境为准
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

PROVIDERS: dict = _cfg["providers"]
ROUTES: dict = _cfg["routes"]
BUDGET: dict = _cfg.get("budget", {})
QC: dict = _cfg.get("qc", {})
QC_RULES_DIR = BACKEND_DIR / "qc_rules"
AGENT_MODELS: list = _cfg.get("agent_models", [])
PRICING: dict = _cfg.get("pricing", {})


def estimate_cost_cny(model: str, task_type: str,
                      input_tokens: int = 0, output_tokens: int = 0) -> float | None:
    """按单价表估算一次调用的人民币成本；无单价返回 None。
    图像模型按张计费（一次成功任务=一张）；token 模型按百万 token。"""
    p = PRICING.get(model)
    if not p:
        return None
    if "per_image" in p and task_type == "image_generation":
        return float(p["per_image"])
    cost = 0.0
    cost += (input_tokens or 0) / 1e6 * float(p.get("per_million_input_tokens", 0))
    cost += (output_tokens or 0) / 1e6 * float(p.get("per_million_output_tokens", 0))
    return round(cost, 4) if cost > 0 else None


def provider_conf(name: str) -> dict:
    conf = PROVIDERS.get(name)
    if not conf or not conf.get("enabled"):
        raise RuntimeError(f"供应商 {name} 未启用，请检查 providers.yaml")
    base_url = conf.get("base_url") or os.environ.get(conf.get("base_url_env", ""), "")
    api_key = os.environ.get(conf.get("api_key_env", ""), "")
    if not api_key:
        raise RuntimeError(f"供应商 {name} 的 API Key 未配置，请检查 .env")
    return {"base_url": base_url.rstrip("/"), "api_key": api_key}


def route(task_type: str) -> dict:
    r = ROUTES.get(task_type)
    if not r:
        raise RuntimeError(f"routes 中没有 {task_type}，请检查 providers.yaml")
    return r
