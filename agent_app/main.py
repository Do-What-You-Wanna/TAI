"""Agent demo 启动入口。

用法：
    python -m agent_app.main
"""
from __future__ import annotations

import sys

from .gui.main_window import run_app


def main() -> int:
    return run_app()


if __name__ == "__main__":
    sys.exit(main())