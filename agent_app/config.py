"""配置管理：加载 / 保存正式版应用配置（JSON 格式）。

设计要点：
- 配置存于 <应用目录>/config/config.json，基于本模块路径定位，避免受运行 cwd 影响。
- 敏感项（云端 API Key 等）不进入本文件，单独存于 credentials.json（已被 gitignore）。
- 首次运行若配置不存在，自动生成默认配置。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 定位可写基目录：打包后为 exe 同目录，开发时为项目目录
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

# 应用配置目录（打包后可写持久化）
CONFIG_DIR = BASE_DIR / "config"
CONFIG_FILE = CONFIG_DIR / "config.json"
CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"

# 默认模型表（与 demo 保持一致，首次初始化时写入）
_DEFAULT_MODELS = [
    {
        "key": "main", "name": "qwen2.5:14b(主)", "source": "local",
        "endpoint": "qwen2.5:14b", "skills": ["规划", "写作", "通用"],
        "cost": 1.0, "capability": 70, "is_primary": True, "api_key_ref": "",
    },
    {
        "key": "code", "name": "code-helper", "source": "cloud",
        "endpoint": "code-helper", "skills": ["代码"],
        "cost": 2.0, "capability": 85, "is_primary": False, "api_key_ref": "cloud_api_key",
    },
    {
        "key": "doc", "name": "doc-writer", "source": "cloud",
        "endpoint": "doc-writer", "skills": ["写作", "校对"],
        "cost": 1.5, "capability": 80, "is_primary": False, "api_key_ref": "cloud_api_key",
    },
]

_DEFAULT_CONFIG = {
    # 常规设置
    "general": {
        "main_model_key": "main",       # 主模型
        "ollama_url": "http://127.0.0.1:11434",
        "timeout_seconds": 120,          # 单次模型调用超时
    },
    # 性价比权重
    "weights": {"capability": 0.7, "cost": 0.3},
    # 模型列表（用户可增删改）
    "models": _DEFAULT_MODELS,
    # 分配规则
    "rules": {
        "sensitive_keywords": ["密码", "密钥", "token", "隐私", "身份证",
                               "银行卡", "机密", "secret", "password"],
        "offline_use_cloud": True,       # 本地离线时是否回退云端
    },
}


def load_config() -> dict:
    """读取配置；不存在则生成默认配置并保存。"""
    if not CONFIG_FILE.exists():
        save_config(_DEFAULT_CONFIG)
    with CONFIG_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg: dict) -> None:
    """保存配置到磁盘（自动建目录）。"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with CONFIG_FILE.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ---- 敏感项（credentials）----
def load_credential(key: str, default: str = "") -> str:
    """读取单项敏感凭证（不存在返回默认）。"""
    if not CREDENTIALS_FILE.exists():
        return default
    with CREDENTIALS_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get(key, default)


def save_credential(key: str, value: str) -> None:
    """保存单项敏感凭证（写入独立文件，仅本机，已被 gitignore）。"""
    CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if CREDENTIALS_FILE.exists():
        with CREDENTIALS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
    data[key] = value
    with CREDENTIALS_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)