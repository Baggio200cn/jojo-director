"""管理员供应商配置、密钥掩码与路由热更新测试。"""
import copy
import io
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("ARK_API_KEY", "test-dummy-key")
os.environ.setdefault("BJMOMA_API_KEY", "test-dummy-key")
os.environ.pop("ADMIN_USER", None)
os.environ.pop("ADMIN_PASS", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from fastapi.testclient import TestClient
from app import config, provider_settings
from app.main import app

PASS, FAIL = [], []


def check(name, ok):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}")


def main():
    old_env = {key: os.environ.get(key) for key in provider_settings.ENV_KEYS}
    old_routes = copy.deepcopy(config.ROUTES)
    with tempfile.TemporaryDirectory() as tmp:
        provider_settings.SETTINGS_PATH = Path(tmp) / "settings.json"
        client = TestClient(app)
        response = client.put("/api/admin/provider-settings", json={
            "env": {"ARK_API_KEY": "new-secret-key", "BJMOMA_BASE": "https://moma.example.com/v1",
                    "PUBLIC_BASE": "https://jojo.example.com"},
            "routes": {"image": {"provider": "ark", "model": "doubao-seedream-test"}},
        })
        check("保存供应商配置", response.status_code == 200)
        check("密钥仅返回掩码", response.json()["providers"]["ark"]["apiKeyMask"] == "••••-key"
              and "new-secret-key" not in json.dumps(response.json()))
        check("图片路由热更新为火山", config.ROUTES["image"]["provider"] == "ark"
              and config.ROUTES["image"]["model"] == "doubao-seedream-test")
        saved = json.loads(provider_settings.SETTINGS_PATH.read_text(encoding="utf-8"))
        check("配置在服务端持久化", saved["env"]["ARK_API_KEY"] == "new-secret-key"
              and saved["routes"]["image"]["provider"] == "ark")
        os.environ["ARK_API_KEY"] = "other-value"
        provider_settings.apply_saved()
        check("重启后恢复服务端配置", os.environ["ARK_API_KEY"] == "new-secret-key")
        public = client.get("/api/admin/provider-settings")
        check("读取接口不包含明文密钥", public.status_code == 200
              and "new-secret-key" not in public.text)
        invalid = client.put("/api/admin/provider-settings", json={
            "env": {"PUBLIC_BASE": "http://insecure.example.com"}, "routes": {},
        })
        check("非 HTTPS 地址被拒绝", invalid.status_code == 400)
        invalid_route = client.put("/api/admin/provider-settings", json={
            "env": {}, "routes": {"research": {"provider": "moma", "model": "unsupported"}},
        })
        check("不支持的能力路由被拒绝", invalid_route.status_code == 400)
        client.close()
        import gc
        gc.collect()
    for key, value in old_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    config.ROUTES.clear()
    config.ROUTES.update(old_routes)


if __name__ == "__main__":
    main()
    print(f"\n通过 {len(PASS)}/{len(PASS) + len(FAIL)}")
    sys.exit(1 if FAIL else 0)
