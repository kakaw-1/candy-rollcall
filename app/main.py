"""应用入口：WebView2 检测、启动与退出编排（§3.2 应用生命周期、§9.1）。

初始化业务服务 → 检测 WebView2 → 创建点名主窗（可隐藏）→ 启动 GUI 循环
及球线程 → 圆球可见。点击球显示点名窗；进管理先隐藏点名窗再按需创建；
关闭管理即销毁并恢复原状态；退出统一结束。
"""
from __future__ import annotations

import ctypes
import sys
import threading
from pathlib import Path

# ---- 打包资源路径（PyInstaller _MEIPASS 兼容）----
def resource_path(relative: str) -> str:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return str(Path(base) / relative)
    return str(Path(__file__).resolve().parent.parent / relative)


def check_webview2_runtime() -> bool:
    """检测 WebView2 Runtime（§9.1）：核对注册表版本字段。

    不静默回退 MSHTML；缺失时由 caller 提示安装官方运行环境。
    """
    if sys.platform != "win32":
        return True  # 非 Windows 开发环境视为可用（实机验证在 Windows 上做）
    try:
        import winreg
        for root, path in (
            (winreg.HKEY_LOCAL_MACHINE,
             r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
            (winreg.HKEY_CURRENT_USER,
             r"Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
        ):
            try:
                with winreg.OpenKey(root, path) as k:
                    pv, _ = winreg.QueryValueEx(k, "pv")
                    if pv and pv not in ("0.0.0.0", ""):
                        return True
            except OSError:
                continue
    except Exception:
        pass
    return False


def show_webview2_missing_dialog() -> None:
    """缺失 WebView2 时的明确提示（AT-04），不自动下载、不提权。"""
    message = (
        "当前电脑缺少 Microsoft Edge WebView2 Runtime。\n\n"
        "本工具需要 WebView2 才能显示界面。\n"
        "请从微软官网下载并安装 WebView2 Runtime（Evergreen 安装程序）：\n"
        "https://developer.microsoft.com/microsoft-edge/webview2/\n\n"
        "安装完成后重新打开本工具。"
    )
    if sys.platform == "win32":
        MB_OK = 0x0
        MB_ICONERROR = 0x10
        ctypes.windll.user32.MessageBoxW(None, message, "缺少运行环境", MB_OK | MB_ICONERROR)
    else:
        print(message, file=sys.stderr)


def get_frontend_url() -> str:
    """本地静态资源 URL；pywebview 内置静态服务器加载，避免 file:// 限制（§3.3）。"""
    html = resource_path("frontend/index.html")
    return str(Path(html).resolve())


def main() -> int:
    import webview

    # 0. WebView2 检测（在创建任何窗口之前）
    if not check_webview2_runtime():
        show_webview2_missing_dialog()
        return 2

    # 1. 单实例（AT-31）
    from .windows.startup import SingleInstanceGuard, StartupManager
    guard = SingleInstanceGuard()
    if not guard.acquire():
        # 重复双击：激活现有实例即可（此处简单退出；实机验证阶段可扩展窗口激活）
        return 0

    # 2. 业务服务初始化（崩溃后重启重置本轮，加载名单设置，AT-29）
    from .core.application_state import ApplicationState
    state = ApplicationState.instance()
    state.startup()

    # 3. 窗口编排
    from .windows.window_manager import WindowManager
    wm = WindowManager()

    # 4. 悬浮球（独立 GUI 线程；跨线程 PostMessage）
    from .windows.orb_win32 import create_orb_thread
    from .windows.dpi_and_snap import Win32MonitorMixin, RECT, clamp_to_workarea

    settings = state.get_settings()
    snap_enabled = bool(settings.get("edgeSnap", True))
    saved = settings.get("lastOrbPosition") or {"x": 0, "y": 0}

    def on_position_saved(x: int, y: int) -> None:
        state.set_preference("lastOrbPosition", {"x": x, "y": y})

    def on_orb_click() -> None:
        # 单击球：展开点名窗（§4.2）。跨线程 → 投递到 pywebview 主循环线程。
        try:
            wm.roll_window.show()
            wm.roll_window.restore()
        except Exception:
            pass

    def on_orb_manage() -> None:
        # 右键→管理名单：隐藏球 + 按需管理窗；关闭后恢复球（§4.2）
        wm.open_manage(create_manage_window)

    def on_orb_quit() -> None:
        quit_app()

    def collapse_roll_window() -> None:
        """收起：点名窗隐藏变回悬浮球；轮次保留（§2）。"""
        wm.hide_roll()

    def quit_app() -> None:
        """统一退出：停止球线程、关闭所有窗口、结束进程（无残留，AT-09）。"""
        try:
            orb.close()
        except Exception:
            pass
        try:
            wm.shutdown()
        except Exception:
            pass
        import os
        os._exit(0)  # 确保无孤儿线程/进程残留

    # 初始位置：主屏右边缘中部附近（实施建议，§4.2），夹紧到工作区
    if sys.platform == "win32":
        work = Win32MonitorMixin.get_primary_workarea()
        from .windows.orb_win32 import ORB_SIZE
        init_x = work.right - ORB_SIZE - 16 if not saved.get("x") else int(saved["x"])
        init_y = (work.top + work.bottom) // 2 - ORB_SIZE // 2 if not saved.get("y") else int(saved["y"])
        init_x, init_y = clamp_to_workarea(init_x, init_y, ORB_SIZE, work)
    else:
        init_x, init_y = int(saved.get("x", 100)), int(saved.get("y", 100))

    def create_roll_window() -> webview.Window:
        """点名主窗：唯一长期保留的业务窗；先隐藏创建，球线程就绪后显示。"""
        return webview.create_window(
            "CandyRollCall 点名",
            get_frontend_url(),
            js_api=bridge,
            width=settings.get("expandedSize", {}).get("width", 420),
            height=settings.get("expandedSize", {}).get("height", 340),
            min_size=(340, 260),
            hidden=True,  # 初始隐藏；点击球显示（实机验证项 §3.2）
        )

    def create_manage_window() -> webview.Window:
        """管理窗：按需创建；关闭事件走 close_manage 编排，不触发应用退出。"""
        win = webview.create_window(
            "管理名单 – CandyRollCall",
            get_frontend_url() + "?view=manage",
            js_api=bridge,
            width=760,
            height=560,
            min_size=(600, 420),
        )
        win.events.closed += on_manage_window_closed
        return win

    def on_manage_window_closed() -> None:
        """管理窗关闭（X 或返回点名）：销毁并恢复先前状态，不退出主进程。"""
        wm.close_manage()
        # 从悬浮球右键进入时：关闭管理后恢复球（AT-40）
        if orb:
            orb.show()

    # 5. 桥接（窗口编排回调注入）
    from .bridge import Bridge
    bridge = Bridge(state, window_ctrl={
        "open_manage": lambda: wm.open_manage(create_manage_window),
        "close_manage": on_manage_window_closed,
        "collapse": collapse_roll_window,
        "fullscreen": lambda: wm.toggle_fullscreen(),
        "quit": quit_app,
    })

    # 6. 启动球线程（在 pywebview GUI 循环启动前创建窗口对象不可行——
    #    pywebview 需主线程循环。这里先起球线程，再 webview.start 阻塞主线程。）
    orb = create_orb_thread(
        on_click=on_orb_click,
        on_manage=on_orb_manage,
        on_quit=quit_app,
        initial_pos=(init_x, init_y),
        snap_enabled=snap_enabled,
        on_position_saved=on_position_saved,
    )

    # 7. 开机启动路径变更检查（AT-30）
    sm = StartupManager()
    if sm.is_enabled() and sm.check_stale_path():
        # 下次手动启动时识别当前路径；此处静默修正为当前 EXE（用户已在用新版路径）
        sm.enable()

    # 8. 创建点名主窗并启动 GUI 循环（主线程阻塞）
    create_roll_window()
    try:
        webview.start(gui="edgechromium", debug=False)
    except Exception:
        # WebView2 环境异常等；明确失败而非伪装成功
        raise
    finally:
        try:
            orb.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
