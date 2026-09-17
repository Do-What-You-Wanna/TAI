"""统一 UI 主题：现代浅色样式（QSS）+ 控件辅助函数。

设计目标（对应本次优化方向）：
- 现代浅色：白底卡片 + 品牌色(#3b82f6)，浅灰背景分层。
- 全面统一：全应用同一套 QSS，按钮/输入框/表格/标签页风格一致。
- 可读性优先：统一中英文字体栈、适中的字号与行高、按钮加图形符号。
- 视觉精调（v2）：更大基础字号、更宽行高、分区标题更醒目、标签页图标间距。
"""
from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QLabel

# 品牌主色
PRIMARY = "#3b82f6"

# 全局 QSS 样式表（含分页/卡片/表格/输入/按钮等）
APP_QSS = """
QWidget {
    font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
    font-size: 14px;
    color: #1f2430;
}
QMainWindow, QTabWidget::pane, QWidget { background: #f5f7fa; }
QLabel { background: transparent; }

/* 分区标题：更大字号 + 品牌色左竖线 */
QLabel[section="true"] {
    font-size: 15px; font-weight: 700; color: #1e293b;
    border-left: 4px solid #3b82f6; padding-left: 10px; margin: 4px 0 8px 0;
}
/* 次级标签（普通字段标题） */
QLabel[field="true"] {
    font-size: 13px; font-weight: 600; color: #475569;
}
/* 空状态提示 */
QLabel[empty="true"] {
    color: #94a3b8; font-size: 14px; font-style: italic;
    padding: 32px; alignment: center;
}

QGroupBox {
    background: #ffffff; border: 1px solid #e3e8ef;
    border-radius: 10px; margin-top: 18px; padding: 14px 12px 10px 12px;
    font-weight: 700; color: #334155;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 16px; padding: 0 8px;
    color: #3b82f6; font-size: 14px;
}

QPushButton {
    background: #3b82f6; color: #ffffff; border: none;
    border-radius: 7px; padding: 8px 18px; font-weight: 600; font-size: 13px;
}
QPushButton:hover { background: #2f6ee0; }
QPushButton:pressed { background: #275bc0; }
QPushButton:disabled { background: #c7d2e0; color: #eef2f7; }
QPushButton#danger { background: #e5484d; }
QPushButton#danger:hover { background: #cf3a3f; }
QPushButton#ghost { background: #eef1f6; color: #334155; }
QPushButton#ghost:hover { background: #e3e9f2; }

QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: #ffffff; border: 1px solid #d4dbe6; border-radius: 7px;
    padding: 7px 10px; selection-background-color: #3b82f6;
}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QComboBox:focus { border: 1px solid #3b82f6; }
/* placeholder 文字更柔和 */
QLineEdit[placeholder="true"] { color: #94a3b8; }

QTableWidget {
    background: #ffffff; border: 1px solid #e3e8ef; border-radius: 8px;
    gridline-color: #eef1f6; selection-background-color: #e7f0ff;
    selection-color: #16213a; alternate-background-color: #f8fafc;
}
QHeaderView::section {
    background: #f1f5f9; color: #334155; border: none;
    border-bottom: 2px solid #dde4ee; padding: 10px 8px;
    font-weight: 700; font-size: 13px;
}
QTableWidget::item { padding: 6px 8px; }
QTableCornerButton::section { background: #f1f5f9; border: none; }

QTabWidget::pane { border: none; background: #f5f7fa; }
QTabBar::tab {
    background: #eef1f6; color: #5b6676; padding: 10px 22px; margin-right: 4px;
    border-top-left-radius: 8px; border-top-right-radius: 8px; font-size: 14px;
}
QTabBar::tab:selected { background: #ffffff; color: #3b82f6; font-weight: 700; }
QTabBar::tab:hover:!selected { background: #e3e9f2; }

QProgressBar {
    background: #eef1f6; border: none; border-radius: 5px; height: 10px;
}
QProgressBar::chunk { background: #3b82f6; border-radius: 5px; }
QProgressBar:indeterminate { background: #eef1f6; }
QProgressBar:indeterminate::chunk { background: #3b82f6; border-radius: 5px; }

QStatusBar { background: #f5f7fa; color: #5b6676; border-top: 1px solid #e3e8ef; }
QStatusBar QLabel { padding: 2px 8px; border-radius: 4px; }
QStatusBar QLabel[status="ok"] { background: #dcfce7; color: #166534; }
QStatusBar QLabel[status="warn"] { background: #fef3c7; color: #92400e; }
QStatusBar QLabel[status="error"] { background: #fee2e2; color: #991b1b; }

QScrollBar:vertical { background: transparent; width: 11px; margin: 2px; }
QScrollBar::handle:vertical { background: #c7d2e0; border-radius: 5px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #9fb4d6; }
QScrollBar:horizontal { background: transparent; height: 11px; margin: 2px; }
QScrollBar::handle:horizontal { background: #c7d2e0; border-radius: 5px; min-width: 24px; }
QScrollBar::handle:horizontal:hover { background: #9fb4d6; }
"""
# 说明：QSS 不支持真投影，故用浅灰底 + 白色圆角卡片实现现代分层。


def make_button(text: str, glyph: str | None = None,
                 kind: str | None = None) -> QPushButton:
    """创建统一样式的按钮。

    glyph：按钮前的图形符号（Unicode 技术符号，非 emoji）。
    kind ：'danger'(危险/删除) 或 'ghost'(次要)，对应 QSS 中的对象名。
    """
    btn = QPushButton(f"{glyph}  {text}" if glyph else text)
    if kind:
        btn.setObjectName(kind)
    return btn


def section_label(text: str) -> QLabel:
    """创建带醒目前缀的分区标题（QSS 中 section=true 样式）。"""
    lbl = QLabel(text)
    lbl.setProperty("section", True)
    return lbl


def empty_hint(text: str) -> QLabel:
    """创建空状态占位提示（表格无数据时显示）。"""
    lbl = QLabel(text)
    lbl.setProperty("empty", True)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter if hasattr(Qt, "AlignmentFlag")
                     else Qt.AlignCenter)
    return lbl


# 标签页图标映射（Unicode 几何符号，跨字体兼容）
TAB_ICONS = {
    "工作台": "◆",
    "模型管理": "▣",
    "规则配置": "⚙",
    "任务历史": "◴",
    "设置": "✦",
}


def styled_tab_text(title: str) -> str:
    """为标签页标题添加图标前缀。"""
    icon = TAB_ICONS.get(title, "")
    return f"{icon}  {title}" if icon else title


# 延迟导入 Qt 常量（避免文件顶层循环依赖）
from PySide6.QtCore import Qt  # noqa: E402 放在末尾，供 empty_hint 使用