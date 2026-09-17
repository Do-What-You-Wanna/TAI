"""日志初始化：将日志写入 <应用目录>/logs/app.log，并输出到控制台。

便于正式版排查运行时问题（尤其模型调用失败、异常详情）。
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# 打包后日志写到 exe 同目录 logs/（可写）；开发时为项目目录 logs/
if getattr(sys, "frozen", False):
    _BASE_DIR = Path(sys.executable).resolve().parent
else:
    _BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = _BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "app.log"


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """初始化根日志：文件(滚动) + 控制台 双输出。返回根 logger。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if root.handlers:  # 避免重复初始化
        return root

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    file_h = RotatingFileHandler(
        LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    file_h.setFormatter(fmt)
    console_h = logging.StreamHandler()
    console_h.setFormatter(fmt)

    root.setLevel(level)
    root.addHandler(file_h)
    root.addHandler(console_h)
    return root