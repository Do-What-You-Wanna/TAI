"""Agent demo 主界面（PySide6 桌面应用）。

界面分四个区域：
1. 顶部：主任务输入框 + 「运行」按钮。
2. 左侧：模型列表（当前注册的模型与来源标记）。
3. 中部：分配结果表格（子任务 → 模型 → 理由/评分）。
4. 底部：主模型概览与任务总结。
"""
from __future__ import annotations

import sys
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QSplitter,
    QListWidget, QListWidgetItem,
)

from ..core.executor import Executor
from ..core.models import ModelInfo, ModelRegistry, build_default_registry


class MainWindow(QWidget):
    """应用主窗口。"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Agent 自动化智能体 · Demo")
        self.resize(960, 620)

        self.registry = build_default_registry()
        self.executor = Executor(self.registry)

        self._build_ui()
        self._refresh_model_list()

    # ---- 界面搭建 ----
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # 顶部输入区
        top = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("输入主任务，例如：生成一份项目周报并检查是否有 bug")
        self.run_btn = QPushButton("运行")
        self.run_btn.clicked.connect(self._on_run)
        top.addWidget(QLabel("主任务："))
        top.addWidget(self.input, 3)
        top.addWidget(self.run_btn)
        root.addLayout(top)

        split = QSplitter(Qt.Horizontal)

        # 左：模型列表
        left = QVBoxLayout()
        left.addWidget(QLabel("已注册模型"))
        self.model_list = QListWidget()
        left.addWidget(self.model_list)
        lw = QWidget(); lw.setLayout(left)
        split.addWidget(lw)

        # 中：分配结果表格
        mid = QVBoxLayout()
        mid.addWidget(QLabel("任务分配结果"))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["#", "子任务", "模型", "理由 / 评分"])
        self.table.horizontalHeader().setStretchLastSection(True)
        mid.addWidget(self.table)
        mw = QWidget(); mw.setLayout(mid)
        split.addWidget(mw)

        root.addWidget(split, 3)

        # 底部：概览与总结
        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("主模型概览："))
        self.overview = QTextEdit()
        self.overview.setMaximumHeight(90)
        bottom.addWidget(self.overview, 3)
        bottom.addWidget(QLabel("总结："))
        self.summary = QTextEdit()
        self.summary.setMaximumHeight(90)
        bottom.addWidget(self.summary, 2)
        root.addLayout(bottom)

    # ---- 数据刷新 ----
    def _refresh_model_list(self) -> None:
        self.model_list.clear()
        for m in self.registry.all():
            src = "本地" if m.source == "local" else "云端"
            it = QListWidgetItem(f"[{src}] {m.name}  (能力{m.capability}/成本{m.cost})")
            it.setToolTip(f"擅长：{'、'.join(m.skills)}")
            self.model_list.addItem(it)

    # ---- 运行逻辑 ----
    def _on_run(self) -> None:
        task = self.input.text().strip()
        if not task:
            self.summary.setPlainText("请先输入主任务。")
            return
        self.run_btn.setEnabled(False)
        self.run_btn.setText("执行中…")
        # 这里用同步调用；demo 直接执行，真实产品可用线程池/QThread 避免卡界面
        try:
            result = self.executor.run(task)
            self._render_result(result)
        except Exception as e:  # noqa: BLE001 界面兜底，展示错误
            self.summary.setPlainText(f"执行异常：{e}")
        finally:
            self.run_btn.setEnabled(True)
            self.run_btn.setText("运行")

    def _render_result(self, result: dict[str, Any]) -> None:
        self.overview.setPlainText(result.get("overview", ""))
        self.summary.setPlainText(result.get("summary", ""))

        self.table.setRowCount(0)
        for idx, res in enumerate(result.get("results", [])):
            a = res["assignment"]
            status = res.get("status", "ok")
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(str(a.task.index)))
            self.table.setItem(row, 1, QTableWidgetItem(a.task.title))
            self.table.setItem(row, 2, QTableWidgetItem(self._model_name(a.model_key)))
            if status == "ok":
                reason = f"{a.reason} · 分{a.score}" if a.score >= 0 else a.reason
                self.table.setItem(row, 3, QTableWidgetItem(reason))
            else:
                self.table.setItem(row, 3, QTableWidgetItem(f"失败：{res.get('error')}（已回退）"))

    def _model_name(self, key: str) -> str:
        m = self.registry.get(key)
        return m.name if m else key


def run_app() -> int:
    """应用入口：供 main.py 调用。"""
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()