"""随机抽取服务（§6）。

- 唯一抽样入口 DrawService.draw(count)，使用 secrets.SystemRandom 均匀不放回抽样。
- 完全随机模式：每次从全班抽；跨次可重复，同一次绝不重复。
- 不重复模式：从 students - drawn_ids 中抽，不足时抽完剩余。
- 抽取进行中返回 BUSY，防连点（AT-18）。
- drawId 绑定每次事务；mark_reveal_complete 幂等（AT-38）。
"""
from __future__ import annotations

import secrets
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from .models import Student

MODE_RANDOM = "random"
MODE_NO_REPEAT = "no_repeat"

# 状态机（§6.2）
EMPTY = "EMPTY"
IDLE = "IDLE"
ANIMATING = "ANIMATING"
REVEALING = "REVEALING"
RESULT = "RESULT"
ROUND_COMPLETE = "ROUND_COMPLETE"
ERROR = "ERROR"


class DrawBusyError(RuntimeError):
    """抽取进行中，拒绝并发开始（BUSY）。"""


class DrawResult:
    """一次抽取事务：结果在 __init__ 时即锁定。"""

    def __init__(self, draw_id: str, students: List[Student]):
        self.draw_id = draw_id
        self.students = students

    def to_dict(self) -> dict:
        return {
            "drawId": self.draw_id,
            "students": [s.to_dict() for s in self.students],
        }


class DrawService:
    def __init__(self, rng: Optional[secrets.SystemRandom] = None):
        self._rng = rng or secrets.SystemRandom()
        self._lock = threading.RLock()
        self._students: List[Student] = []          # 有序名单
        self._drawn_ids: set = set()                # 本轮已抽（仅会话内）
        self._mode: str = MODE_NO_REPEAT
        self._state: str = EMPTY                    # 初始无名单
        self._active: Optional[DrawResult] = None   # 进行中/最近一次事务
        self._reveal_done: bool = False             # 当前事务是否已确认完成
        self._round_complete: bool = False
        self._draw_seq: int = 0

    # ---------- 名单 ----------
    def set_students(self, students: List[Student], reset_round: bool = True) -> None:
        """整表替换名单（提交/恢复时调用）。调用方持有确认；此处在锁内重置轮次。"""
        with self._lock:
            self._students = list(students)
            if reset_round:
                self._reset_round_locked()
            if not self._students:
                self._state = EMPTY
            elif self._state in (EMPTY,):
                self._state = IDLE

    def get_students(self) -> List[Student]:
        with self._lock:
            return list(self._students)

    # ---------- 模式 ----------
    def get_mode(self) -> str:
        with self._lock:
            return self._mode

    def set_mode(self, mode: str) -> None:
        """调用前必须已完成用户确认（§2 切模式）。确认后重置本轮。"""
        if mode not in (MODE_RANDOM, MODE_NO_REPEAT):
            raise ValueError(f"非法模式: {mode!r}")
        with self._lock:
            if mode == self._mode:
                return
            self._mode = mode
            self._reset_round_locked()

    # ---------- 轮次 ----------
    def reset_round(self) -> None:
        with self._lock:
            self._reset_round_locked()

    def _reset_round_locked(self) -> None:
        self._drawn_ids.clear()
        self._active = None
        self._reveal_done = False
        self._round_complete = False
        self._state = IDLE if self._students else EMPTY

    def get_drawn_ids(self) -> set:
        with self._lock:
            return set(self._drawn_ids)

    def remaining_count(self) -> int:
        with self._lock:
            return self._remaining_locked()

    def _remaining_locked(self) -> int:
        if self._mode == MODE_NO_REPEAT:
            return sum(1 for s in self._students if s.id not in self._drawn_ids)
        return len(self._students)

    def get_state(self) -> str:
        with self._lock:
            return self._state

    def is_round_complete(self) -> bool:
        with self._lock:
            return self._round_complete

    # ---------- 抽样（唯一入口） ----------
    def draw(self, count: int) -> DrawResult:
        """原子生成结果并进入动画态。忙时抛 DrawBusyError（映射为 BUSY）。"""
        if not isinstance(count, int) or not 1 <= count <= 5:
            raise ValueError(f"抽取人数须为 1~5: {count!r}")
        with self._lock:
            if self._state in (ANIMATING, REVEALING) and not self._reveal_done:
                raise DrawBusyError("抽取进行中")

            if not self._students:
                raise ValueError("名单为空，请先录入学生")

            if self._round_complete:
                raise ValueError("本轮已完成，请先开启新一轮")

            if self._mode == MODE_NO_REPEAT:
                pool = [s for s in self._students if s.id not in self._drawn_ids]
            else:
                pool = list(self._students)

            n = min(count, len(pool))
            picked = self._sample_no_replacement(pool, n)

            self._draw_seq += 1
            result = DrawResult(draw_id=f"d{self._draw_seq}-{uuid.uuid4().hex[:8]}", students=picked)

            # 事务内决定目标并写入已抽集合（§6.1）
            self._drawn_ids.update(s.id for s in picked)
            self._active = result
            self._reveal_done = False
            self._round_complete = False
            self._state = ANIMATING

            # 不重复模式：抽尽即预示轮次完成（揭晓确认后进入 ROUND_COMPLETE）
            return result

    def _sample_no_replacement(self, pool: List[Student], n: int) -> List[Student]:
        """均匀不放回抽样（Fisher-Yates 取前 n）。"""
        pool = list(pool)
        for i in range(len(pool) - 1, 0, -1):
            j = self._rng.randrange(i + 1)
            pool[i], pool[j] = pool[j], pool[i]
        return pool[:n]

    # ---------- 揭晓完成（幂等） ----------
    def mark_reveal_complete(self, draw_id: str) -> bool:
        """常规动画完成或“立即揭晓”共用。同一 drawId 仅生效一次；过期 drawId 忽略。

        返回 True 表示本次调用生效。
        """
        with self._lock:
            if not self._active or self._active.draw_id != draw_id:
                return False  # 过期 drawId
            if self._reveal_done:
                return True  # 幂等：已生效过
            self._reveal_done = True

            if self._mode == MODE_NO_REPEAT and self._remaining_locked() == 0:
                self._round_complete = True
                self._state = ROUND_COMPLETE
            else:
                self._state = RESULT
            return True

    def get_active_result(self) -> Optional[DrawResult]:
        """最近一次锁定的事务（收起/恢复时直接展示，不重抽，§6.2）。"""
        with self._lock:
            return self._active

    def is_reveal_done(self) -> bool:
        with self._lock:
            return self._reveal_done

    # ---------- 快照 ----------
    def snapshot(self) -> Dict[str, Any]:
        """当前状态摘要，供前端初始化与恢复。"""
        with self._lock:
            active = self._active.to_dict() if self._active else None
            remaining = self._remaining_locked()
            return {
                "state": self._state,
                "mode": self._mode,
                "studentCount": len(self._students),
                "remaining": remaining,
                "roundComplete": self._round_complete,
                "activeResult": active,
                "revealDone": self._reveal_done,
                "serverTime": time.time(),
            }
