"""正式版桌面界面：多标签分类式窗口。

页面组成（对应设计文档 §5）：
- 工作台（WorkbenchPage）：任务输入、运行/中止、进度、分配视图、概览。
- 模型管理（ModelManagerPage）：模型的增删改、设为主模型。
- 规则配置（RulesPage）：敏感关键词、离线回退。
- 任务历史（HistoryPage）：查看/清空历史记录。
- 设置（SettingsPage）：服务地址、超时、权重、云端凭据。

所有页面共享一个 AppContext（config/registry/history/orchestrator）。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QTabWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import config as app_config
from ..core.models import ModelInfo, ModelRegistry
from ..core.orchestrator import Orchestrator
from ..core.task_history import TaskHistory, assignment_to_dict


@dataclass
class AppContext:
    """跨页面共享的应用状态。"""
    cfg: dict
    registry: ModelRegistry
    history: TaskHistory
    orchestrator: Orchestrator = field(init=False)
    cancel: threading.Event = field(default_factory=threading.Event)


class WorkThread(QThread):
    """后台执行线程：不阻塞 GUI。增：进度信号（线程安全跨线程触达 UI）。"""

    done = Signal(object)      # 任务完成，携带结果字典
    failed = Signal(str)       # 执行异常
    progress = Signal(str, str)  # (类型, 消息)，类型: info/task

    def __init__(self, orchestrator: Orchestrator, task: str, cancel: threading.Event) -> None:
        super().__init__()
        self.orchestrator = orchestrator
        self.task = task
        self.cancel = cancel

    def run(self) -> None:
        try:
            result = self.orchestrator.run(
                self.task, cancel=self.cancel,
                on_progress=lambda k, m: self.progress.emit(k, m))
            self.done.emit(result)
        except Exception as e:  # noqa: BLE001 线程内兜底，避免崩溃
            self.failed.emit(str(e))


# ============================================================
# 工作台
# ============================================================
class WorkbenchPage(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._thread: WorkThread | None = None
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        top = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("输入主任务，例如：生成一份项目周报并进行代码检查")
        self.run_btn = QPushButton("运行")
        self.stop_btn = QPushButton("中止")
        self.stop_btn.setEnabled(False)
        top.addWidget(self.input, 1)
        top.addWidget(self.run_btn)
        top.addWidget(self.stop_btn)
        root.addLayout(top)

        self.status = QLabel("就绪")
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)          # busy 样式
        self.bar.setVisible(False)
        root.addWidget(self.status)
        root.addWidget(self.bar)

        self.overview = QPlainTextEdit()
        self.overview.setPlaceholderText("主模型总览将显示在这里")
        self.overview.setMaximumHeight(90)
        root.addWidget(QLabel("主模型总览"))
        root.addWidget(self.overview)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["子任务", "分配模型", "理由/评分", "状态", "输出"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        root.addWidget(QLabel("子任务分配结果"))
        root.addWidget(self.table)

        self.run_btn.clicked.connect(self._on_run)
        self.stop_btn.clicked.connect(self._on_stop)

    def _on_run(self) -> None:
        task = self.input.text().strip()
        if not task:
            QMessageBox.information(self, "提示", "请先输入任务。")
            return
        self.ctx.cancel = threading.Event()   # 每次运行新的取消令牌
        self.ctx.orchestrator.setup()          # 依据最新配置重建
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.bar.setVisible(True)
        self.status.setText("执行中…")
        self.overview.clear()
        self.table.setRowCount(0)

        self._thread = WorkThread(self.ctx.orchestrator, task, self.ctx.cancel)
        self._thread.done.connect(self._on_done)
        self._thread.failed.connect(self._on_failed)
        self._thread.progress.connect(self._on_progress)
        self._thread.finished.connect(self._on_finished)
        self._thread.start()

    def _on_stop(self) -> None:
        self.ctx.cancel.set()
        self.status.setText("正在中止…")

    def _on_progress(self, kind: str, msg: str) -> None:
        self.status.setText(msg)
        if kind == "task":
            self.bar.show()

    def _on_done(self, result: dict) -> None:
        self.overview.setPlainText(result.get("overview", ""))
        subs = result.get("subtasks", [])
        for a in result.get("assignments", []):
            row = self.table.rowCount()
            self.table.insertRow(row)
            title = next((t.title for t in subs
                          if t.index == a.task.index), a.task.title)
            self._set(row, 0, title)
            self._set(row, 1, a.model_key or "—")
            self._set(row, 2, f"{a.reason}（{a.score}）")
        # 结果按 state 回填
        for i, r in enumerate(result.get("results", [])):
            if i < self.table.rowCount():
                self._set(i, 3, r.get("status", ""))
                self._set(i, 4, (r.get("output") or r.get("error") or "")[:40])
        self.status.setText(result.get("summary", "完成"))
        self._record_history(result)

    def _record_history(self, result: dict) -> None:
        try:
            rec = {
                "task_text": self.input.text().strip(),
                "status": result.get("status", "done"),
                "summary": result.get("summary", ""),
                "overview": result.get("overview", "")[:500],
                "assignments": [assignment_to_dict(a)
                                for a in result.get("assignments", [])],
                "duration_ms": result.get("duration_ms", 0),
            }
            self.ctx.history.add(rec)
        except Exception as e:  # noqa: BLE001 历史保存失败不影响主流程
            print("历史保存失败:", e)

    def _on_failed(self, err: str) -> None:
        self.status.setText("执行出错")
        self.overview.setPlainText(f"执行异常：{err}")

    def _on_finished(self) -> None:
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.bar.setVisible(False)

    def _set(self, row: int, col: int, text: str) -> None:
        item = QTableWidgetItem(text)
        if col == 3:
            item.setForeground(Qt.darkGreen if text == "ok" else Qt.darkRed)
        self.table.setItem(row, col, item)


# ============================================================
# 模型管理
# ============================================================
class ModelManagerPage(QWidget):
    saved = Signal()

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["标识", "名称", "来源", "端点", "技能", "成本", "能力", "主模型"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        root.addWidget(self.table)

        form = QGroupBox("模型编辑")
        fl = QFormLayout(form)
        self.f_key = QLineEdit(); self.f_name = QLineEdit()
        self.f_source = QComboBox(); self.f_source.addItems(["local", "cloud"])
        self.f_endpoint = QLineEdit(); self.f_skills = QLineEdit()
        self.f_cost = QDoubleSpinBox(); self.f_cost.setDecimals(2); self.f_cost.setRange(0.1, 1000.0)
        self.f_cap = QSpinBox(); self.f_cap.setRange(0, 100)
        self.f_primary = QComboBox(); self.f_primary.addItems(["否", "是"])
        self.f_apikey = QLineEdit()
        for w, lbl in [(self.f_key, "标识 key"), (self.f_name, "显示名"),
                       (self.f_source, "来源"), (self.f_endpoint, "远端端点/模型"),
                       (self.f_skills, "技能(逗号分隔)"), (self.f_cost, "成本"),
                       (self.f_cap, "能力(0-100)"), (self.f_primary, "设为主模型"),
                       (self.f_apikey, "凭证键(云端)")]:
            fl.addRow(lbl, w)
        btns = QHBoxLayout()
        self.btn_add = QPushButton("新增"); self.btn_update = QPushButton("更新")
        self.btn_del = QPushButton("删除"); self.btn_primary = QPushButton("设为主模型")
        self.btn_save = QPushButton("保存到存储")
        for b in (self.btn_add, self.btn_update, self.btn_del,
                  self.btn_primary, self.btn_save):
            btns.addWidget(b)
        root.addWidget(form)
        root.addLayout(btns)

        self.btn_add.clicked.connect(self._start_add)
        self.btn_update.clicked.connect(self._update)
        self.btn_del.clicked.connect(self._delete)
        self.btn_primary.clicked.connect(self._set_primary)
        self.btn_save.clicked.connect(self._save)
        self.table.itemSelectionChanged.connect(self._load_selected)

    def refresh(self) -> None:
        self.table.setRowCount(0)
        for m in self.ctx.registry.all():
            self._append_row(m)
            self.ctx.cfg["models"] = self.ctx.registry.to_list_dicts()

    def _append_row(self, m: ModelInfo) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._set(row, 0, m.key); self._set(row, 1, m.name); self._set(row, 2, m.source)
        self._set(row, 3, m.endpoint); self._set(row, 4, ",".join(m.skills))
        self._set(row, 5, str(m.cost)); self._set(row, 6, str(int(m.capability)))
        self._set(row, 7, "是" if m.is_primary else "")

    def _set(self, row: int, col: int, text: str) -> None:
        self.table.setItem(row, col, QTableWidgetItem(text))

    def _load_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        r = rows[0].row()
        self.f_key.setText(self.table.item(r, 0).text())
        self.f_name.setText(self.table.item(r, 1).text())
        self.f_source.setCurrentText(self.table.item(r, 2).text())
        self.f_endpoint.setText(self.table.item(r, 3).text())
        self.f_skills.setText(self.table.item(r, 4).text())
        self.f_cost.setValue(float(self.table.item(r, 5).text()))
        self.f_cap.setValue(int(self.table.item(r, 6).text()))
        self.f_primary.setCurrentText(self.table.item(r, 7).text() or "否")
        self.f_key.setReadOnly(True)  # 编辑已有行时 key 作为主键不可改，规避更新错位

    def _start_add(self) -> None:
        """开始新增：解锁 key 并清空表单（区别于编辑已有行）。"""
        self.f_key.setReadOnly(False)
        for w in (self.f_key, self.f_name, self.f_endpoint,
                  self.f_skills, self.f_apikey):
            w.clear()
        self.f_source.setCurrentIndex(0)
        self.f_cost.setValue(1.0)
        self.f_cap.setValue(50)
        self.f_primary.setCurrentIndex(0)

    def _form_model(self) -> ModelInfo:
        return ModelInfo(
            key=self.f_key.text().strip(),
            name=self.f_name.text().strip() or self.f_key.text().strip(),
            source=self.f_source.currentText(),
            endpoint=self.f_endpoint.text().strip(),
            skills=[s.strip() for s in self.f_skills.text().split(",") if s.strip()],
            cost=round(self.f_cost.value(), 2),
            capability=float(self.f_cap.value()),
            is_primary=(self.f_primary.currentText() == "是"),
        )

    def _add(self) -> None:
        m = self._form_model()
        if not m.key:
            QMessageBox.warning(self, "提示", "标识 key 不能为空。")
            return
        self.ctx.registry.register(m)
        self.refresh()

    def _update(self) -> None:
        m = self._form_model()
        try:
            self.ctx.registry.update(m)
            self.refresh()
        except KeyError as e:
            QMessageBox.warning(self, "提示", str(e))

    def _delete(self) -> None:
        key = self.f_key.text().strip()
        if not key:
            return
        if QMessageBox.question(self, "确认", f"删除模型 {key}？") == QMessageBox.Yes:
            self.ctx.registry.remove(key)
            self.refresh()

    def _set_primary(self) -> None:
        key = self.f_key.text().strip()
        if not key:
            return
        try:
            self.ctx.registry.set_primary(key)
            self.refresh()
        except KeyError as e:
            QMessageBox.warning(self, "提示", str(e))

    def _save(self) -> None:
        self.ctx.cfg["models"] = self.ctx.registry.to_list_dicts()
        self.ctx.cfg["weights"] = {
            "capability": self.ctx.cfg.get("weights", {}).get("capability", 0.7),
            "cost": self.ctx.cfg.get("weights", {}).get("cost", 0.3)}
        app_config.save_config(self.ctx.cfg)
        self.saved.emit()


# ============================================================
# 规则配置
# ============================================================
class RulesPage(QWidget):
    saved = Signal()

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        rules = self.ctx.cfg.get("rules", {})
        form = QFormLayout()
        self.kw = QLineEdit(",".join(rules.get("sensitive_keywords", [])))
        form.addRow("敏感关键词(逗号分隔，命中则强制本地)", self.kw)
        self.offline = QComboBox()
        self.offline.addItems(["是", "否"])
        self.offline.setCurrentText("是" if rules.get("offline_use_cloud", True) else "否")
        form.addRow("本地离线时是否回退云端", self.offline)
        root.addLayout(form)
        self.btn = QPushButton("保存规则")
        self.btn.clicked.connect(self._save)
        root.addWidget(self.btn)
        root.addStretch(1)

    def refresh(self) -> None:
        rules = self.ctx.cfg.get("rules", {})
        self.kw.setText(",".join(rules.get("sensitive_keywords", [])))
        self.offline.setCurrentText("是" if rules.get("offline_use_cloud", True) else "否")

    def _save(self) -> None:
        self.ctx.cfg["rules"] = {
            "sensitive_keywords": [s.strip() for s in self.kw.text().split(",") if s.strip()],
            "offline_use_cloud": self.offline.currentText() == "是"}
        app_config.save_config(self.ctx.cfg)
        self.saved.emit()


# ============================================================
# 任务历史
# ============================================================
class HistoryPage(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        btns = QHBoxLayout()
        self.btn_refresh = QPushButton("刷新")
        self.btn_clear = QPushButton("清空历史")
        btns.addWidget(self.btn_refresh); btns.addWidget(self.btn_clear)
        btns.addStretch(1)
        root.addLayout(btns)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["时间", "主任务", "状态", "子任务数"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemDoubleClicked.connect(self._show_detail)
        root.addWidget(self.table)
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_clear.clicked.connect(self._clear)

    def refresh(self) -> None:
        self.table.setRowCount(0)
        for r in self.ctx.history.all():
            row = self.table.rowCount()
            self.table.insertRow(row)
            vals = [r.get("created_at", ""), r.get("task_text", ""),
                    r.get("status", ""), str(len(r.get("assignments", [])))]
            for c, v in enumerate(vals):
                self.table.setItem(row, c, QTableWidgetItem(v))

    def _clear(self) -> None:
        if QMessageBox.question(self, "确认", "清空全部历史？") == QMessageBox.Yes:
            self.ctx.history.clear()
            self.refresh()

    def _show_detail(self, item) -> None:
        rec = self.ctx.history.all()[item.row()]
        detail = f"时间：{rec.get('created_at')}\n任务：{rec.get('task_text')}\n" \
                 f"状态：{rec.get('status')}  耗时：{rec.get('duration_ms')}ms\n\n" \
                 f"分配：\n"
        for a in rec.get("assignments", []):
            detail += f"  - {a.get('task_title')} → {a.get('model_key')} ({a.get('reason')})\n"
        detail += f"\n总览：\n{rec.get('overview', '')}"
        QMessageBox.information(self, "任务详情", detail)


# ============================================================
# 设置
# ============================================================
class SettingsPage(QWidget):
    saved = Signal()

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        g = self.ctx.cfg.get("general", {})
        cloud = self.ctx.cfg.get("cloud", {})
        w = self.ctx.cfg.get("weights", {})

        f = QFormLayout()
        self.ollama = QLineEdit(g.get("ollama_url", "http://127.0.0.1:11434"))
        self.timeout = QSpinBox(); self.timeout.setRange(5, 600); self.timeout.setValue(int(g.get("timeout_seconds", 120)))
        self.w_cap = QDoubleSpinBox(); self.w_cap.setRange(0, 1); self.w_cap.setDecimals(1)
        self.w_cap.setValue(float(w.get("capability", 0.7)))
        self.w_cost = QDoubleSpinBox(); self.w_cost.setRange(0, 1); self.w_cost.setDecimals(1)
        self.w_cost.setValue(float(w.get("cost", 0.3)))
        self.cb_base = QLineEdit(cloud.get("base_url", "https://api.openai.com/v1"))
        self.cb_model = QLineEdit(cloud.get("default_model", ""))
        self.cb_key = QLineEdit(app_config.load_credential("cloud_api_key",""))
        self.cb_key.setEchoMode(QLineEdit.Password)
        f.addRow("Ollama 地址", self.ollama)
        f.addRow("单次调用超时(秒)", self.timeout)
        f.addRow("能力权重", self.w_cap)
        f.addRow("成本权重", self.w_cost)
        f.addRow("云端 Base URL", self.cb_base)
        f.addRow("云端默认模型", self.cb_model)
        f.addRow("云端 API Key(本地保存)", self.cb_key)
        root.addLayout(f)

        self.btn = QPushButton("保存设置")
        self.btn.clicked.connect(self._save)
        root.addWidget(self.btn)
        root.addStretch(1)

    def refresh(self) -> None:  # 与 _build 相同字段，重新读取一下即可
        g = self.ctx.cfg.get("general", {}); w = self.ctx.cfg.get("weights", {})
        cloud = self.ctx.cfg.get("cloud", {})
        self.ollama.setText(g.get("ollama_url", "http://127.0.0.1:11434"))
        self.timeout.setValue(int(g.get("timeout_seconds", 120)))
        self.w_cap.setValue(float(w.get("capability", 0.7)))
        self.w_cost.setValue(float(w.get("cost", 0.3)))
        self.cb_base.setText(cloud.get("base_url", "https://api.openai.com/v1"))
        self.cb_model.setText(cloud.get("default_model", ""))
        self.cb_key.setText(app_config.load_credential("cloud_api_key", ""))

    def _save(self) -> None:
        self.ctx.cfg["general"] = {
            "main_model_key": self.ctx.cfg.get("general", {}).get("main_model_key", "main"),
            "ollama_url": self.ollama.text().strip(),
            "timeout_seconds": int(self.timeout.value())}
        self.ctx.cfg["weights"] = {
            "capability": round(self.w_cap.value(), 1),
            "cost": round(self.w_cost.value(), 1)}
        self.ctx.cfg["cloud"] = {
            "base_url": self.cb_base.text().strip(),
            "default_model": self.cb_model.text().strip(),
            "api_key_ref": "cloud_api_key"}
        app_config.save_config(self.ctx.cfg)
        if self.cb_key.text().strip():
            app_config.save_credential("cloud_api_key", self.cb_key.text())
        self.saved.emit()


# ============================================================
# 主窗口
# ============================================================
class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle("Agent 自动化智能体 · 正式版")
        self.resize(1080, 720)

        self.tabs = QTabWidget()
        self.workbench = WorkbenchPage(ctx)
        self.models_page = ModelManagerPage(ctx)
        self.rules_page = RulesPage(ctx)
        self.history_page = HistoryPage(ctx)
        self.settings_page = SettingsPage(ctx)

        self.tabs.addTab(self.workbench, "工作台")
        self.tabs.addTab(self.models_page, "模型管理")
        self.tabs.addTab(self.rules_page, "规则配置")
        self.tabs.addTab(self.history_page, "任务历史")
        self.tabs.addTab(self.settings_page, "设置")
        self.setCentralWidget(self.tabs)

        self.models_page.saved.connect(self._on_config_saved)
        self.rules_page.saved.connect(self._on_config_saved)
        self.settings_page.saved.connect(self._on_config_saved)
        self.statusBar().showMessage("就绪")

        # 初始刷新各页
        self.models_page.refresh()
        self.history_page.refresh()

    def _on_config_saved(self) -> None:
        # 配置变更后重建编排器，并刷新各页
        self.ctx.cfg = app_config.load_config()
        self.ctx.registry.from_list_dicts(self.ctx.cfg.get("models", []))
        self.ctx.orchestrator = Orchestrator(self.ctx.registry, self.ctx.cfg)
        self.models_page.refresh()
        self.rules_page.refresh()
        self.settings_page.refresh()
        self.statusBar().showMessage("配置已生效")