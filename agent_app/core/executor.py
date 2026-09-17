"""主执行器（Executor）：驱动整条任务链路。

流程：
1. 选择主模型（默认本地 Ollama；在线不可用时回退）。
2. 调用主模型得到主任务回复。
3. 用 Router 拆分并分配子任务给模型。
4. 逐个子任务执行，失败可回退到次优模型。
5. 汇总所有结果。
"""
from __future__ import annotations

from typing import Any

from .models import ModelRegistry
from .provider import BaseProvider, MockProvider, OllamaProvider, is_ollama_online
from .router import Assignment, Router, SubTask


# 默认主模型名（本机 Ollama 需先启动）
MAIN_MODEL = "qwen2.5:14b"
OLLAMA_URL = "http://127.0.0.1:11434"


class Executor:
    """任务的编排执行入口。"""

    def __init__(self, registry: ModelRegistry) -> None:
        self.registry = registry
        self.router = None
        self.providers: dict[str, BaseProvider] = {}

    def setup(self, cloud_config: dict[str, Any] | None = None) -> None:
        """按当前服务可用性组装 provider 与路由。

        cloud_config 示例：{"model": "...", "api_key": "...", "base_url": "..."}
        离线且无云端 Key 时，全部模型降级为 Mock，保证 demo 不依赖外部服务也能跑通。
        """
        self.providers.clear()
        online = is_ollama_online(OLLAMA_URL)
        for m in self.registry.all():
            if m.source == "local":
                # 本地模型：仅在线时才真正连 Ollama，否则用 Mock 占位
                self.providers[m.key] = (
                    OllamaProvider(m.endpoint or MAIN_MODEL, OLLAMA_URL)
                    if online else MockProvider(m.name))
            elif m.source == "cloud" and cloud_config:
                # 云端：配置了 Key 才连真实 API，否则用 Mock 占位
                self.providers[m.key] = (
                    MockProvider(m.name)
                    if not cloud_config.get("api_key")
                    else _new_openai(cloud_config))
            else:
                self.providers[m.key] = MockProvider(m.name)
        # 主模型：本地在线用它；否则降级 Mock（保证演示不中断）
        main_provider = self.providers.get("main", MockProvider("主模型(mock)"))
        if online:
            main_provider = OllamaProvider(MAIN_MODEL, OLLAMA_URL)
        else:
            main_provider = MockProvider("主模型(mock)")
        self.main_provider = main_provider
        # 路由模型：本地在线时复用主模型，否则用 Mock
        self.router = Router(
            self.registry,
            route_provider=main_provider if online else MockProvider("路由(mock)"),
        )

    def run(self, task_text: str) -> dict[str, Any]:
        """执行一次完整任务，返回结构化结果（供界面展示）。"""
        if self.router is None:
            self.setup()
        # 1) 主模型总览回复
        overview = self.main_provider.generate(
            "你是总执行 Agent。概括一下你会如何完成以下任务，保持简短。\n任务：" + task_text)
        # 2) 拆分
        subtasks = self.router.split(task_text)
        # 3) 分配（含回退）
        assignments: list[Assignment] = []
        results: list[dict[str, Any]] = []
        failed_map: dict[int, list[str]] = {}
        for _ in range(3):  # 最多三次完整重分配用于回退
            assignments = self.router.assign(subtasks, task_text, exclude=failed_map)
            # 4) 逐个执行
            ok_all = True
            for a in assignments:
                prov = self.providers.get(a.model_key, MockProvider(a.model_key))
                try:
                    out = prov.generate(f"执行子任务：{a.task.title}")
                    results.append({"assignment": a, "status": "ok", "output": out})
                except Exception as e:
                    ok_all = False
                    failed_map.setdefault(a.task.index, []).append(a.model_key)
                    results.append({"assignment": a, "status": "failed", "error": str(e)})
            if ok_all:
                break
        # 5) 汇总
        summary = f"主任务共拆解出 {len(subtasks)} 个子任务，执行完成，结果已汇总（见结果面板）。"
        return {
            "overview": overview,
            "subtasks": subtasks,
            "assignments": assignments,
            "results": results,
            "summary": summary,
        }


def _new_openai(cloud_config: dict[str, Any]):
    """根据云端配置创建 OpenAI 兼容 provider（延迟 import，避免缺失 openai 包时报错）。"""
    from .provider import OpenAIProvider
    return OpenAIProvider(
        model=cloud_config.get("model", "gpt-4o-mini"),
        api_key=cloud_config["api_key"],
        base_url=cloud_config.get("base_url", "https://api.openai.com/v1"),
    )