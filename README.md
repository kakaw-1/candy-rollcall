# CandyRollCall 🍬 小学课堂随机点名工具

Windows 便携版桌面应用：双击 EXE 启动，糖果色圆形悬浮球，点击展开点名窗，
静音动效随机抽取 1～5 名学生。单班级（最多 100 人），全部数据本地存储，完全离线可用。

依据《小学课堂随机点名工具 开发文档 V2.1（交互精简修订版）》实现。

## 功能一览

- **悬浮球**：Win32 真圆形窗口（椭圆命中区域），置顶、拖动、左右边缘自动吸附、右键菜单
- **点名**：完全随机 / 不重复两种模式（默认不重复），每次 1～5 人依次揭晓
- **动效**：基础 2～8 秒（默认 4 秒）翻转减速 + 每人约 0.6 秒逐人揭晓；动画中可“立即揭晓”
- **管理**：手动添加/编辑/删除、批量粘贴、Excel/CSV 导入导出、重复逐项确认、完整 JSON 备份/恢复
- **系统集成**：开机自启（默认关闭，仅当前用户 HKCU）、单实例、WebView2 检测
- **全程静音**，无任何网络请求

## 目录结构

```
candy-rollcall/
├─ app/
│  ├─ main.py                 # WebView2 检测、启动与退出编排
│  ├─ bridge.py               # js_api 白名单（§10 API）
│  ├─ core/
│  │  ├─ models.py            # 数据模型与校验（学号保留前导零）
│  │  ├─ draw_service.py      # secrets 均匀抽样、状态机
│  │  └─ application_state.py # 线程安全会话单例（唯一事实来源）
│  ├─ storage/
│  │  ├─ json_store.py        # 原子写入 + .bak 损坏恢复
│  │  ├─ import_export.py     # CSV/XLSX/JSON 备份
│  │  └─ user_paths.py        # %LOCALAPPDATA%\CandyRollCall
│  └─ windows/
│     ├─ orb_win32.py         # 圆形悬浮球 HWND/WndProc
│     ├─ window_manager.py    # 点名窗常驻可隐藏、管理窗按需创建销毁
│     ├─ dpi_and_snap.py      # 主屏工作区与吸附
│     └─ startup.py           # HKCU 自启与单实例互斥体
├─ frontend/                  # 糖果活力风页面（无外部资源）
├─ packaging/CandyRollCall.spec
├─ tests/                     # 自动化测试 + Windows 实机清单
└─ requirements.txt
```

## 本地开发与测试

```bash
pip install -r requirements.txt
python -m pytest tests/ -q          # 数据与算法层自动化测试（55 项）
```

非 Windows 平台可运行核心逻辑测试；窗口层（悬浮球、WebView）须在 Windows 实机验证。

## Windows 打包（须在 Windows 开发机执行）

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
pyinstaller packaging\CandyRollCall.spec --clean --noconfirm
# dist\CandyRollCall.exe 复制到目标电脑，断网实测
```

先以 one-folder 调试，验收通过后再用 one-file（见 `tests/manual_windows_checklist.md`）。

## 数据位置

- `%LOCALAPPDATA%\CandyRollCall\data.json` — 名单与设置（UTF-8，原子写入）
- `data.json.bak` — 最近一次可恢复副本（损坏时自动尝试）
- 本轮已抽状态不落盘：退出重启后轮次从头开始

## 重要设计约束（与文档对应）

- 前端不做随机：结果由 Python `secrets.SystemRandom` 在抽取开始时一次锁定（drawId 事务）
- 管理窗按需创建、关闭即销毁；点名主窗长期保留（可隐藏）
- “立即揭晓”仅终止前端剩余动画，经 `mark_reveal_complete(drawId)` 幂等完成，不重抽
- 涉及确认的操作须前端显式确认 + 后端 revision 校验，防过期草稿覆盖
