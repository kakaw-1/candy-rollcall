"""JSON 持久化：原子写入与损坏恢复（§5.3）。

- UTF-8 写入；先写同目录临时文件、flush/fsync、再用 os.replace 原子替换。
- data.json.bak 留存最近一次可恢复副本。
- 损坏时先尝试 bak；两者均失败抛出 DataCorruptError，由上层提示用户恢复备份，不悄悄覆盖。
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Optional

from . import user_paths

SCHEMA_VERSION = 2


class DataCorruptError(RuntimeError):
    """data.json 与 .bak 均无法加载。"""


class JsonStore:
    """线程安全的名单+设置存储。"""

    def __init__(self, path: Optional[Path] = None):
        self._path = Path(path) if path else user_paths.data_file()
        self._lock = threading.RLock()

    # ---- 路径 ----
    @property
    def path(self) -> Path:
        return self._path

    @property
    def bak_path(self) -> Path:
        return self._path.with_suffix(self._path.suffix + ".bak")

    # ---- 读取 ----
    def load(self) -> Optional[dict]:
        """加载有效数据；损坏时尝试 .bak。两者皆失败抛 DataCorruptError。文件不存在返回 None。"""
        with self._lock:
            if not self._path.exists():
                return None
            try:
                return self._read_json(self._path)
            except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                # 主文件损坏，尝试上次副本
                if self.bak_path.exists():
                    try:
                        data = self._read_json(self.bak_path)
                        # 恢复副本回写为主文件（此时主文件已损坏，覆盖它是恢复而非破坏）
                        self._atomic_write(data)
                        return data
                    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                        pass
                raise DataCorruptError(
                    f"数据文件损坏且无可恢复副本: {self._path}。"
                    f"请使用教师自己的 JSON 备份恢复，数据未被覆盖。"
                )

    @staticmethod
    def _read_json(path: Path) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("根节点必须是对象")
        return data

    # ---- 写入 ----
    def save(self, data: dict) -> None:
        """原子保存。写入前把现有主文件复制为 .bak（仅当主文件当前可解析）。"""
        with self._lock:
            # 更新前留存副本
            if self._path.exists():
                try:
                    current = self._read_json(self._path)
                    self._atomic_write(current, target=self.bak_path)
                except (json.JSONDecodeError, OSError, ValueError):
                    pass  # 主文件已坏时不覆盖旧 .bak
            self._atomic_write(data)
            # 首次保存后也保证存在可恢复副本（最近一次有效数据）
            if not self.bak_path.exists():
                try:
                    self._atomic_write(data, target=self.bak_path)
                except OSError:
                    pass

    def _atomic_write(self, data: dict, target: Optional[Path] = None) -> None:
        target = Path(target) if target else self._path
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        fd, tmp_path = tempfile.mkstemp(prefix=".data-", suffix=".tmp", dir=str(target.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, target)
        except OSError:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # ---- 备份文件读写（教师可见的完整备份走 import_export，这里只管内部 .bak） ----
    def write_raw(self, path: Path, data: dict) -> None:
        with self._lock:
            self._atomic_write(data, target=Path(path))


def empty_document() -> dict:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "students": [],
        "settings": {},
    }
