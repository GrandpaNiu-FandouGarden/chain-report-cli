"""Configuration and .env helpers for ChainReport CLI."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

APP_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = APP_DIR / ".env"
DEFAULT_OUTPUT_DIR = APP_DIR / "outputs"

PROVIDER_SPECS = {
    "mock": {
        "display": "Mock 本地占位模型（无需 Key，用于测试流程）",
        "key_env": None,
        "base_url": None,
        "default_model": "mock-report-writer",
        "key_optional": True,
    },
    "openai": {
        "display": "OpenAI",
        "key_env": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4.1",
        "key_optional": False,
    },
    "deepseek": {
        "display": "DeepSeek",
        "key_env": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-chat",
        "key_optional": False,
    },
    "minimax-cn": {
        "display": "MiniMax 中国区",
        "key_env": "MINIMAX_CN_API_KEY",
        "base_url": "https://api.minimaxi.com/v1",
        "default_model": "MiniMax-M1",
        "key_optional": False,
    },
    "minimax": {
        "display": "MiniMax Global",
        "key_env": "MINIMAX_API_KEY",
        "base_url": "https://api.minimax.io/v1",
        "default_model": "MiniMax-M1",
        "key_optional": False,
    },
    "openai-compatible": {
        "display": "OpenAI-Compatible 自定义接口",
        "key_env": "OPENAI_COMPATIBLE_API_KEY",
        "base_url": None,
        "default_model": "custom-model",
        "key_optional": True,
    },
    "ollama": {
        "display": "Ollama 本地/远程模型",
        "key_env": None,
        "base_url": "http://localhost:11434/v1",
        "default_model": "qwen3:latest",
        "key_optional": True,
    },
}


def load_env(path: Path = ENV_PATH) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
        values[key] = value
    return values


def set_env_value(key: str, value: str, path: Path = ENV_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    replaced = False
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    new_lines = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            new_lines.append(f"{key}={value}")
            replaced = True
        else:
            new_lines.append(line)
    if not replaced:
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.append(f"{key}={value}")
    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    os.environ[key] = value
    try:
        path.chmod(0o600)
    except OSError:
        pass


def get_env_value(key: Optional[str]) -> Optional[str]:
    if not key:
        return None
    return os.environ.get(key)
