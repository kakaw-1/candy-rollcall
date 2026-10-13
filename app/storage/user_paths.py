"""数据目录定位（§5.3）。

Windows: %LOCALAPPDATA%\\CandyRollCall\\
其他平台（开发/测试）: ~/.candy_rollcall/ 或环境变量 CANDY_ROLLCALL_HOME。
不写在 PyInstaller _MEIPASS 解包目录，也不默认 EXE 所在目录。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "CandyRollCall"


def data_dir() -> Path:
    env = os.environ.get("CANDY_ROLLCALL_HOME")
    if env:
        return Path(env)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_DIR_NAME
    return Path.home() / ".candy_rollcall"


def data_file() -> Path:
    return data_dir() / "data.json"


def backup_file() -> Path:
    return data_dir() / "data.json.bak"


def logs_dir() -> Path:
    return data_dir() / "logs"


def ensure_dirs() -> Path:
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def is_frozen() -> bool:
    """PyInstaller one-file 冻结运行时 sys._MEIPASS 存在；那里不可写。"""
    return hasattr(sys, "_MEIPASS") or getattr(sys, "frozen", False)
