# 截图工装（仅开发用，不进入打包产物）

用无头 Chrome 渲染**真实前端代码**并截图，用于快速查看 UI 效果。

```bash
node tools/screenshot/shoot.mjs     # 产物 → tools/screenshot/out/*.png
```

## 原理

- `mock-api.js` 在页面脚本执行前注入，替换 `window.pywebview.api`，提供与 `app/bridge.py`
  同形的返回结构（`get_snapshot` / `draw` / `mark_reveal_complete` …）。
- `shoot.mjs` 通过 Chrome DevTools Protocol（Node 内置 WebSocket，无第三方依赖）
  打开页面、驱动真实交互（点"开始点名""立即揭晓"、切 Tab）、逐状态截图。
- 静态资源由脚本临时启动的本地服务器提供，只读 `frontend/`，不改动真实代码。

## 重要声明

这是**视图层效果预览，不是实机验收**：

- 数据来自 mock，不经过 Python 业务层；
- Win32 原生悬浮球不在其中（本工装无法呈现圆球）；
- 无头环境缺少彩色 emoji 字体，界面里的 emoji 会显示为单色/方块，Windows 上正常；
- 通过 pywebview/WebView2 集成才有的问题（DPI、透明、置顶、线程）拍不出来。

真实验收见 `tests/manual_windows_checklist.md`。
