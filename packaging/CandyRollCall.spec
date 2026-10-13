# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包方案（§12）。示意 spec——hiddenimports/datas/excludes
# 须在 Windows 实机上以 one-folder 验证后再用于 one-file 正式构建。
import sys

block_cipher = None

a = Analysis(
    ['..\\app\\main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('..\\frontend', 'frontend'),          # 静态网页资源（index.html/src/assets）
    ],
    hiddenimports=[
        'webview',                              # pywebview
        'webview.platforms.edgechromium',       # WebView2 渲染器（§3.4 禁止回退 MSHTML）
        'openpyxl',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'tkinter',  # 无关 GUI 引擎（§12.1）
        'matplotlib', 'numpy', 'pandas',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='CandyRollCall',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,          # one-file：解包到临时目录（§3.4 冷启动成本已知）
    console=False,                # windowed：双击启动无控制台
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app.ico' if sys.platform == 'win32' else None,
)
