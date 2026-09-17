"""模型调用层：统一 BaseProvider 接口，屏蔽本地/云端/Mock 差异。

正式版相对 demo 增强：
- 所有网络调用支持超时（timeout）。
- 支持“取消标志”（cancel: threading.Event），用于用户中止。
- 错误按类型分类抛出 ProviderError，便于界面友好提示与回退决策。
"""
from __future__ import annotations

import json
import threading
from abc import ABC, abstractmethod
from typing import Any
from urllib.error import HTTPError

import requests


class ProviderError(Exception):
    """模型调用失败。kind: network/auth/model/timeout/other。"""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


class BaseProvider(ABC):
    """所有模型提供端的抽象基类。"""

    def __init__(self, timeout: int = 120) -> None:
        self.timeout = timeout

    def _check_cancel(self, cancel: threading.Event | None) -> None:
        if cancel is not None and cancel.is_set():
            raise ProviderError("cancel", "任务已被用户中止")

    @abstractmethod
    def generate(self, prompt: str, cancel: threading.Event | None = None) -> str:
        """给定 prompt，返回模型文本输出；cancel 置位时尽早抛出中止。"""

    def chat_json(self, prompt: str, cancel: threading.Event | None = None) -> dict[str, Any]:
        """让模型输出 JSON 并解析（路由拆解用）。"""
        raw = self.generate(prompt, cancel)
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end != -1:
            raw = raw[start:end + 1]
        return json.loads(raw)


def _request_based_generate(method, url, *, params=None, json_body=None, headers=None,
                            timeout: int, cancel: threading.Event | None) -> Any:
    """统一封装带取消与超时的 HTTP 请求（供 Ollama/OpenAI 复用）。"""
    try:
        resp = method(url, params=params, json=json_body, headers=headers, timeout=timeout)
    except requests.exceptions.Timeout as e:
        raise ProviderError("timeout", f"请求超时：{e}") from e
    except requests.exceptions.ConnectionError as e:
        raise ProviderError("network", f"网络连接失败：{e}") from e
    except requests.exceptions.RequestException as e:
        raise ProviderError("other", f"请求失败：{e}") from e
    if resp.status_code in (401, 403):
        raise ProviderError("auth", f"认证失败（HTTP {resp.status_code}）：请检查 API Key")
    if resp.status_code == 404:
        raise ProviderError("model", f"模型不存在（HTTP 404）：请检查模型名")
    if resp.status_code >= 400:
        raise ProviderError("other", f"HTTP {resp.status_code}: {resp.text[:200]}")
    return resp


class MockProvider(BaseProvider):
    """离线演示用模型：不联网，按预设规则返回内容。"""

    def __init__(self, name: str, timeout: int = 120) -> None:
        super().__init__(timeout)
        self.name = name

    def generate(self, prompt: str, cancel: threading.Event | None = None) -> str:
        self._check_cancel(cancel)
        if "拆分" in prompt or "subtasks" in prompt.lower():
            return (
                '{"subtasks":['
                '{"index":1,"title":"撰写报告大纲","skill":"规划"},'
                '{"index":2,"title":"编写正文内容","skill":"写作"},'
                '{"index":3,"title":"校对与润色","skill":"校对"}]}'
            )
        return (
            f"[{self.name} · Mock 执行] 已处理子任务："
            f"{prompt[max(0, len(prompt) - 60):]}。"
            "（如需真实回答，请启动 Ollama 或配置云端 API）"
        )


class OllamaProvider(BaseProvider):
    """调用本机 Ollama 服务。"""

    def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434",
                 timeout: int = 120) -> None:
        super().__init__(timeout)
        self.model = model
        self.base_url = base_url

    def generate(self, prompt: str, cancel: threading.Event | None = None) -> str:
        self._check_cancel(cancel)
        resp = _request_based_generate(
            requests.post, f"{self.base_url}/api/generate",
            json_body={"model": self.model, "prompt": prompt,
                       "stream": False, "options": {"temperature": 0.2}},
            timeout=self.timeout, cancel=cancel)
        return resp.json().get("response", "")


class OpenAIProvider(BaseProvider):
    """调用 OpenAI 兼容 API（官方或通义等）。

    api_key 通过 credentials 传入，避免明文写死在代码里。
    """

    def __init__(self, model: str, api_key: str,
                 base_url: str = "https://api.openai.com/v1",
                 timeout: int = 120) -> None:
        super().__init__(timeout)
        self.model = model
        self.api_key = api_key
        self.base_url = base_url

    def generate(self, prompt: str, cancel: threading.Event | None = None) -> str:
        self._check_cancel(cancel)
        resp = _request_based_generate(
            requests.post, f"{self.base_url}/chat/completions",
            json_body={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
            },
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout, cancel=cancel)
        return resp.json()["choices"][0]["message"]["content"]


def is_ollama_online(base_url: str = "http://127.0.0.1:11434") -> bool:
    """探测本机 Ollama 服务是否在线。"""
    try:
        return requests.get(f"{base_url}/api/tags", timeout=3).status_code == 200
    except requests.RequestException:
        return False