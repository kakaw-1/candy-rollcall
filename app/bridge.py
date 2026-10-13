"""js_api 白名单桥接（§10）。

pywebview 在独立线程调用这些方法（非线程安全），全部写操作经
ApplicationState 的锁保护。仅暴露白名单方法，前端不能任意注入路径。

确认类动作（change_mode / reset_round / commit_students / restore_commit）
返回 needsConfirm 供前端展示确认框；真正提交须带 confirmed=True，
且后端用 revision 防止过期草稿覆盖（§10 注意）。
"""
from __future__ import annotations

import threading
from typing import Any, Optional

from .core.application_state import ApplicationState
from .core.models import ValidationError
from .storage import import_export


class Bridge:
    """window.pywebview.api 的后端对象。每个公开方法对应 §10 API 表。"""

    def __init__(self, state: ApplicationState, window_ctrl: Optional[dict] = None):
        """
        window_ctrl: 可选 {"collapse": fn, "fullscreen": fn, "quit": fn,
                           "open_manage": fn, "close_manage": fn}
        由 main.py 注入窗口编排回调（跨线程 UI 指令经此投递）。
        """
        self._state = state
        self._ctrl = window_ctrl or {}
        self._import_tokens: dict = {}   # fileToken -> 解析后的草稿
        self._token_seq = 0
        self._token_lock = threading.Lock()

    # ================ 状态 ================
    def get_snapshot(self) -> dict:
        """全班名单、设置、当前状态摘要（管理页初始化用）。"""
        return {"ok": True, **self._state.full_snapshot()}

    # ================ 抽取 ================
    def draw(self, count: Optional[int] = None) -> dict:
        """原子生成结果；忙时返回 BUSY（不重复计数，AT-18）。"""
        result = self._state.draw(count)
        if not result.get("ok"):
            if result.get("error") == "DrawBusyError":
                result["error"] = "BUSY"
            return result
        # 供动画层使用
        snap = result.get("state", {})
        result["baseAnimationSeconds"] = self._state.get_settings()["baseAnimationSeconds"]
        result["remaining"] = snap.get("remaining")
        return result

    def mark_reveal_complete(self, draw_id: str) -> dict:
        """常规完成/立即揭晓共用；同 drawId 幂等，过期忽略（AT-38）。"""
        return self._state.mark_reveal_complete(draw_id)

    def set_draw_count(self, count: int) -> dict:
        """仅点名主页调用；保存设置不重置轮次（§10）。"""
        result = self._state.set_preference("drawCount", count)
        return result

    def change_mode(self, mode: str, confirmed: bool = False) -> dict:
        return self._state.change_mode(mode, confirmed=confirmed)

    def reset_round(self, confirmed: bool = False) -> dict:
        return self._state.reset_round(confirmed=confirmed)

    # ================ 名单 ================
    def commit_students(self, draft: list, strategy: str,
                        decisions: Optional[dict] = None,
                        confirmed: bool = False,
                        base_revision: Optional[int] = None) -> dict:
        """校验 → 保存 → 重置本轮 → 广播。前端须传当前 revision。"""
        return self._state.commit_students(draft, strategy, decisions, confirmed, base_revision)

    # ================ 导入 ================
    def import_file_dialog(self) -> dict:
        """打开文件选择对话框（pywebview 原生），返回 token 与原始行预览。"""
        import webview
        windows = webview.windows
        if not windows:
            return {"ok": False, "error": "NoWindow"}
        result = windows[0].create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=False,
            file_types=("Excel/CSV (*.xlsx;*.csv)", "所有文件 (*.*)",),
        )
        if not result:
            return {"ok": False, "error": "Cancelled"}
        path = result[0] if isinstance(result, (list, tuple)) else result
        return self._load_import_file(path)

    def _load_import_file(self, path: str) -> dict:
        try:
            lower = str(path).lower()
            if lower.endswith(".xlsx"):
                rows = import_export.read_xlsx_rows(path)
            elif lower.endswith(".csv"):
                with open(path, "rb") as f:
                    content = f.read()
                rows, encoding = self._decode_csv_with_fallback(content)
            else:
                return {"ok": False, "error": "UnsupportedFormat",
                        "message": "仅支持 .xlsx 与 .csv 文件"}
        except ValidationError as e:
            return {"ok": False, "error": "ValidationError", "message": str(e)}
        except OSError as e:
            return {"ok": False, "error": "IOError", "message": str(e)}

        with self._token_lock:
            self._token_seq += 1
            token = f"f{self._token_seq}"
            self._import_tokens[token] = {"rows": rows, "path": path}
        return {"ok": True, "fileToken": token, "rowCount": len(rows),
                "previewRows": rows[:20], "csvEncoding": encoding if lower.endswith(".csv") else None}

    @staticmethod
    def _decode_csv_with_fallback(content: bytes):
        """优先 utf-8-sig，失败回退 GB18030，仍失败给出乱码警告（§8.2）。"""
        for enc in ("utf-8-sig", "gb18030"):
            try:
                return import_export.read_csv_rows(content, enc), enc
            except ValidationError:
                continue
        raise ValidationError("无法识别文件编码，请用 UTF-8 或 GB18030 重新保存")

    def import_preview(self, file_token: str, mapping: dict,
                       has_header: bool = True) -> dict:
        """按列映射返回预览与问题清单，不写库。"""
        with self._token_lock:
            entry = self._import_tokens.get(file_token)
        if not entry:
            return {"ok": False, "error": "InvalidToken", "message": "文件会话已失效，请重新选择"}
        try:
            drafts = import_export.rows_to_drafts(entry["rows"], mapping, has_header)
            preview = import_export.preview_import(drafts, self._state.get_students())
        except ValidationError as e:
            return {"ok": False, "error": "ValidationError", "message": str(e)}
        return {"ok": True, "fileToken": file_token, **preview}

    def import_from_paste(self, text: str) -> dict:
        """批量粘贴预览。"""
        drafts = import_export.parse_paste_text(text)
        preview = import_export.preview_import(drafts, self._state.get_students())
        return {"ok": True, **preview}

    # ================ 导出 ================
    def export_students(self, fmt: str) -> dict:
        """xlsx/csv 导出：打开保存对话框并写入本地文件（AT-34）。"""
        import webview
        if fmt not in ("xlsx", "csv"):
            return {"ok": False, "error": "ValidationError", "message": "格式须为 xlsx 或 csv"}
        windows = webview.windows
        if not windows:
            return {"ok": False, "error": "NoWindow"}
        ext = "xlsx" if fmt == "xlsx" else "csv"
        result = windows[0].create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename=f"学生名单.{ext}",
            file_types=(f"{ext.upper()} (*.{ext})",),
        )
        if not result:
            return {"ok": False, "error": "Cancelled"}
        path = result[0] if isinstance(result, (list, tuple)) else result
        students = self._state.get_students()
        try:
            if fmt == "xlsx":
                data = import_export.export_xlsx(students)
            else:
                data = import_export.export_csv(students)
            with open(path, "wb") as f:
                f.write(data)
        except OSError as e:
            return {"ok": False, "error": "IOError", "message": str(e)}
        return {"ok": True, "path": str(path), "count": len(students)}

    # ================ 完整备份 / 恢复 ================
    def backup_all(self) -> dict:
        """完整 JSON 备份（不含本轮已抽状态，§5.4）。"""
        import webview
        windows = webview.windows
        if not windows:
            return {"ok": False, "error": "NoWindow"}
        result = windows[0].create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename="CandyRollCall-备份.json",
            file_types=("JSON (*.json)",),
        )
        if not result:
            return {"ok": False, "error": "Cancelled"}
        path = result[0] if isinstance(result, (list, tuple)) else result
        backup = import_export.build_backup(self._state.get_students(),
                                            self._state.get_settings())
        import json
        try:
            with open(path, "wb") as f:
                f.write(json.dumps(backup, ensure_ascii=False, indent=2).encode("utf-8"))
        except OSError as e:
            return {"ok": False, "error": "IOError", "message": str(e)}
        return {"ok": True, "path": str(path), "count": len(backup["students"])}

    def restore_preview(self) -> dict:
        """选择备份文件并预览：人数、设置、校验错误（不写库）。"""
        import webview
        windows = webview.windows
        if not windows:
            return {"ok": False, "error": "NoWindow"}
        result = windows[0].create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=False,
            file_types=("JSON (*.json)", "所有文件 (*.*)"),
        )
        if not result:
            return {"ok": False, "error": "Cancelled"}
        path = result[0] if isinstance(result, (list, tuple)) else result
        try:
            with open(path, "rb") as f:
                content = f.read()
            students, settings = import_export.parse_backup(content)
        except ValidationError as e:
            return {"ok": False, "error": "ValidationError", "message": str(e)}
        except OSError as e:
            return {"ok": False, "error": "IOError", "message": str(e)}
        with self._token_lock:
            self._token_seq += 1
            token = f"r{self._token_seq}"
            self._import_tokens[token] = {"backup": (students, settings)}
        return {
            "ok": True, "restoreToken": token, "path": str(path),
            "count": len(students),
            "students": [s.to_dict() for s in students[:20]],
            "settings": settings,
            "startWithWindowsInBackup": bool(settings.get("startWithWindows", False)),
        }

    def restore_commit(self, restore_token: str, confirmed: bool = False,
                       allow_start_with_windows: bool = False) -> dict:
        """恢复提交：替换当前数据并重置本轮（需显式确认，AT-28）。

        备份声明自启时，需老师单独确认才写注册表（§5.4）。
        """
        if not confirmed:
            return {"ok": False, "needsConfirm": True,
                    "message": "恢复备份将替换当前数据并重置本轮，确认继续吗？"}
        with self._token_lock:
            entry = self._import_tokens.pop(restore_token, None)
        if not entry or "backup" not in entry:
            return {"ok": False, "error": "InvalidToken", "message": "恢复会话已失效，请重新选择"}
        students, settings = entry["backup"]
        try:
            # 替换名单并重置本轮
            self._state.commit_students(
                [s.to_dict() for s in students], "replace", None, confirmed=True)
            self._state.set_settings_raw(settings)
        except ValidationError as e:
            return {"ok": False, "error": "ValidationError", "message": str(e)}

        # 自启单独确认（不未经同意写注册表）
        startup_note = None
        if settings.get("startWithWindows"):
            from .windows.startup import StartupManager
            sm = StartupManager()
            if allow_start_with_windows:
                sm.enable()
                startup_note = "已同步开启开机启动"
            else:
                sm.disable()
                startup_note = "备份包含开机启动开启，但未获确认，已保持关闭"

        return {"ok": True, "startupNote": startup_note,
                **self._state.full_snapshot()}

    # ================ 设置 ================
    def set_preference(self, key: str, value: Any) -> dict:
        return self._state.set_preference(key, value)

    # ================ 窗口编排 ================
    def show_manage(self) -> dict:
        """按需创建管理窗；已打开只激活（幂等）。"""
        fn = self._ctrl.get("open_manage")
        if fn:
            fn()
        return {"ok": True}

    def close_manage(self) -> dict:
        """关闭管理窗：销毁并恢复先前界面，不退出主进程。"""
        fn = self._ctrl.get("close_manage")
        if fn:
            fn()
        return {"ok": True}

    def collapse(self) -> dict:
        """收起点名窗变回悬浮球；不重置轮次（AT-10）。"""
        fn = self._ctrl.get("collapse")
        if fn:
            fn()
        return {"ok": True}

    def show_fullscreen(self) -> dict:
        """全屏切换；Esc 由前端监听退出（§4.3）。"""
        fn = self._ctrl.get("fullscreen")
        if fn:
            fn()
        return {"ok": True}

    def quit_app(self) -> dict:
        """统一退出：停止球线程、关闭所有窗口、结束进程（AT-09）。"""
        fn = self._ctrl.get("quit")
        if fn:
            fn()
        return {"ok": True}

    def notify_window_closed(self, kind: str) -> dict:
        """窗口关闭事件回传（管理窗 X 按钮 → close_manage 编排）。"""
        if kind == "manage":
            self.close_manage()
        return {"ok": True}
