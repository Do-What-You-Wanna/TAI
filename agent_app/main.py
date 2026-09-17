"""Agent 自动化智能体 · 正式版启动入口。

用法：
    python -m agent_app.main
"""
from __future__ import annotations

import logging
import sys

from . import config as app_config
from .core.models import ModelRegistry, build_default_registry
from .core.orchestrator import Orchestrator
from .core.task_history import TaskHistory
from .gui.app import AppContext, MainWindow
from .logging_setup import setup_logging

log = logging.getLogger("main")


def _install_exception_guard() -> None:
    """全局异常兜底：未捕获异常写日志，避免静默崩溃（设计文档 §7）。"""

    def _hook(etype, evalue, tb) -> None:
        log.error("未捕获异常", exc_info=(etype, evalue, tb))

    def _qt(msg_type, context, message) -> None:
        log.error("Qt消息(%s): %s", msg_type, message)

    sys.excepthook = _hook


def build_context() -> AppContext:
    """从配置构建应用共享上下文。"""
    cfg = app_config.load_config()
    reg = ModelRegistry()
    reg.from_list_dicts(cfg.get("models", []))
    # 配置为空时兜底默认模型表
    if not reg.all():
        reg = build_default_registry(cfg.get("weights"))
        cfg["models"] = reg.to_list_dicts()
        app_config.save_config(cfg)
    ctx = AppContext(cfg=cfg, registry=reg, history=TaskHistory())
    ctx.orchestrator = Orchestrator(reg, cfg)
    return ctx


def run_app() -> int:
    from PySide6.QtWidgets import QApplication

    setup_logging()
    _install_exception_guard()
    ctx = build_context()
    app = QApplication(sys.argv)
    win = MainWindow(ctx)
    win.show()
    return app.exec()


def main() -> int:
    return run_app()


if __name__ == "__main__":
    sys.exit(main())