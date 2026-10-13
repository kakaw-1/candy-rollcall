"""导入导出测试（M3 门槛、§8、AT-14/26/27/34）。"""
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.models import Student, ValidationError
from app.storage import import_export as ie


def mk(n, prefix="学生"):
    return [Student.create(f"{prefix}{i:02d}") for i in range(1, n + 1)]


class TestCsv(unittest.TestCase):
    def test_csv_roundtrip_preserves_leading_zero(self):
        """AT-14：学号 08 导出再导入仍为 08（字符串）。"""
        students = [Student.create("张小明", "08"), Student.create("李小红", "")]
        data = ie.export_csv(students)
        rows = ie.read_csv_rows(data, "utf-8-sig")
        self.assertEqual(rows[0], ["序号", "姓名", "学号"])
        self.assertEqual(rows[1][2], "08")  # 前导零保留
        drafts = ie.rows_to_drafts(rows[1:], {"name": 1, "studentNo": 2})
        self.assertEqual(drafts[0]["studentNo"], "08")

    def test_csv_gb18030(self):
        """GB18030 编码可读，不把乱码写入库（§8.2）。"""
        students = mk(2)
        text = "姓名,学号\n王小虎,01\n"
        data = text.encode("gb18030")
        rows = ie.read_csv_rows(data, "gb18030")
        drafts = ie.rows_to_drafts(rows[1:], {"name": 0, "studentNo": 1})
        self.assertEqual(drafts[0]["name"], "王小虎")

    def test_csv_bad_encoding_raises(self):
        with self.assertRaises(ValidationError):
            ie.read_csv_rows(b"\xff\xfe\x00\x00bad", "utf-8-sig")


class TestXlsx(unittest.TestCase):
    def test_xlsx_roundtrip(self):
        """XLSX 学号列文本格式（@），数值学号 8 读回为 "8"。"""
        students = [Student.create("张小明", "08"), Student.create("李小红", "9")]
        data = ie.export_xlsx(students)
        # 写临时文件再读
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            f.write(data)
            path = f.name
        try:
            rows = ie.read_xlsx_rows(path)
            self.assertEqual(rows[0], ["序号", "姓名", "学号"])
            self.assertEqual(rows[1][2], "08")
            self.assertEqual(rows[2][2], "9")
        finally:
            os.unlink(path)


class TestPaste(unittest.TestCase):
    def test_paste_single_column(self):
        drafts = ie.parse_paste_text("张小明\n李小红\n")
        self.assertEqual(drafts, [{"name": "张小明", "studentNo": None},
                                  {"name": "李小红", "studentNo": None}])

    def test_paste_two_columns(self):
        drafts = ie.parse_paste_text("08\t张小明\n09,李小红\n10 王小虎\n")
        self.assertEqual(drafts[0], {"name": "张小明", "studentNo": "08"})
        self.assertEqual(drafts[1], {"name": "李小红", "studentNo": "09"})
        self.assertEqual(drafts[2], {"name": "王小虎", "studentNo": "10"})

    def test_paste_skips_empty_lines(self):
        drafts = ie.parse_paste_text("\n张小明\n\n\n")
        self.assertEqual(len(drafts), 1)


class TestPreview(unittest.TestCase):
    def test_duplicates_kinds(self):
        """§8.3 重复判定矩阵。"""
        existing = [Student.create("张小明", "08"), Student.create("李小红", "")]
        drafts = [
            {"name": "张小明", "studentNo": "08"},   # 同名同号 → 高度疑似
            {"name": "张小明", "studentNo": "09"},   # 同名不同号 → 同名提示
            {"name": "李小红", "studentNo": None},   # 同名双无号 → 人工确认
            {"name": "王小虎", "studentNo": "08"},   # 同学号不同名 → 冲突
            {"name": "赵新", "studentNo": "10"},     # 无问题
        ]
        p = ie.preview_import(drafts, existing)
        kinds = {i["key"]: i["kind"] for i in p["issues"]}
        self.assertEqual(kinds["new:0"], "duplicate_exact")
        self.assertEqual(kinds["new:1"], "same_name_diff_no")
        self.assertEqual(kinds["new:2"], "same_name_no_no")
        self.assertEqual(kinds["new:3"], "no_conflict")
        self.assertNotIn("new:4", kinds)
        self.assertEqual(p["validCount"], 5)

    def test_over_limit(self):
        """AT-12 后端部分：超过 100 人标记超额。"""
        drafts = [{"name": f"学生{i}", "studentNo": None} for i in range(101)]
        p = ie.preview_import(drafts, [])
        self.assertTrue(p["overLimit"])
        self.assertEqual(p["maxStudents"], 100)

    def test_empty_names_flagged(self):
        drafts = [{"name": "", "studentNo": None}, {"name": "张小明", "studentNo": None}]
        p = ie.preview_import(drafts, [])
        self.assertEqual(p["emptyNameRows"], [0])
        self.assertEqual(p["validCount"], 1)

    def test_internal_duplicates_within_new_list(self):
        """追加时新名单内部重复也要检查（§8.3）。"""
        drafts = [{"name": "张小明", "studentNo": "08"},
                  {"name": "张小明", "studentNo": "08"}]
        p = ie.preview_import(drafts, [])
        exact = [i for i in p["issues"] if i["kind"] == "duplicate_exact"]
        self.assertEqual(len(exact), 1)  # 第二条被报告


class TestBackup(unittest.TestCase):
    def test_backup_build_and_parse(self):
        students = [Student.create("张小明", "08")]
        settings = {"mode": "random", "drawCount": 3, "baseAnimationSeconds": 5}
        backup = ie.build_backup(students, settings)
        blob = json.dumps(backup, ensure_ascii=False).encode("utf-8")
        parsed_students, parsed_settings = ie.parse_backup(blob)
        self.assertEqual([s.to_dict() for s in parsed_students],
                         [s.to_dict() for s in students])
        self.assertEqual(parsed_settings["mode"], "random")

    def test_corrupt_backup_rejected(self):
        """AT-27 后端部分：损坏备份被拒绝，不覆盖数据。"""
        for bad in (b"", b"not json", b"{}", b'{"students": "x"}'):
            with self.assertRaises(ValidationError):
                ie.parse_backup(bad)

    def test_duplicate_ids_in_backup_rejected(self):
        s = Student.create("张小明", "08")
        backup = {"type": "CandyRollCall-backup", "schemaVersion": 2,
                  "students": [s.to_dict(), dict(s.to_dict())], "settings": {}}
        with self.assertRaises(ValidationError):
            ie.parse_backup(json.dumps(backup).encode())

    def test_over_100_in_backup_rejected(self):
        students = [Student.create(f"学生{i}") for i in range(101)]
        backup = {"type": "CandyRollCall-backup", "schemaVersion": 2,
                  "students": [s.to_dict() for s in students], "settings": {}}
        with self.assertRaises(ValidationError):
            ie.parse_backup(json.dumps(backup).encode())

    def test_backup_excludes_round_state(self):
        """完整备份不含本轮已抽状态（§5.4）。"""
        students = mk(3)
        backup = ie.build_backup(students, {})
        self.assertNotIn("drawnIds", json.dumps(backup))
        self.assertNotIn("drawn", json.dumps(backup).lower().replace("drawncount", ""))


if __name__ == "__main__":
    unittest.main()
