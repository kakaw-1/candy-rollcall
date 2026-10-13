"""主屏工作区与自动吸附（§4.1、§4.2）。

仅在 Windows 上有实际意义；非 Windows 平台提供纯计算逻辑供测试。
"""
from __future__ import annotations

from typing import NamedTuple, Optional

RECT = NamedTuple("RECT", [("left", int), ("top", int), ("right", int), ("bottom", int)])


def clamp_to_workarea(x: int, y: int, size: int, work: RECT) -> tuple:
    """把球坐标夹紧到工作区可视范围内（分辨率/任务栏变化时）。"""
    x = max(work.left, min(x, work.right - size))
    y = max(work.top, min(y, work.bottom - size))
    return x, y


def nearest_snap_edge(x: int, y: int, size: int, work: RECT) -> tuple:
    """返回吸附后的坐标：吸附所在主屏工作区最近的左/右侧（§4.2）。"""
    x, y = clamp_to_workarea(x, y, size, work)
    dist_left = abs(x - work.left)
    dist_right = abs((work.right - size) - x)
    if dist_left <= dist_right:
        return work.left, y
    return work.right - size, y


class Win32MonitorMixin:
    """Windows 实机部分：取主屏工作区。非 win32 环境抛 RuntimeError。"""

    @staticmethod
    def get_primary_workarea() -> RECT:
        import ctypes
        from ctypes import wintypes

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        user32 = ctypes.windll.user32
        SPI_GETWORKAREA = 0x0048
        rc = wintypes.RECT()
        # 主屏工作区：SystemParametersInfo(SPI_GETWORKAREA) 即主显示器工作区
        if user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rc), 0):
            return RECT(rc.left, rc.top, rc.right, rc.bottom)
        # 兜底：屏幕尺寸
        w = user32.GetSystemMetrics(0)   # SM_CXSCREEN
        h = user32.GetSystemMetrics(1)   # SM_CYSCREEN
        return RECT(0, 0, w, h)
