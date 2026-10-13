"""Win32 原生圆形悬浮球（§4）。

独立 GUI 线程：创建 WS_POPUP 圆形 HWND（CreateEllipticRgn + SetWindowRgn），
GDI 绘制糖果渐变球，置顶、拖动、边缘吸附、右键菜单。
线程模型（§3.2）：本线程拥有消息循环；跨线程指令经 PostMessage(WM_APP+n) 投递，
不直接跨线程操作 HWND。
"""
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from typing import Callable, Optional

from .dpi_and_snap import RECT, clamp_to_workarea, nearest_snap_edge

if ctypes.sizeof(ctypes.c_void_p) != ctypes.sizeof(ctypes.c_void_p):  # pragma: no cover
    raise ImportError("orb_win32 仅支持 Windows")

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

# ---- 常量 ----
WM_APP = 0x8000
WM_APP_SHOW = WM_APP + 1        # 显示球
WM_APP_HIDE = WM_APP + 2        # 隐藏球
WM_APP_SNAP = WM_APP + 3        # 执行吸附
WM_APP_QUIT = WM_APP + 10       # 退出消息循环
WM_APP_CLOSE_MENUS = WM_APP + 11

WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000

WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_MOUSEMOVE = 0x0200
WM_PAINT = 0x000F
WM_ERASEBKGND = 0x0014
WM_DESTROY = 0x0002
WM_CREATE = 0x0001
WM_COMMAND = 0x0111

HTCAPTION = 2
SRCCOPY = 0x00CC0020

CS_HREDRAW = 0x0002
CS_VREDRAW = 0x0001
COLOR_WINDOW = 5

IDI_APPLICATION = 32512
MF_STRING = 0x0000
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100

MENU_EXPAND = 1001
MENU_MANAGE = 1002
MENU_QUIT = 1003

# 悬浮球尺寸（§4.2 建议 64~72 DIP）
ORB_SIZE = 68


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND, wintypes.UINT,
                                           wintypes.WPARAM, wintypes.LPARAM)),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", wintypes.HDC),
        ("fErase", wintypes.BOOL),
        ("rcPaint", wintypes.RECT),
        ("fRestore", wintypes.BOOL),
        ("fIncUpdate", wintypes.BOOL),
        ("rgbReserved", ctypes.c_byte * 32),
    ]


user32.DefWindowProcW.restype = ctypes.c_longlong
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.CreateWindowExW.restype = wintypes.HWND
user32.TrackPopupMenu.restype = wintypes.BOOL
gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
gdi32.CreateEllipticRgn.restype = wintypes.HRGN


class OrbWin32:
    """真圆形悬浮球。create() 必须在其专属 GUI 线程调用。"""

    def __init__(self, on_click: Callable[[], None],
                 on_manage: Callable[[], None],
                 on_quit: Callable[[], None],
                 size: int = ORB_SIZE,
                 snap_enabled: bool = True):
        self.on_click = on_click
        self.on_manage = on_manage
        self.on_quit = on_quit
        self.size = size
        self.snap_enabled = snap_enabled

        self.hwnd: Optional[int] = None
        self._hbr_core = None
        self._hbr_ring = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._quit_sent = False

        # 拖动状态（仅 GUI 线程访问）
        self._dragging = False
        self._drag_moved = False
        self._drag_off_x = 0
        self._drag_off_y = 0
        self._last_pos = (0, 0)

    # ---------- 线程生命周期 ----------
    def start(self, initial_pos: tuple) -> None:
        """启动 GUI 线程；initial_pos 为屏幕坐标 (x, y)。"""
        self._thread = threading.Thread(target=self._thread_main, args=(initial_pos,),
                                        name="OrbWin32Thread", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5.0)

    def _thread_main(self, initial_pos: tuple) -> None:
        self._create_window(initial_pos)
        self._message_loop()
        self._cleanup()

    # ---------- 窗口创建 ----------
    def _create_window(self, pos: tuple) -> None:
        hinst = kernel32.GetModuleHandleW(None)

        @ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND, wintypes.UINT,
                            wintypes.WPARAM, wintypes.LPARAM)
        def wnd_proc(hwnd, msg, wparam, lparam):
            return self._wndproc(hwnd, msg, wparam, lparam)

        self._wndproc_ref = wnd_proc  # 防 GC

        wc = WNDCLASSW()
        wc.style = CS_HREDRAW | CS_VREDRAW
        wc.lpfnWndProc = wnd_proc
        wc.hInstance = hinst
        wc.hCursor = user32.LoadCursorW(None, 32512)  # IDC_ARROW
        wc.hbrBackground = gdi32.CreateSolidBrush(0x00FFF3D6)  # 奶油黄底（圆外被裁剪）
        wc.lpszClassName = "CandyOrbWindow"
        user32.RegisterClassW(ctypes.byref(wc))

        # 椭圆命中区域（AT-05）：区域句柄所有权交系统，不重复释放（§4.1）
        rgn = gdi32.CreateEllipticRgn(0, 0, self.size, self.size)

        style = WS_POPUP
        ex_style = WS_EX_TOPMOST | WS_EX_TOOLWINDOW
        self.hwnd = user32.CreateWindowExW(
            ex_style, wc.lpszClassName, "CandyRollCall", style,
            pos[0], pos[1], self.size, self.size,
            None, None, hinst, None)
        if not self.hwnd:
            raise OSError(f"CreateWindowExW failed: {kernel32.GetLastError()}")
        user32.SetWindowRgn(self.hwnd, rgn, True)
        self._make_brushes()
        self._last_pos = pos
        self._ready.set()
        user32.ShowWindow(self.hwnd, 1)  # SW_SHOWNORMAL

    def _make_brushes(self) -> None:
        # 糖果色：主球体蜜桃粉，边缘深一圈做立体感（0x00BBGGRR）
        self._hbr_core = gdi32.CreateSolidBrush(0x008FC7FF)   # 蜜桃粉 #FFC78F
        self._hbr_ring = gdi32.CreateSolidBrush(0x00649BE8)   # 深一档 #E89B64

    # ---------- 消息循环（独立 GUI 循环，不阻塞 pywebview 主循环） ----------
    def _message_loop(self) -> None:
        msg = MSG()
        while True:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r <= 0:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
            if msg.message == WM_APP_QUIT or not user32.IsWindow(self.hwnd or 0):
                if msg.message == WM_APP_QUIT:
                    break

    # ---------- WndProc ----------
    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_PAINT:
            self._on_paint(hwnd)
            return 0
        if msg == WM_ERASEBKGND:
            return 1
        if msg == WM_LBUTTONDOWN:
            self._on_lbutton_down(hwnd, lparam)
            return 0
        if msg == WM_MOUSEMOVE and self._dragging:
            self._on_mouse_move(hwnd, lparam)
            return 0
        if msg == WM_LBUTTONUP:
            self._on_lbutton_up(hwnd, lparam)
            return 0
        if msg == WM_RBUTTONUP:
            self._on_rbutton_up(hwnd)
            return 0
        if msg == WM_APP_SHOW:
            user32.ShowWindow(hwnd, 8)  # SW_SHOWNA
            user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0043)  # HWND_TOPMOST|NOSIZE|NOMOVE|NOACTIVATE
            return 0
        if msg == WM_APP_HIDE:
            user32.ShowWindow(hwnd, 0)
            return 0
        if msg == WM_APP_SNAP:
            self._do_snap(hwnd)
            return 0
        if msg == WM_APP_QUIT:
            user32.PostQuitMessage(0)
            return 0
        if msg == WM_DESTROY:
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # ---------- 绘制（GDI，无闪烁：InvalidatRect 限球区域） ----------
    def _on_paint(self, hwnd) -> None:
        ps = PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        size = self.size
        # 外圈
        brush = user32.GetStockObject(0)  # NULL_BRUSH 不便取色，用自建
        gdi32.SelectObject(hdc, self._hbr_ring)
        gdi32.Ellipse(hdc, 0, 0, size, size)
        # 内核高光
        gdi32.SelectObject(hdc, self._hbr_core)
        inset = max(4, size // 10)
        gdi32.Ellipse(hdc, inset, inset, size - inset, size - inset)
        # 高光点
        white = gdi32.CreateSolidBrush(0x00FFFFFF)
        gdi32.SelectObject(hdc, white)
        gdi32.Ellipse(hdc, int(size * 0.25), int(size * 0.18),
                      int(size * 0.45), int(size * 0.38))
        gdi32.DeleteObject(white)
        user32.EndPaint(hwnd, ctypes.byref(ps))

    # ---------- 交互：拖动与点击区分（AT-08） ----------
    def _on_lbutton_down(self, hwnd, lparam) -> None:
        self._dragging = True
        self._drag_moved = False
        self._drag_off_x = ctypes.c_short(lparam & 0xFFFF).value
        self._drag_off_y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
        user32.SetCapture(hwnd)

    def _on_mouse_move(self, hwnd, lparam) -> None:
        if not self._dragging:
            return
        x = ctypes.c_short(lparam & 0xFFFF).value
        y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
        pt = POINT(x, y)
        user32.ClientToScreen(hwnd, ctypes.byref(pt))
        new_x = pt.x - self._drag_off_x
        new_y = pt.y - self._drag_off_y
        if abs(new_x - self._last_pos[0]) > 2 or abs(new_y - self._last_pos[1]) > 2:
            self._drag_moved = True
        user32.SetWindowPos(hwnd, 0, new_x, new_y, 0, 0, 0x0015)  # NOSIZE|NOZORDER|NOACTIVATE
        self._last_pos = (new_x, new_y)

    def _on_lbutton_up(self, hwnd, lparam) -> None:
        if not self._dragging:
            return
        self._dragging = False
        user32.ReleaseCapture()
        if self._drag_moved:
            # 拖动后松开：不触发单击（§4.2），执行吸附
            if self.snap_enabled:
                self._do_snap(hwnd)
            self._save_position(hwnd)
        else:
            # 单击：展开点名页
            try:
                self.on_click()
            except Exception:
                pass

    def _on_rbutton_up(self, hwnd) -> None:
        """右键菜单：展开点名 / 管理名单 / 退出工具（§4.1）。"""
        hmenu = user32.CreatePopupMenu()
        user32.AppendMenuW(hmenu, MF_STRING, MENU_EXPAND, "展开点名")
        user32.AppendMenuW(hmenu, MF_STRING, MENU_MANAGE, "管理名单")
        user32.AppendMenuW(hmenu, MF_STRING, MENU_QUIT, "退出工具")
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(hwnd)
        cmd = user32.TrackPopupMenu(hmenu, TPM_RIGHTBUTTON | TPM_RETURNCMD,
                                    pt.x, pt.y, 0, hwnd, None)
        user32.DestroyMenu(hmenu)
        if cmd == MENU_EXPAND:
            try:
                self.on_click()
            except Exception:
                pass
        elif cmd == MENU_MANAGE:
            try:
                self.on_manage()
            except Exception:
                pass
        elif cmd == MENU_QUIT:
            try:
                self.on_quit()
            except Exception:
                pass

    # ---------- 吸附与位置 ----------
    def _workarea(self) -> RECT:
        from .dpi_and_snap import Win32MonitorMixin
        return Win32MonitorMixin.get_primary_workarea()

    def _do_snap(self, hwnd) -> None:
        """吸附主屏工作区最近左/右侧，短距离缓动（此处直接落位）。"""
        work = self._workarea()
        pt = POINT(0, 0)
        user32.ClientToScreen(hwnd, ctypes.byref(pt))
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        sx, sy = nearest_snap_edge(rect.left, rect.top, self.size, work)
        user32.SetWindowPos(hwnd, 0, sx, sy, 0, 0, 0x0015)
        self._last_pos = (sx, sy)

    def _save_position(self, hwnd) -> None:
        """保存合法停留位置（坐标夹紧在工作区内，§4.2）。"""
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        work = self._workarea()
        x, y = clamp_to_workarea(rect.left, rect.top, self.size, work)
        self._last_pos = (x, y)
        try:
            self.on_position_saved(x, y)
        except Exception:
            pass

    on_position_saved: Callable[[int, int], None] = lambda self, x, y: None

    # ---------- 跨线程指令（PostMessage 投递，§3.2） ----------
    def show(self) -> None:
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_APP_SHOW, 0, 0)

    def hide(self) -> None:
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_APP_HIDE, 0, 0)

    def request_snap(self) -> None:
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_APP_SNAP, 0, 0)

    def get_position(self) -> tuple:
        return self._last_pos

    def close(self) -> None:
        """退出：向 GUI 线程投递退出消息，销毁窗口不留孤儿进程（AT-09）。"""
        if self._quit_sent:
            return
        self._quit_sent = True
        if self.hwnd and user32.IsWindow(self.hwnd):
            user32.PostMessageW(self.hwnd, WM_APP_QUIT, 0, 0)

    def _cleanup(self) -> None:
        if self.hwnd and user32.IsWindow(self.hwnd):
            user32.DestroyWindow(self.hwnd)
        self.hwnd = None


def create_orb_thread(on_click: Callable[[], None],
                      on_manage: Callable[[], None],
                      on_quit: Callable[[], None],
                      initial_pos: tuple,
                      snap_enabled: bool = True,
                      on_position_saved: Optional[Callable[[int, int], None]] = None,
                      ) -> OrbWin32:
    """便捷工厂：创建并启动球线程。"""
    orb = OrbWin32(on_click, on_manage, on_quit, snap_enabled=snap_enabled)
    if on_position_saved:
        orb.on_position_saved = on_position_saved
    orb.start(initial_pos)
    return orb
