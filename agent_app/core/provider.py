"""模型调用层：屏蔽本地/云端/Mock 差异，提供统一的 chat() 接口。

所有 Provider 都实现 generate(prompt) -> str。
- MockProvider   离线演示用，不依赖任何服务。
- OllamaProvider 调用本机 Ollama REST API。
- OpenAIProvider 调用 OpenAI 兼容 API（OpenAI 官方或通义等）。
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

import requests


class BaseProvider(ABC):
    """所有模型提供端的抽象基类。"""

    @abstractmethod
    def generate(self, prompt: str) -> str:
        """给定 prompt，返回模型文本输出。"""

    def chat_json(self, prompt: str) -> dict[str, Any]:
        """默认实现：让模型输出 JSON 并解析。

        子类可按需覆盖（例如专门用于路由的轻量调用）。
        """
        raw = self.generate(prompt)
        # 兼容“```json ... ```”包裹，取第一段 JSON
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1:
            raw = raw[start:end + 1]
        return json.loads(raw)


class MockProvider(BaseProvider):
    """离线演示用模型：不联网，按预设规则返回内容。

    用于在没有 Ollama / 云端 Key 时也能完整体验「拆分 → 分配 → 执行」流程。
    """

    def __init__(self, name: str) -> None:
        self.name = name

    def generate(self, prompt: str) -> str:
        # 命中“拆分任务”请求时，返回一个包含子任务的示例 JSON
        if "拆分" in prompt or "subtasks" in prompt.lower():
            return (
                '{"subtasks":['
                '{"index":1,"title":"撰写报告大纲","skill":"规划"},'
                '{"index":2,"title":"编写第一章内容","skill":"写作"},'
                '{"index":3,"title":"校对与润色","skill":"校对"}]}'
            )
        # 否则按模型名字返回一段“执行结果”占位，便于可视化
        return (
            f"[{self.name} · Mock 执行] 已处理子任务："
            f"{prompt[max(0, len(prompt) - 60):]}。"
            "（如需真实回答，请启动 Ollama 或配置云端 API）"
        )


class OllamaProvider(BaseProvider):
    """调用本机 Ollama 服务（默认 http://127.0.0.1:11434）。"""

    def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434") -> None:
        self.model = model
        self.base_url = base_url

    def generate(self, prompt: str) -> str:
        resp = requests.post(
            f"{self.base_url}/api/generate",
            json={"model": self.model, "prompt": prompt,
                  "stream": False, "options": {"temperature": 0.2}},
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json().get("response", "")


class OpenAIProvider(BaseProvider):
    """调用 OpenAI 兼容 API（官方或通义等）。

    api_key 应通过 credentials 引用，避免明文写死在代码里。
    """

    def __init__(self, model: str, api_key: str, base_url: str = "https://api.openai.com/v1") -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url

    def generate(self, prompt: str) -> str:
        resp = requests.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


def is_ollama_online(base_url: str = "http://127.0.0.1:11434") -> bool:
    """探测本机 Ollama 服务是否在线。"""
    try:
        return requests.get(f"{base_url}/api/tags", timeout=3).status_code == 200
    except requests.RequestException:
        return False