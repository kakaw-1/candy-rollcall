/**
 * 截图工装：用 Chrome DevTools Protocol 驱动真实前端代码，渲染各状态并截图。
 *
 * 用法：node tools/screenshot/shoot.mjs
 * 前置：frontend/ 已由静态服务器提供（脚本会自行启动）。
 *
 * 产物：tools/screenshot/out/*.png
 * 声明：这是视图层效果预览，非实机验收（数据来自 mock，Win32 圆球不在其中）。
 */
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { readFile, mkdir, writeFile, rm } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..", "..");
const FRONTEND = path.join(ROOT, "frontend");
const OUT = path.join(__dirname, "out");
const CHROME = "/mnt/workspace/codeg/userhome/.codeg/npm-global/bin/google-chrome";
const PORT = 8731;
const DEBUG_PORT = 9333;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ---------------- 静态服务器（只读 frontend/） ---------------- */
const MIME = { ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8" };
function startStaticServer() {
  const server = createServer(async (req, res) => {
    try {
      const url = new URL(req.url, "http://localhost");
      const rel = url.pathname === "/" ? "/index.html" : url.pathname;
      const file = path.join(FRONTEND, path.normalize(rel).replace(/^(\.\.[/\\])+/, ""));
      if (!file.startsWith(FRONTEND) || !existsSync(file)) { res.writeHead(404).end("not found"); return; }
      const body = await readFile(file);
      res.writeHead(200, { "content-type": MIME[path.extname(file)] || "application/octet-stream" });
      res.end(body);
    } catch { res.writeHead(500).end("error"); }
  });
  return new Promise((resolve) => server.listen(PORT, "127.0.0.1", () => resolve(server)));
}

/* ---------------- 极简 CDP 客户端 ---------------- */
class CDP {
  constructor(ws) { this.ws = ws; this.id = 0; this.pending = new Map(); this.handlers = new Map();
    ws.addEventListener("message", (ev) => this._onMessage(ev.data)); }
  _onMessage(raw) {
    const msg = JSON.parse(typeof raw === "string" ? raw : raw.toString());
    if (msg.id && this.pending.has(msg.id)) {
      const { resolve, reject } = this.pending.get(msg.id);
      this.pending.delete(msg.id);
      msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result);
    } else if (msg.method) {
      (this.handlers.get(msg.method) || []).forEach((h) => h(msg.params));
    }
  }
  send(method, params = {}, sessionId) {
    const id = ++this.id;
    const payload = { id, method, params };
    if (sessionId) payload.sessionId = sessionId;
    this.ws.send(JSON.stringify(payload));
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
  }
  once(method) { return new Promise((resolve) => { const h = (p) => { this.handlers.set(method, (this.handlers.get(method) || []).filter((x) => x !== h)); resolve(p); };
    this.handlers.set(method, [...(this.handlers.get(method) || []), h]); }); }
}

/* ---------------- 打开一个已注入 mock 的页面 ---------------- */
async function openPage(cdp, { url, width, height, scale = 2 }) {
  const { targetId } = await cdp.send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await cdp.send("Target.attachToTarget", { targetId, flatten: true });
  await cdp.send("Page.enable", {}, sessionId);
  await cdp.send("Runtime.enable", {}, sessionId);
  // 关键：在页面任何脚本执行前注入 pywebview mock
  await cdp.send("Page.addScriptToEvaluateOnNewDocument", { source: MOCK_SOURCE }, sessionId);
  await cdp.send("Emulation.setDeviceMetricsOverride",
    { width, height, deviceScaleFactor: scale, mobile: false }, sessionId);

  const loaded = cdp.once("Page.loadEventFired");
  await cdp.send("Page.navigate", { url }, sessionId);
  await loaded;
  await waitFor(cdp, sessionId, "!document.getElementById('loading') || document.getElementById('loading').classList.contains('hidden')", 5000);
  await sleep(120);
  return { sessionId, targetId };
}

async function evaluate(cdp, sessionId, expression, awaitPromise = false) {
  const r = await cdp.send("Runtime.evaluate", { expression, awaitPromise, returnByValue: true }, sessionId);
  if (r.exceptionDetails) throw new Error("eval failed: " + JSON.stringify(r.exceptionDetails.exception?.description || r.exceptionDetails.text));
  return r.result?.value;
}

async function waitFor(cdp, sessionId, expr, timeout = 5000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeout) {
    if (await evaluate(cdp, sessionId, expr)) return true;
    await sleep(60);
  }
  throw new Error("waitFor 超时: " + expr);
}

async function shot(cdp, sessionId, name) {
  const { data } = await cdp.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false }, sessionId);
  const file = path.join(OUT, name + ".png");
  await writeFile(file, Buffer.from(data, "base64"));
  console.log("  ✓", name + ".png");
}

/* ---------------- 主流程 ---------------- */
const MOCK_SOURCE = await readFile(path.join(__dirname, "mock-api.js"), "utf8");

async function main() {
  await rm(OUT, { recursive: true, force: true });
  await mkdir(OUT, { recursive: true });

  const server = await startStaticServer();
  const chrome = spawn(CHROME, [
    "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
    `--remote-debugging-port=${DEBUG_PORT}`,
    "--user-data-dir=/tmp/candy-shot-profile",
    "about:blank",
  ], { stdio: "ignore" });

  try {
    // 等 Chrome 的调试端口就绪
    let wsUrl = null;
    for (let i = 0; i < 50 && !wsUrl; i++) {
      try {
        const v = await (await fetch(`http://127.0.0.1:${DEBUG_PORT}/json/version`)).json();
        wsUrl = v.webSocketDebuggerUrl;
      } catch { await sleep(200); }
    }
    if (!wsUrl) throw new Error("Chrome 调试端口未就绪");

    const ws = new WebSocket(wsUrl);
    await new Promise((res, rej) => { ws.addEventListener("open", res); ws.addEventListener("error", rej); });
    const cdp = new CDP(ws);

    const ROLL = `http://127.0.0.1:${PORT}/index.html`;
    const MANAGE = `http://127.0.0.1:${PORT}/index.html?view=manage`;

    // ---- 1. 待点名（IDLE） ----
    let p = await openPage(cdp, { url: ROLL, width: 420, height: 340 });
    await shot(cdp, p.sessionId, "01-roll-idle");
    await evaluate(cdp, p.sessionId, "1"); // keep page for later? 用独立页更干净 → 关掉
    await cdp.send("Target.closeTarget", { targetId: p.targetId });

    // ---- 2~5. 抽取全过程（动画中 / 逐个揭晓 / 结果 / 全屏） ----
    p = await openPage(cdp, { url: ROLL, width: 420, height: 340 });
    await evaluate(cdp, p.sessionId, "document.querySelectorAll('#count-picker .count-dot')[2].click()");
    await sleep(200);                                    // 走真实 UI 选中"3 人"
    await evaluate(cdp, p.sessionId, "document.getElementById('btn-start').click()");
    await sleep(1200);                                   // 基础 4 秒内 → 翻转中
    await shot(cdp, p.sessionId, "02-roll-animating");
    await sleep(3400);                                   // ≈4.6s → 揭晓进行中
    await shot(cdp, p.sessionId, "03-roll-revealing");
    await sleep(2000);                                   // ≈6.6s → 全部揭晓完成
    await shot(cdp, p.sessionId, "04-roll-result");
    // 全屏（加大字号布局）
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 720, deviceScaleFactor: 1, mobile: false }, p.sessionId);
    await evaluate(cdp, p.sessionId, "document.getElementById('btn-fullscreen').click()");
    await sleep(500);
    await shot(cdp, p.sessionId, "05-roll-fullscreen");
    await cdp.send("Target.closeTarget", { targetId: p.targetId });

    // ---- 6. 轮次完成（ROUND_COMPLETE） ----
    p = await openPage(cdp, { url: ROLL, width: 420, height: 340 });
    await evaluate(cdp, p.sessionId, "window.__mock.shrinkTo(3)");
    await evaluate(cdp, p.sessionId, "document.querySelectorAll('#count-picker .count-dot')[2].click()");
    await sleep(200);
    await evaluate(cdp, p.sessionId, "document.getElementById('btn-start').click()");
    await sleep(1200 + 3400 + 3 * 700 + 800);            // 等完整动画 + 揭晓
    await shot(cdp, p.sessionId, "06-roll-round-complete");
    await cdp.send("Target.closeTarget", { targetId: p.targetId });

    // ---- 7~9. 管理窗三个 Tab ----
    p = await openPage(cdp, { url: MANAGE, width: 760, height: 560, scale: 2 });
    await shot(cdp, p.sessionId, "07-manage-list");
    await evaluate(cdp, p.sessionId, `document.querySelector('.tab[data-tab="tools"]').click()`);
    await sleep(300);
    await shot(cdp, p.sessionId, "08-manage-tools");
    await evaluate(cdp, p.sessionId, `document.querySelector('.tab[data-tab="settings"]').click()`);
    await sleep(300);
    await shot(cdp, p.sessionId, "09-manage-settings");
    await cdp.send("Target.closeTarget", { targetId: p.targetId });

    ws.close();
    console.log("\n全部截图已生成 →", OUT);
  } finally {
    chrome.kill("SIGKILL");
    server.close();
  }
}

main().catch((e) => { console.error("截图失败:", e); process.exit(1); });
