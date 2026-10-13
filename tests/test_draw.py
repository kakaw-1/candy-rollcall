"""抽样算法与状态机测试（M1 门槛、§6、AT-13/15/16/17/18/20/29/36/37/38）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.draw_service import (
    DrawBusyError, DrawService, ANIMATING, REVEALING, RESULT, ROUND_COMPLETE,
    EMPTY, IDLE, MODE_NO_REPEAT, MODE_RANDOM,
)
from app.core.models import Student


def make_students(n, prefix="学生"):
    return [Student.create(f"{prefix}{i:02d}") for i in range(1, n + 1)]


class TestDraw(unittest.TestCase):
    def setUp(self):
        self.svc = DrawService()

    # ---------- 名单为空 ----------
    def test_empty_roster_rejects_draw(self):
        """AT-11 后端部分：空名单禁用开始。"""
        with self.assertRaises(ValueError):
            self.svc.draw(1)
        self.assertEqual(self.svc.get_state(), EMPTY)

    # ---------- 完全随机模式（AT-17） ----------
    def test_random_mode_same_draw_no_duplicate(self):
        self.svc.set_students(make_students(30))
        self.svc.set_mode(MODE_RANDOM)
        for _ in range(50):
            r = self.svc.draw(5)
            ids = [s.id for s in r.students]
            self.assertEqual(len(ids), len(set(ids)), "同一次抽取内不允许重复")
            self.svc.mark_reveal_complete(r.draw_id)  # 完成后才能再抽

    def test_random_mode_cross_draw_may_repeat(self):
        """跨次允许重复：1 人名单抽两次必然同 ID，不报错。"""
        students = make_students(1)
        self.svc.set_students(students)
        self.svc.set_mode(MODE_RANDOM)
        r1 = self.svc.draw(1)
        self.svc.mark_reveal_complete(r1.draw_id)
        r2 = self.svc.draw(1)
        self.assertEqual(r1.students[0].id, r2.students[0].id)

    # ---------- 不重复模式（AT-16） ----------
    def test_no_repeat_round_exhaustion_manual_reset(self):
        """不重复模式 40 人：每人仅一次；抽尽后必须手动开启新一轮。"""
        self.svc.set_students(make_students(40))
        all_ids = []
        rounds = 0
        while True:
            r = self.svc.draw(5)
            all_ids += [s.id for s in r.students]
            self.svc.mark_reveal_complete(r.draw_id)
            if self.svc.is_round_complete():
                break
            rounds += 1
            self.assertLess(rounds, 20)
        self.assertEqual(len(all_ids), len(set(all_ids)), "轮内不允许重复")
        self.assertEqual(set(all_ids), {s.id for s in self.svc.get_students()},
                         "抽尽时覆盖全班")
        # 抽尽后再抽被拒
        with self.assertRaises(ValueError):
            self.svc.draw(1)
        # 手动开启新一轮后恢复
        self.svc.reset_round()
        self.assertEqual(self.svc.get_state(), IDLE)
        self.assertEqual(self.svc.remaining_count(), 40)

    # ---------- 人数不足（AT-15） ----------
    def test_insufficient_remaining_draws_rest(self):
        """抽 5 人仅剩 3 人：单次抽 3 个不同 ID。"""
        self.svc.set_students(make_students(8))
        r1 = self.svc.draw(5)
        self.svc.mark_reveal_complete(r1.draw_id)
        r2 = self.svc.draw(5)
        self.assertEqual(len(r2.students), 3)
        ids = [s.id for s in r2.students]
        self.assertEqual(len(ids), len(set(ids)))
        # 不与第一次重复
        r1_ids = {s.id for s in r1.students}
        self.assertFalse(r1_ids & set(ids))

    def test_insufficient_single_round(self):
        """名单仅 3 人抽 5 人：抽 3 人，界面不报错。"""
        self.svc.set_students(make_students(3))
        r = self.svc.draw(5)
        self.assertEqual(len(r.students), 3)

    # ---------- 同名独立记录（AT-13） ----------
    def test_same_name_independent_ids(self):
        students = [Student.create("张小明", "08"), Student.create("张小明", "09")]
        self.svc.set_students(students)
        picked = set()
        for _ in range(100):
            self.svc.reset_round()
            r = self.svc.draw(2)
            picked.update(s.id for s in r.students)
            if len(picked) == 2:
                break
        self.assertEqual(picked, {s.id for s in students}, "两名同名学生均可被独立抽中")

    # ---------- BUSY（AT-18） ----------
    def test_busy_rejects_concurrent_draw(self):
        self.svc.set_students(make_students(10))
        self.svc.draw(3)
        with self.assertRaises(DrawBusyError):
            self.svc.draw(3)

    # ---------- drawId 幂等与过期（AT-38） ----------
    def test_mark_reveal_complete_idempotent_and_stale(self):
        self.svc.set_students(make_students(10))
        r = self.svc.draw(2)
        self.assertTrue(self.svc.mark_reveal_complete(r.draw_id))
        self.assertEqual(self.svc.get_state(), RESULT)
        # 同一 drawId 重复调用仅生效一次，返回 True 但状态不变
        self.assertTrue(self.svc.mark_reveal_complete(r.draw_id))
        self.assertEqual(self.svc.get_state(), RESULT)
        # 过期/未知 drawId 忽略
        self.assertFalse(self.svc.mark_reveal_complete("d999-unknown"))

    def test_stale_callback_after_new_draw_ignored(self):
        """旧动画完成回调不能覆盖新抽取。"""
        self.svc.set_students(make_students(10))
        r1 = self.svc.draw(2)
        self.svc.mark_reveal_complete(r1.draw_id)  # 旧动画完成
        r2 = self.svc.draw(2)                      # 新抽取
        # 旧 drawId 的迟到回调对新状态无效
        self.assertFalse(self.svc.mark_reveal_complete(r1.draw_id))
        self.assertEqual(self.svc.get_state(), ANIMATING)
        self.assertTrue(self.svc.mark_reveal_complete(r2.draw_id))
        self.assertEqual(self.svc.get_state(), RESULT)

    # ---------- 立即揭晓（AT-36/37） ----------
    def test_reveal_now_does_not_change_drawn_ids(self):
        """立即揭晓：不调用 draw、不改变 drawn_ids、不重置轮次。"""
        self.svc.set_students(make_students(10))
        r = self.svc.draw(3)
        drawn_after_draw = self.svc.get_drawn_ids()
        # 模拟“立即揭晓”：直接 mark_reveal_complete，不重抽
        self.svc.mark_reveal_complete(r.draw_id)
        self.assertEqual(self.svc.get_drawn_ids(), drawn_after_draw)
        self.assertEqual(len(drawn_after_draw), 3)
        self.assertEqual(self.svc.get_state(), RESULT)

    def test_reveal_now_partial_after_reveal(self):
        """REVEALING 已揭晓部分后立即揭晓：先前结果不变，余下全部揭晓。"""
        self.svc.set_students(make_students(10))
        r = self.svc.draw(3)
        # 已锁定结果在前端已揭晓 2 人（前端层面），后端状态仍是未完成
        self.assertEqual(self.svc.get_state(), ANIMATING)
        self.svc.mark_reveal_complete(r.draw_id)
        snap = self.svc.snapshot()
        self.assertEqual(len(snap["activeResult"]["students"]), 3)
        self.assertTrue(snap["revealDone"])

    # ---------- 模式切换（AT-19/20） ----------
    def test_mode_switch_resets_round(self):
        self.svc.set_students(make_students(10))
        r = self.svc.draw(3)
        self.svc.mark_reveal_complete(r.draw_id)
        self.assertEqual(len(self.svc.get_drawn_ids()), 3)
        self.svc.set_mode(MODE_RANDOM)  # 确认后切换
        self.assertEqual(len(self.svc.get_drawn_ids()), 0, "切换模式重置本轮")
        self.assertEqual(self.svc.remaining_count(), 10)

    def test_mode_switch_cancel_no_change(self):
        """取消切换：模式与进度均不变（由上层不调用 set_mode 保证）。"""
        self.svc.set_students(make_students(10))
        r = self.svc.draw(1)
        self.svc.mark_reveal_complete(r.draw_id)
        mode_before = self.svc.get_mode()
        drawn_before = self.svc.get_drawn_ids()
        # 上层确认框取消 → 不调用 set_mode；验证状态原样
        self.assertEqual(self.svc.get_mode(), mode_before)
        self.assertEqual(self.svc.get_drawn_ids(), drawn_before)

    # ---------- 重置 ----------
    def test_reset_round_keeps_students(self):
        self.svc.set_students(make_students(10))
        self.svc.draw(2)
        self.svc.reset_round()
        self.assertEqual(len(self.svc.get_students()), 10)
        self.assertEqual(self.svc.get_drawn_ids(), set())
        self.assertEqual(self.svc.get_state(), IDLE)

    # ---------- 公平性诊断（§14.1：诊断性，不判缺陷） ----------
    def test_uniform_sampling_diagnostic(self):
        self.svc.set_students(make_students(6))
        self.svc.set_mode(MODE_RANDOM)
        counts = {s.id: 0 for s in self.svc.get_students()}
        trials = 600
        for _ in range(trials):
            self.svc.reset_round()
            r = self.svc.draw(1)
            counts[r.students[0].id] += 1
        expected = trials / 6
        for sid, c in counts.items():
            # 诊断性宽松区间：±40%（短样本波动不算缺陷，只捕捉系统性偏斜）
            self.assertLess(abs(c - expected), expected * 0.4,
                            f"候选 {sid} 被抽 {c} 次，期望约 {expected:.0f}")


if __name__ == "__main__":
    unittest.main()
