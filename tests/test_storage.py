"""存储与会话状态测试（M1 门槛、§5、AT-21/22/27/28/29）。

使用 CANDY_ROLLCALL_HOME 环境变量隔离临时数据目录。
"""
import json
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmpdir = tempfile.mkdtemp(prefix="candy-test-")
os.environ["CANDY_ROLLCALL_HOME"] = _tmpdir

from app.core.application_state import ApplicationState
from app.core.models import Student, ValidationError
from app.storage.json_store import DataCorruptError, JsonStore


def make_draft(n, prefix="学生"):
    return [{"name": f"{prefix}{i:02d}", "studentNo": None} for i in range(1, n + 1)]


class TestJsonStore(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="candy-store-")
        self.path = os.path.join(self.dir, "data.json")
        self.store = JsonStore(self.path)
        # 重置单例，让 ApplicationState 用当前测试目录
        ApplicationState._instance = None

    def test_atomic_save_load(self):
        doc = {"schemaVersion": 2, "students": [{"id": "a", "name": "张小明", "studentNo": "08"}],
               "settings": {"mode": "no_repeat"}}
        self.store.save(doc)
        loaded = self.store.load()
        self.assertEqual(loaded["students"][0]["studentNo"], "08")

    def test_no_tmp_files_left(self):
        self.store.save({"schemaVersion": 2, "students": [], "settings": {}})
        leftovers = [f for f in os.listdir(self.dir) if f.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_corrupt_main_recovers_from_bak(self):
        good = {"schemaVersion": 2, "students": [], "settings": {"mode": "random"}}
        self.store.save(good)
        # 破坏主文件
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{corrupt!!!")
        loaded = self.store.load()
        self.assertEqual(loaded["settings"]["mode"], "random")
        # 恢复后主文件可用
        self.assertEqual(self.store.load()["schemaVersion"], 2)

    def test_both_corrupt_raises_not_overwrite(self):
        """AT-27：两者均坏时抛 DataCorruptError，不悄悄覆盖。"""
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{bad")
        with open(self.store.bak_path, "w", encoding="utf-8") as f:
            f.write("{also bad")
        with self.assertRaises(DataCorruptError):
            self.store.load()
        # 原文件未被覆盖
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "{bad")

    def test_missing_file_returns_none(self):
        self.assertIsNone(self.store.load())


class TestApplicationState(unittest.TestCase):
    def setUp(self):
        # 每个测试独立的临时目录 + 新单例
        self.dir = tempfile.mkdtemp(prefix="candy-app-")
        os.environ["CANDY_ROLLCALL_HOME"] = self.dir
        ApplicationState._instance = None
        self.state = ApplicationState.instance()

    def tearDown(self):
        ApplicationState._instance = None

    # ---------- 提交与校验 ----------
    def test_commit_replace_resets_round(self):
        """AT-22 后端部分：提交名单重置本轮。"""
        snap = self.state.commit_students(make_draft(5), "replace", None, True)
        self.assertEqual(snap["draw"]["studentCount"], 5)
        r = self.state.draw()
        self.assertTrue(r["ok"])
        self.state.mark_reveal_complete(r["drawId"])
        # 再次提交 → 轮次重置
        snap2 = self.state.commit_students(make_draft(6), "replace", None, True)
        self.assertEqual(snap2["draw"]["remaining"], 6)
        self.assertIsNone(snap2["draw"]["activeResult"])

    def test_commit_requires_confirmation(self):
        """§10：无确认标记不提交。"""
        r = self.state.commit_students(make_draft(3), "replace", None, False)
        self.assertFalse(r["ok"])

    def test_commit_over_100_rejected(self):
        """AT-12：第 101 人明确拒绝，不截断。"""
        r = self.state.commit_students(make_draft(101), "replace", None, True)
        self.assertFalse(r["ok"])
        self.assertIn("101", r["message"])

    def test_append_over_100_rejected(self):
        self.state.commit_students(make_draft(95), "replace", None, True)
        r = self.state.commit_students(make_draft(10), "append", None, True)
        self.assertFalse(r["ok"])
        self.assertIn("105", r["message"])

    def test_skip_decision_respected(self):
        """教师选择“跳过”的疑似重复条目不入库（§8.3 不自动合并）。"""
        drafts = [{"name": "张小明", "studentNo": "08"},
                  {"name": "张小明", "studentNo": "08"}]
        snap = self.state.commit_students(drafts, "replace", {"new:1": "skip"}, True)
        self.assertEqual(len(snap["students"]), 1)

    def test_empty_name_rejected(self):
        r = self.state.commit_students([{"name": "  ", "studentNo": None}], "replace", None, True)
        self.assertFalse(r["ok"])

    # ---------- 学号字符串（AT-14） ----------
    def test_student_no_kept_as_string(self):
        snap = self.state.commit_students([{"name": "张小明", "studentNo": "08"}],
                                          "replace", None, True)
        self.assertEqual(snap["students"][0]["studentNo"], "08")
        # 重新加载后仍是字符串
        state2 = ApplicationState.instance()
        state2.startup()
        students = state2.get_students()
        self.assertEqual(students[0].studentNo, "08")

    # ---------- 持久化与重启（AT-29） ----------
    def test_restart_resets_round_keeps_students(self):
        self.state.commit_students(make_draft(8), "replace", None, True)
        r = self.state.draw()
        self.state.mark_reveal_complete(r["drawId"])
        # 模拟重启：新单例加载
        ApplicationState._instance = None
        state2 = ApplicationState.instance()
        snap = state2.startup()
        self.assertEqual(len(snap["students"]), 8)
        self.assertEqual(snap["draw"]["state"], "IDLE")
        self.assertEqual(snap["draw"]["remaining"], 8, "重启清空 drawn_ids")

    def test_settings_persisted(self):
        self.state.commit_students(make_draft(3), "replace", None, True)
        self.state.set_preference("drawCount", 3)
        self.state.set_preference("baseAnimationSeconds", 6)
        ApplicationState._instance = None
        snap = ApplicationState.instance().startup()
        self.assertEqual(snap["settings"]["drawCount"], 3)
        self.assertEqual(snap["settings"]["baseAnimationSeconds"], 6)

    def test_corrupt_data_starts_clean_not_crash(self):
        self.state.commit_students(make_draft(3), "replace", None, True)
        # 破坏主文件和 .bak
        for name in ("data.json", "data.json.bak"):
            p = os.path.join(self.dir, name)
            if os.path.exists(p):
                with open(p, "w", encoding="utf-8") as f:
                    f.write("{broken")
        ApplicationState._instance = None
        snap = ApplicationState.instance().startup()
        # 不崩溃：从干净状态开始（教师用外部备份恢复）
        self.assertEqual(snap["students"], [])

    # ---------- 设置校验 ----------
    def test_preference_whitelist(self):
        r = self.state.set_preference("drawCount", 3)
        self.assertTrue(r["ok"])
        r = self.state.set_preference("evilKey", "x")
        self.assertFalse(r["ok"])
        r = self.state.set_preference("drawCount", 9)
        self.assertFalse(r["ok"], "drawCount 须为 1~5")
        r = self.state.set_preference("baseAnimationSeconds", 20)
        self.assertFalse(r["ok"], "动画时长须为 2~8")

    # ---------- 模式切换（AT-19/20） ----------
    def test_change_mode_confirmation_flow(self):
        self.state.commit_students(make_draft(10), "replace", None, True)
        r = self.state.change_mode("random")   # 未确认
        self.assertTrue(r.get("needsConfirm"))
        self.assertEqual(self.state.get_settings()["mode"], "no_repeat")
        r = self.state.change_mode("random", confirmed=True)
        self.assertTrue(r["ok"])
        self.assertEqual(self.state.get_settings()["mode"], "random")
        self.assertEqual(r["state"]["remaining"], 10, "确认切换后重置本轮")

    def test_draw_count_change_does_not_reset(self):
        """修改人数保存设置但不重置（§6.3）。"""
        self.state.commit_students(make_draft(10), "replace", None, True)
        r = self.state.draw()
        self.state.mark_reveal_complete(r["drawId"])
        self.state.set_preference("drawCount", 3)
        self.assertEqual(self.state.get_revision(), 2)
        self.assertEqual(len(self.state.full_snapshot()["draw"]["activeResult"]["students"]), 1)

    # ---------- revision 防过期草稿 ----------
    def test_stale_revision_rejected(self):
        self.state.commit_students(make_draft(3), "replace", None, True)
        rev = self.state.get_revision()
        self.state.commit_students(make_draft(4), "replace", None, True)  # 其他窗口改了
        r = self.state.commit_students(make_draft(5), "replace", None, True, base_revision=rev)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "STALE_REVISION")

    # ---------- 抽取接口（AT-18） ----------
    def test_draw_busy_via_state(self):
        self.state.commit_students(make_draft(10), "replace", None, True)
        r1 = self.state.draw()
        self.assertTrue(r1["ok"])
        r2 = self.state.draw()
        self.assertFalse(r2["ok"])
        self.assertEqual(r2["error"], "BUSY")

    # ---------- 恢复备份（AT-28） ----------
    def test_restore_flow(self):
        from app.storage.import_export import build_backup, parse_backup
        students = [Student.create("备份学生", "01")]
        backup = build_backup(students, {"mode": "random", "drawCount": 2})
        blob = json.dumps(backup, ensure_ascii=False).encode("utf-8")
        parsed_students, parsed_settings = parse_backup(blob)
        self.state.commit_students(make_draft(5), "replace", None, True)
        snap = self.state.commit_students(
            [s.to_dict() for s in parsed_students], "replace", None, True)
        self.state.set_settings_raw(parsed_settings)
        snap = self.state.full_snapshot()
        self.assertEqual(len(snap["students"]), 1)
        self.assertEqual(snap["students"][0]["name"], "备份学生")
        self.assertEqual(snap["settings"]["mode"], "random")


class TestConcurrency(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="candy-conc-")
        os.environ["CANDY_ROLLCALL_HOME"] = self.dir
        ApplicationState._instance = None
        self.state = ApplicationState.instance()

    def tearDown(self):
        ApplicationState._instance = None

    def test_concurrent_draws_single_transaction(self):
        """并发抽取：只有一个成功，其余 BUSY（AT-18）。"""
        self.state.commit_students(make_draft(50), "replace", None, True)
        results = []
        lock = threading.Lock()

        def worker():
            r = self.state.draw(5)
            with lock:
                results.append(r)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        ok = [r for r in results if r.get("ok")]
        self.assertEqual(len(ok), 1, "只应有一个抽样事务")
        self.assertEqual(len(results) - len(ok), 7, "其余全部 BUSY")

    def test_concurrent_commits_consistent(self):
        """并发提交：revision 防护下数据一致。"""
        self.state.commit_students(make_draft(3), "replace", None, True)
        errors = []

        def worker(i):
            try:
                self.state.commit_students(make_draft(3 + i), "replace", None, True)
            except ValidationError as e:
                errors.append(str(e))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 最终状态可加载且学生数在合法范围
        snap = self.state.full_snapshot()
        self.assertLessEqual(len(snap["students"]), 100)


if __name__ == "__main__":
    unittest.main()
