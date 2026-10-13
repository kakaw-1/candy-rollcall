"""线程安全应用会话单例（§3.2）。

唯一事实来源：内存状态 + 本地 JSON。前端不得在 localStorage 维护正式名单。
所有共享状态（名单、drawn_ids、设置）经互斥锁保护；页面、悬浮球通过此服务交互。
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional

from . import models
from .draw_service import DrawService
from .models import Student, ValidationError
from ..storage.json_store import JsonStore, SCHEMA_VERSION

CONFIRM_TOKEN = "CONFIRM"  # 后端确认标记：涉及老师确认的动作须传入


class ApplicationState:
    _instance: Optional["ApplicationState"] = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "ApplicationState":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self):
        if ApplicationState._instance is not None:
            raise RuntimeError("ApplicationState 是单例，请使用 ApplicationState.instance()")
        self._lock = threading.RLock()
        self._store = JsonStore()
        self._draw = DrawService()
        self._settings: dict = models.default_settings()
        self._revision: int = 1  # 名单修订号，防过期草稿覆盖（§10）
        self._listeners: List[Callable[[dict], None]] = []
        self._webview2_available: Optional[bool] = None

    # ---------- 生命周期 ----------
    def startup(self) -> dict:
        """加载数据文件；异常退出后重启重置本轮但保留名单设置（AT-29）。

        数据损坏且无可恢复副本时从干净状态启动（教师可用外部备份恢复，
        AT-27：不悄悄覆盖原数据——损坏文件保留在磁盘上）。
        """
        with self._lock:
            students: List[Student] = []
            data = None
            try:
                data = self._store.load()
            except Exception:
                data = None  # 损坏：保留损坏文件，从空开始
            if data:
                try:
                    raw_students = data.get("students", [])
                    if not isinstance(raw_students, list):
                        raise ValidationError("students 必须是数组")
                    students = [Student.from_dict(s) for s in raw_students]
                    self._settings = models.validate_settings(data.get("settings"))
                except ValidationError:
                    # 单条坏记录时尽量保留其余；整体结构坏则从空开始
                    students = []
                    self._settings = models.default_settings()
                    for s in raw_students if isinstance(raw_students, list) else []:
                        try:
                            students.append(Student.from_dict(s))
                        except ValidationError:
                            continue
            # drawn_ids 不持久化：重启即轮次从头开始（§5.2）
            self._draw.set_students(students, reset_round=True)
            return self.full_snapshot()

    def persist(self) -> None:
        with self._lock:
            doc = {
                "schemaVersion": SCHEMA_VERSION,
                "students": [s.to_dict() for s in self._draw.get_students()],
                "settings": dict(self._settings),
            }
            self._store.save(doc)

    # ---------- 订阅广播 ----------
    def subscribe(self, callback: Callable[[dict], None]) -> None:
        with self._lock:
            self._listeners.append(callback)

    def _broadcast(self, event: str) -> None:
        with self._lock:
            snap = self.full_snapshot()
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb({"event": event, **snap})
            except Exception:
                pass

    # ---------- 名单 ----------
    def get_students(self) -> List[Student]:
        with self._lock:
            return self._draw.get_students()

    def get_revision(self) -> int:
        with self._lock:
            return self._revision

    def commit_students(self, draft: List[dict], strategy: str,
                        decisions: Optional[Dict[str, str]] = None,
                        confirmed: bool = False,
                        base_revision: Optional[int] = None) -> dict:
        """校验 → 保存 → 重置本轮 → 广播（§10）。

        draft: [{name, studentNo?}] 或含 id（编辑保留）。
        strategy: replace | append
        decisions: {key: "skip"|"keep"}，key 为预览时给定的重复键。
        base_revision: 过期草稿防护（§10 注意）；不匹配时拒绝提交。
        返回 {"ok": bool, ...}；校验失败返回 ok=False 而非抛异常（供桥接直接返回）。
        """
        if strategy not in ("replace", "append"):
            return {"ok": False, "error": "ValidationError", "message": f"非法策略: {strategy!r}"}
        if not confirmed:
            return {"ok": False, "error": "ValidationError", "message": "缺少确认标记，名单未提交"}
        if base_revision is not None and base_revision != self._revision:
            return {"ok": False, "error": "STALE_REVISION",
                    "message": "名单已被其他窗口修改，请刷新后重试"}

        try:
            new_students: List[Student] = []
            for d in draft:
                if isinstance(d, dict) and d.get("id"):
                    new_students.append(Student.from_dict(d))     # 编辑：保留原 ID
                else:
                    new_students.append(Student.create(d.get("name"), d.get("studentNo")))
        except ValidationError as e:
            return {"ok": False, "error": "ValidationError", "message": str(e)}

        with self._lock:
            if len(new_students) > models.MAX_STUDENTS:
                return {"ok": False, "error": "ValidationError",
                        "message": f"名单共 {len(new_students)} 人，"
                                   f"超过上限 {models.MAX_STUDENTS} 人，已阻止提交"}
            if strategy == "append":
                existing = self._draw.get_students()
                if len(existing) + len(new_students) > models.MAX_STUDENTS:
                    return {"ok": False, "error": "ValidationError",
                            "message": f"追加后共 {len(existing) + len(new_students)} 人，"
                                       f"超过上限 {models.MAX_STUDENTS} 人，已阻止提交"}

            # 疑似重复决策（§8.3 不自动删除或合并）
            decisions = decisions or {}
            kept: List[Student] = []
            for i, stu in enumerate(new_students):
                if decisions.get(f"new:{i}") == "skip":
                    continue
                kept.append(stu)

            if strategy == "replace":
                final = kept
            else:
                final = self._draw.get_students() + kept

            if len(final) > models.MAX_STUDENTS:
                return {"ok": False, "error": "ValidationError",
                        "message": f"提交后共 {len(final)} 人，超过上限 {models.MAX_STUDENTS} 人"}

            self._draw.set_students(final, reset_round=True)  # 名单变化重置本轮（§6.3）
            self._revision += 1
            self.persist()
            self._broadcast("students_changed")
            return {"ok": True, **self.full_snapshot()}

    # ---------- 抽取 ----------
    def draw(self, count: Optional[int] = None) -> dict:
        with self._lock:
            n = count if count is not None else int(self._settings["drawCount"])
            try:
                result = self._draw.draw(n)
            except Exception as e:
                err = type(e).__name__
                return {"ok": False, "error": "BUSY" if err == "DrawBusyError" else err,
                        "message": str(e)}
            return {"ok": True, **result.to_dict(), "state": self._draw.snapshot()}

    def mark_reveal_complete(self, draw_id: str) -> dict:
        with self._lock:
            applied = self._draw.mark_reveal_complete(draw_id)
            return {"ok": True, "applied": applied, "state": self._draw.snapshot()}

    def change_mode(self, mode: str, confirmed: bool = False) -> dict:
        with self._lock:
            if mode not in models.VALID_MODES:
                return {"ok": False, "error": "ValidationError", "message": f"非法模式: {mode!r}"}
            if not confirmed:
                # 返回需要确认的信号，前端展示确认框
                return {"ok": False, "needsConfirm": True,
                        "message": "切换模式将重置本轮点名进度，确认切换吗？"}
            self._draw.set_mode(mode)
            self._settings["mode"] = mode
            self.persist()
            self._broadcast("mode_changed")
            return {"ok": True, "state": self._draw.snapshot()}

    def reset_round(self, confirmed: bool = False) -> dict:
        with self._lock:
            if not confirmed:
                return {"ok": False, "needsConfirm": True,
                        "message": "确认清空本轮已抽记录并重新开始吗？"}
            self._draw.reset_round()
            self._broadcast("round_reset")
            return {"ok": True, "state": self._draw.snapshot()}

    # ---------- 设置 ----------
    def set_preference(self, key: str, value: Any) -> dict:
        with self._lock:
            if key not in models.SETTING_KEYS:
                return {"ok": False, "error": "ValidationError", "message": f"非法设置键: {key}"}
            try:
                coerced = models._coerce_setting(key, value, self._settings.get(key))
            except (ValidationError, TypeError, ValueError) as e:
                return {"ok": False, "error": "ValidationError", "message": str(e)}
            self._settings[key] = coerced
            self.persist()
            self._broadcast("preference_changed")
            return {"ok": True, "value": coerced}

    def set_settings_raw(self, settings: dict) -> None:
        """恢复备份时整体写入设置（已由调用方校验）。"""
        with self._lock:
            self._settings = models.validate_settings(settings)
            self.persist()

    def get_settings(self) -> dict:
        with self._lock:
            return dict(self._settings)

    # ---------- 快照 ----------
    def full_snapshot(self) -> dict:
        with self._lock:
            return {
                "revision": self._revision,
                "students": [s.to_dict() for s in self._draw.get_students()],
                "settings": dict(self._settings),
                "draw": self._draw.snapshot(),
            }
