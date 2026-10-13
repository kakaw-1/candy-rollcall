"""数据模型与校验。

学生记录与设置的数据结构，对应文档 §5.1。
- id: UUID 字符串，抽样按它去重，修改姓名不改变 ID。
- name: 非空字符串，去除首尾空白。
- studentNo: 可选字符串，保留前导零（如 "08"），绝不转整数。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

MAX_STUDENTS = 100
MAX_NAME_LEN = 50
MAX_STUDENT_NO_LEN = 20

# 设置键白名单（§10 set_preference）
SETTING_KEYS = {
    "mode",                 # random | no_repeat
    "drawCount",            # 1~5
    "baseAnimationSeconds",  # 2~8
    "edgeSnap",             # bool
    "startWithWindows",     # bool
    "lastOrbPosition",      # {"x": int, "y": int}
    "expandedSize",         # {"width": int, "height": int}
}

VALID_MODES = ("random", "no_repeat")


class ValidationError(ValueError):
    """名单或设置校验失败。"""


@dataclass
class Student:
    id: str
    name: str
    studentNo: Optional[str] = None

    @staticmethod
    def create(name: str, student_no: Optional[str] = None) -> "Student":
        name = validate_name(name)
        no = validate_student_no(student_no)
        return Student(id=str(uuid.uuid4()), name=name, studentNo=no or None)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "studentNo": self.studentNo}

    @staticmethod
    def from_dict(raw: Any) -> "Student":
        if not isinstance(raw, dict):
            raise ValidationError(f"学生记录必须是对象: {raw!r}")
        sid = raw.get("id")
        if not isinstance(sid, str) or not sid.strip():
            raise ValidationError(f"学生缺少有效 id: {raw!r}")
        name = validate_name(raw.get("name"))
        no = validate_student_no(raw.get("studentNo"))
        return Student(id=sid.strip(), name=name, studentNo=no or None)


def validate_name(name: Any) -> str:
    if not isinstance(name, str):
        raise ValidationError(f"姓名必须是字符串: {name!r}")
    name = name.strip()
    if not name:
        raise ValidationError("姓名不能为空")
    if len(name) > MAX_NAME_LEN:
        raise ValidationError(f"姓名过长（最多 {MAX_NAME_LEN} 字）: {name}")
    return name


def validate_student_no(no: Any) -> str:
    """学号强制按字符串保存；None/空串归一化为空串。"""
    if no is None:
        return ""
    if isinstance(no, (int, float)) and not isinstance(no, bool):
        # Excel 可能把 08 存成数值 8；按文档 §8.2 不凭空猜测前导零，
        # 直接按整数字面量转字符串（8 -> "8"）。
        if isinstance(no, float):
            if not no.is_integer():
                raise ValidationError(f"学号必须是整数或字符串: {no!r}")
            no = int(no)
        no = str(no)
    if not isinstance(no, str):
        raise ValidationError(f"学号必须是字符串: {no!r}")
    no = no.strip()
    if len(no) > MAX_STUDENT_NO_LEN:
        raise ValidationError(f"学号过长（最多 {MAX_STUDENT_NO_LEN} 字符）: {no}")
    return no


def validate_settings(raw: Any) -> dict:
    """校验并规范化 settings 字典，非法值回退默认。"""
    defaults = default_settings()
    if not isinstance(raw, dict):
        return defaults
    out = dict(defaults)
    for key, value in raw.items():
        if key not in SETTING_KEYS:
            continue
        try:
            out[key] = _coerce_setting(key, value, out[key])
        except (ValidationError, TypeError, ValueError):
            continue  # 非法值静默回退默认，保证损坏数据可加载
    return out


def default_settings() -> dict:
    return {
        "mode": "no_repeat",
        "drawCount": 1,
        "baseAnimationSeconds": 4,
        "edgeSnap": True,
        "startWithWindows": False,
        "lastOrbPosition": {"x": 0, "y": 0},
        "expandedSize": {"width": 420, "height": 340},
    }


def _coerce_setting(key: str, value: Any, fallback: Any) -> Any:
    if key == "mode":
        if value not in VALID_MODES:
            raise ValidationError(f"非法模式: {value!r}")
        return value
    if key == "drawCount":
        n = int(value)
        if not 1 <= n <= 5:
            raise ValidationError(f"抽取人数须为 1~5: {n}")
        return n
    if key == "baseAnimationSeconds":
        n = int(value)
        if not 2 <= n <= 8:
            raise ValidationError(f"基础动画时长须为 2~8 秒: {n}")
        return n
    if key in ("edgeSnap", "startWithWindows"):
        return bool(value)
    if key == "lastOrbPosition":
        if isinstance(value, dict) and isinstance(value.get("x"), int) and isinstance(value.get("y"), int):
            return {"x": value["x"], "y": value["y"]}
        raise ValidationError("非法坐标")
    if key == "expandedSize":
        if isinstance(value, dict):
            w, h = value.get("width"), value.get("height")
            if isinstance(w, int) and isinstance(h, int) and 200 <= w <= 2000 and 150 <= h <= 1500:
                return {"width": w, "height": h}
        raise ValidationError("非法窗口尺寸")
    return fallback


def display_label(student: Student) -> str:
    """卡片显示格式：同名可用学号区分（§5.1），“姓名 · 08号”。"""
    if student.studentNo:
        return f"{student.name} · {student.studentNo}号"
    return student.name


def new_id() -> str:
    return str(uuid.uuid4())
