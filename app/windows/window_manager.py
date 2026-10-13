"""WebView 窗口生命周期编排（§3.2、§4.3）。

- 点名主窗长期保留（可隐藏），管理窗按需创建、关闭即销毁（V2.1）。
- 从点名窗进管理：先记录可见状态、隐藏点名窗，再创建管理窗；
  关闭管理窗销毁其 WebView 并恢复原状态；不退出主进程（AT-39/40/41）。
- 管理请求幂等：已打开时只激活现有实例。
"""
from __future__ import annotations

import threading
from enum import Enum
from typing import Optional

import webview


class WindowPhase(Enum):
    ORB_ONLY = "orb_only"          # 仅悬浮球
    ROLL_VISIBLE = "roll_visible"  # 点名窗可见
    ROLL_HIDDEN_MANAGE = "roll_hidden_manage"  # 点名窗隐藏、管理窗打开
    ROLL_HIDDEN = "roll_hidden"    # 点名窗隐藏（动画外临时隐藏）


class WindowManager:
    """管理 pywebview 窗口集合与相位。pywebview API 须在 GUI 线程外以
    webview.windows 上的方法调用；这里封装线程安全的窗口操作入口。"""

    def __init__(self):
        self._lock = threading.RLock()
        self.roll_window: Optional[webview.Window] = None
        self.manage_window: Optional[webview.Window] = None
        self.phase: WindowPhase = WindowPhase.ORB_ONLY
        # 进入管理前的可见状态，关闭管理后恢复
        self._pre_manage_phase: WindowPhase = WindowPhase.ORB_ONLY
        self._creating_manage = threading.Lock()

    # ---------- 点名主窗 ----------
    def register_roll_window(self, window: webview.Window) -> None:
        with self._lock:
            self.roll_window = window

    def show_roll(self) -> None:
        with self._lock:
            win = self.roll_window
        if not win:
            return
        try:
            win.show()
            win.restore()
        except Exception:
            pass
        with self._lock:
            self.phase = WindowPhase.ROLL_VISIBLE

    def hide_roll(self) -> None:
        with self._lock:
            win = self.roll_window
        if not win:
            return
        try:
            win.hide()
        except Exception:
            pass
        with self._lock:
            if self.phase == WindowPhase.ROLL_VISIBLE:
                self.phase = WindowPhase.ROLL_HIDDEN

    def toggle_fullscreen(self) -> None:
        with self._lock:
            win = self.roll_window
        if not win:
            return
        try:
            win.toggle_fullscreen()
        except Exception:
            pass

    # ---------- 管理窗（按需创建、幂等、关闭即销毁） ----------
    def open_manage(self, create_window_fn) -> None:
        """进入管理：点名窗隐藏；管理窗已存在则只激活（不重复创建）。

        create_window_fn: 由 main.py 注入的工厂，实际创建 pywebview 管理窗。
        """
        with self._creating_manage:
            with self._lock:
                if self.manage_window is not None:
                    # 幂等：激活现有实例
                    try:
                        self.manage_window.show()
                        self.manage_window.restore()
                    except Exception:
                        pass
                    return
                # 记录进入前状态并隐藏点名窗（§3.2 生命周期）
                with self._lock:
                    if self.phase == WindowPhase.ROLL_VISIBLE:
                        self._pre_manage_phase = WindowPhase.ROLL_VISIBLE
                    else:
                        self._pre_manage_phase = WindowPhase.ORB_ONLY
                self.hide_roll()
                self.phase = WindowPhase.ROLL_HIDDEN_MANAGE
                # 创建管理窗（pywebview 允许运行中 create_window）
                manage_win = create_window_fn()
                with self._lock:
                    self.manage_window = manage_win

    def close_manage(self) -> None:
        """关闭管理窗：销毁实例并恢复进入前状态；不影响主进程（AT-39/40）。"""
        with self._creating_manage:
            with self._lock:
                win = self.manage_window
                pre = self._pre_manage_phase
                self.manage_window = None
            if win is not None:
                try:
                    win.destroy()  # 销毁 WebView，窗口生命周期结束
                except Exception:
                    pass
            with self._lock:
                self.phase = WindowPhase.ORB_ONLY
            # 恢复进入前状态
            if pre == WindowPhase.ROLL_VISIBLE:
                self.show_roll()

    @property
    def manage_open(self) -> bool:
        with self._lock:
            return self.manage_window is not None

    # ---------- 退出 ----------
    def shutdown(self) -> None:
        """统一退出：销毁全部窗口；pywebview 主循环随之结束。"""
        with self._lock:
            manage = self.manage_window
            roll = self.roll_window
        if manage is not None:
            try:
                manage.destroy()
            except Exception:
                pass
        if roll is not None:
            try:
                roll.destroy()
            except Exception:
                pass
