"""编排核心（Orchestrator）：驱动正式版整条任务链路。

相较 demo 的 Executor 增强：
- 从 config 读取模型、权重、规则、服务地址与超时；云端口经 credentials 取 Key。
- 离线检测：未在线的本地模型会被从“可用白名单”剔除（可回退云端）。
- 支持取消事件（cancel: threading.Event）与进度回调（on_progress）。
- 主模型与子任务分开处理，各带异常保护。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from .. import config as app_config
from .models import ModelInfo, ModelRegistry
from .provider import (
    BaseProvider, MockProvider, OllamaProvider, OpenAIProvider,
    ProviderError, is_ollama_online,
)
from .router import Assignment, Router, SubTask

log = logging.getLogger("core.orchestrator")


class Orchestrator:
    """正式版任务执行编排入口。"""

    def __init__(self, registry: ModelRegistry, cfg: dict) -> None:
        self.registry = registry
        self.cfg = cfg
        self.router = None
        self.providers: dict[str, BaseProvider] = {}
        self.available_keys: list[str] = []
        self.online = False

    # ---- provider 构建（依据配置与在线状态）----
    def setup(self) -> None:
        general = self.cfg.get("general", {})
        cloud = self.cfg.get("cloud", {})
        rules = self.cfg.get("rules", {})
        ollama_url = general.get("ollama_url", "http://127.0.0.1:11434")
        timeout = int(general.get("timeout_seconds", 120))
        offline_use_cloud = bool(rules.get("offline_use_cloud", True))

        self.online = is_ollama_online(ollama_url)
        self.providers.clear()
        available: list[str] = []

        for m in self.registry.all():
            if m.source == "local":
                if self.online:
                    self.providers[m.key] = OllamaProvider(m.endpoint, ollama_url, timeout)
                    available.append(m.key)
                else:
                    # 离线：本地模型不可用，用 Mock 占位仅用于展示（不进可用白名单）
                    self.providers[m.key] = MockProvider(m.name, timeout)
            elif m.source == "cloud":
                key = m.api_key_ref or cloud.get("api_key_ref", "cloud_api_key")
                api_key = app_config.load_credential(key)
                if api_key:
                    self.providers[m.key] = OpenAIProvider(
                        model=m.endpoint or cloud.get("default_model", ""),
                        api_key=api_key,
                        base_url=cloud.get("base_url", "https://api.openai.com/v1"),
                        timeout=timeout)
                    available.append(m.key)
                else:
                    # 未配 Key：Mock 占位（不进可用白名单）
                    self.providers[m.key] = MockProvider(m.name, timeout)
            else:
                self.providers[m.key] = MockProvider(m.name, timeout)
                available.append(m.key)

        # 离线且规则允许时，云端仍可回退（available 已含云端 key）；否则仅本地在线可用
        if not self.online and not offline_use_cloud:
            available = []

        self.available_keys = available
        # 路由模型：主模型在线用真实，否则 Mock
        route_provider = None
        primary = self.registry.primary()
        if self.online and primary is not None:
            route_provider = self.providers.get(primary.key, MockProvider("路由(mock)"))
        elif primary is not None:
            route_provider = MockProvider("路由(mock)")

        self.router = Router(
            self.registry,
            route_provider=route_provider,
            sensitive_keywords=rules.get("sensitive_keywords"),
        )
        log.info("编排就绪：在线=%s 可用模型=%s", self.online, self.available_keys)

    def main_provider(self) -> BaseProvider:
        """返回主模型提供端（不可用时兜底 Mock）。"""
        primary = self.registry.primary()
        if primary is not None:
            return self.providers.get(primary.key, MockProvider(primary.name))
        return MockProvider("主模型(mock)")

    # ---- 任务执行 ----
    def run(self, task_text: str,
            cancel: threading.Event | None = None,
            on_progress: Callable[[str, str], None] | None = None) -> dict[str, Any]:
        """执行一次完整任务。可传入取消事件与进度回调。"""
        if self.router is None:
            self.setup()
        t0 = time.time()
        on_progress = on_progress or (lambda *_: None)
        cancel = cancel or threading.Event()

        on_progress("info", "主模型执行中…")
        overview = self._safe_overview(task_text, cancel)

        on_progress("info", "拆分与分配子任务…")
        subtasks = self.router.split(task_text)
        assignments, results = self._execute_with_fallback(
            subtasks, task_text, cancel, on_progress)

        summary = f"主任务共拆解出 {len(subtasks)} 个子任务，执行完成。"
        return {
            "overview": overview,
            "subtasks": subtasks,
            "assignments": assignments,
            "results": results,
            "summary": summary,
            "status": "canceled" if cancel.is_set() else "done",
            "duration_ms": int((time.time() - t0) * 1000),
        }

    def _safe_overview(self, task_text: str, cancel: threading.Event) -> str:
        try:
            return self.main_provider().generate(
                "你是总执行 Agent。概括你会如何完成以下任务，保持简短。\n任务：" + task_text,
                cancel)
        except ProviderError as e:
            log.warning("主模型概览失败：%s", e.message)
            return f"（主模型暂不可用，已降级：{e.message}）"

    def _execute_with_fallback(self, subtasks: list[SubTask], main_task_text: str,
                               cancel: threading.Event,
                               on_progress: Callable[[str, str], None]):
        """对子任务执行分配与运行；失败的子任务在下一轮剔除其模型后重试，最多 3 轮。

        结果按子任务索引保存（exec_by），避免回退轮次重复/错位。
        """
        exec_by: dict[int, dict] = {}          # task.index -> 该子任务执行结果
        ok_set: set[int] = set()               # 已成功的子任务索引（不再重试）
        failed_map: dict[int, list[str]] = {}  # 子任务 -> 已失败模型 key（用于回退）

        for _round in range(3):
            remains = [t for t in subtasks if t.index not in ok_set]
            if not remains or cancel.is_set():
                break
            assignments = self.router.assign(
                remains, main_task_text,
                exclude=failed_map,
                available_keys=self.available_keys or None)
            for a in assignments:
                if a.task.index in ok_set:
                    continue  # 已成功，跳过
                if a.model_key == "":
                    exec_by[a.task.index] = {
                        "assignment": a, "status": "failed", "error": "无可用模型"}
                    failed_map.setdefault(a.task.index, []).append("__nomodel__")
                    continue
                prov = self.providers.get(a.model_key, MockProvider(a.model_key))
                on_progress("task", f"子任务“{a.task.title}”→ {a.model_key}")
                try:
                    out = prov.generate(f"执行子任务：{a.task.title}", cancel)
                    exec_by[a.task.index] = {
                        "assignment": a, "status": "ok", "output": out}
                    ok_set.add(a.task.index)
                except ProviderError as e:
                    exec_by[a.task.index] = {
                        "assignment": a, "status": "failed", "error": e.message}
                    failed_map.setdefault(a.task.index, []).append(a.model_key)
        order = sorted(exec_by)
        return [exec_by[i]["assignment"] for i in order], [exec_by[i] for i in order]