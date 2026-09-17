"""打包/运行入口（PyInstaller 使用）。

开发运行：python run_app.py
打包后：生成 Agent.exe，直接双击启动。
"""
from agent_app.main import main

if __name__ == "__main__":
    main()