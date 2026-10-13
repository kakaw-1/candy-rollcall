/* 前端入口：桥接 ready、视图路由、DOM 交互（§3.3、§7）。
   视图层与动画层——最终结果由 Python 锁定，前端不做随机。 */
"use strict";

/* ============ 全局状态 ============ */
const App = {
  ready: false,
  view: "roll",            // roll | manage
  snapshot: null,           // 最近一次 full_snapshot
  drawId: null,             // 当前事务 ID（防旧动画回调覆盖新抽取，§7.4）
  revealedCount: 0,         // REVEALING 已揭晓人数
  timers: [],               // 动画计时器（立即揭晓/清理时全部取消）
  rolling: false,
};

const $ = (id) => document.getElementById(id);
const api = () => window.pywebview && window.pywebview.api;

/* ============ 启动 ============ */
window.addEventListener("pywebviewready", init);
// 兜底：桥接已就绪后才加载脚本的场景
if (window.pywebview) setTimeout(init, 0);

function init() {
  if (App.ready) return;
  App.ready = true;
  App.view = new URLSearchParams(location.search).get("view") === "manage" ? "manage" : "roll";
  $("loading").classList.add("hidden");
  if (App.view === "roll") {
    $("roll-view").classList.remove("hidden");
    initRollView();
  } else {
    $("manage-view").classList.remove("hidden");
    initManageView();
  }
  refreshSnapshot();
}

async function refreshSnapshot() {
  const r = await api().get_snapshot();
  if (!r || !r.ok) return;
  App.snapshot = r;
  if (App.view === "roll") renderRoll(r);
  else renderManage(r);
}

/* ============ 通用确认框（§10：真正提交前必须显式确认） ============ */
function confirmDialog(text) {
  return new Promise((resolve) => {
    $("confirm-text").textContent = text;
    $("confirm-mask").classList.remove("hidden");
    const done = (val) => {
      $("confirm-mask").classList.add("hidden");
      $("confirm-ok").onclick = $("confirm-cancel").onclick = null;
      resolve(val);
    };
    $("confirm-ok").onclick = () => done(true);
    $("confirm-cancel").onclick = () => done(false);
  });
}

function clearTimers() {
  App.timers.forEach(clearTimeout);
  App.timers = [];
}

/* ============ 点名视图 ============ */
function initRollView() {
  buildCountPicker();
  bindRollControls();
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      // 全屏时 Esc 由 pywebview toggle_fullscreen 处理；否则收起或关闭菜单
      if (document.body.classList.contains("fullscreen")) {
        api().show_fullscreen();
        document.body.classList.remove("fullscreen");
      } else if (!$("more-menu").classList.contains("hidden")) {
        $("more-menu").classList.add("hidden");
      }
    }
  });
}

function buildCountPicker() {
  const picker = $("count-picker");
  picker.innerHTML = "";
  for (let i = 1; i <= 5; i++) {
    const b = document.createElement("button");
    b.className = "count-dot";
    b.textContent = i;
    b.onclick = async () => {
      if (App.rolling) return;              // 动画中禁用（§7.3）
      await api().set_draw_count(i);
      refreshSnapshot();
    };
    picker.appendChild(b);
  }
}

function bindRollControls() {
  $("btn-start").onclick = startDraw;
  $("btn-reveal-now").onclick = revealNow;
  $("btn-collapse").onclick = () => api().collapse();
  $("btn-fullscreen").onclick = toggleFullscreen;
  $("btn-more").onclick = () => $("more-menu").classList.toggle("hidden");
  $("btn-manage").onclick = () => api().show_manage();
  $("btn-empty-manage").onclick = () => api().show_manage();
  $("btn-quit").onclick = async () => {
    if (await confirmDialog("确定退出 CandyRollCall 吗？")) api().quit_app();
  };
  $("btn-mode").onclick = async () => {
    if (App.rolling) return;
    const cur = App.snapshot?.settings?.mode || "no_repeat";
    const next = cur === "no_repeat" ? "random" : "no_repeat";
    const r = await api().change_mode(next, false);
    if (r && r.needsConfirm) {
      if (await confirmDialog(r.message)) {
        await api().change_mode(next, true);
        refreshSnapshot();
      }
    }
  };
  document.addEventListener("click", (e) => {
    if (!$("more-menu").contains(e.target) && e.target !== $("btn-more")) {
      $("more-menu").classList.add("hidden");
    }
  });
}

function toggleFullscreen() {
  document.body.classList.toggle("fullscreen");
  api().show_fullscreen();
}

/* ---- 渲染（按状态机 §6.2） ---- */
function renderRoll(snap) {
  const draw = snap.draw;
  const settings = snap.settings;
  updateCountPicker(settings.drawCount);
  updateModeButton(settings.mode);
  updateRemaining(draw);

  const st = draw.state;
  $("empty-state").classList.toggle("hidden", st !== "EMPTY");
  $("idle-state").classList.toggle("hidden", st !== "IDLE");
  $("round-complete").classList.toggle("hidden", !draw.roundComplete || st !== "ROUND_COMPLETE");

  // “立即揭晓”仅 ANIMATING / REVEALING 显示（V2.1）
  const animating = st === "ANIMATING" || st === "REVEALING";
  $("btn-reveal-now").classList.toggle("hidden", !animating);
  $("btn-start").textContent = st === "ROUND_COMPLETE" ? "开启新一轮"
    : (draw.activeResult && draw.revealDone && st === "RESULT") ? "再抽一次" : "开始点名";
  $("btn-start").disabled = st === "EMPTY" || animating;

  App.rolling = animating;
  $("count-picker").style.opacity = animating ? "0.4" : "1";
  $("btn-mode").disabled = animating;

  // 恢复场景（收起/全屏回来、窗口重建）：直接重建静态结果，不重抽（§6.2）
  if (draw.activeResult && draw.revealDone) {
    renderResultStatic(draw.activeResult.students);
  }
}

function updateCountPicker(n) {
  [...$("count-picker").children].forEach((b, i) => {
    b.classList.toggle("active", i + 1 === n);
  });
}

function updateModeButton(mode) {
  $("btn-mode").textContent = mode === "no_repeat" ? "🔁 不重复" : "🎲 完全随机";
}

function updateRemaining(draw) {
  const el = $("remaining");
  if (App.snapshot?.settings?.mode === "no_repeat") {
    el.textContent = `剩余 ${draw.remaining} 人`;
  } else {
    el.textContent = "";
  }
}

/* ---- 抽取与动画（§7.2 节拍） ---- */
async function startDraw() {
  const draw = App.snapshot?.draw;
  if (draw?.state === "ROUND_COMPLETE") {
    // 手动开启新一轮
    const r = await api().reset_round(true);
    if (r?.ok) refreshSnapshot();
    return;
  }
  if (App.rolling) return;

  const r = await api().draw();     // Python 锁定结果
  if (!r || !r.ok) {
    if (r && r.error === "BUSY") return;   // 连点：忽略（AT-18）
    alert(r?.message || "抽取失败");
    return;
  }
  beginAnimation(r);
}

function beginAnimation(result) {
  App.drawId = result.drawId;
  App.revealedCount = 0;
  clearTimers();
  const students = result.students;
  const baseSec = result.baseAnimationSeconds || 4;
  const perReveal = 600;   // 每人约 0.6 秒（§7.2）

  buildCards(students, true);
  startRollingCards(students);

  // 阶段 2：基础时长后逐人揭晓
  App.timers.push(setTimeout(() => {
    if (App.drawId !== result.drawId) return;   // 过期回调防御
    stopRollingCards();
    revealSequence(result, perReveal);
  }, baseSec * 1000));
}

function buildCards(students, rolling) {
  const box = $("cards");
  box.innerHTML = "";
  box.classList.toggle("grid", students.length >= 3);
  students.forEach((s) => {
    const card = document.createElement("div");
    card.className = "name-card" + (rolling ? " rolling" : " revealed");
    card.innerHTML = `<div class="face">${esc(s.name)}</div>` +
      (s.studentNo ? `<div class="no-tag">${esc(s.studentNo)}号</div>` : "");
    box.appendChild(card);
  });
}

function startRollingCards(students) {
  // 临时姓名只是动效，不决定结果（§7.2）；滚动池取全班名单
  const pool = (App.snapshot?.students || students).map((s) => s.name);
  const cards = document.querySelectorAll("#cards .name-card .face");
  const iv = setInterval(() => {
    cards.forEach((el) => { el.textContent = pool[Math.floor(Math.random() * pool.length)] || "？"; });
  }, 90);
  App.timers.push(iv);
  // setInterval 句柄混入 timers；clearTimers 用 clearTimeout 也能清 interval（同一命名空间）
}

function stopRollingCards() {
  clearTimers();
}

function revealSequence(result, perReveal) {
  const cards = document.querySelectorAll("#cards .name-card");
  const students = result.students;
  App.revealedCount = 0;
  students.forEach((s, i) => {
    App.timers.push(setTimeout(() => {
      if (App.drawId !== result.drawId) return;
      const card = cards[i];
      if (card) {
        card.querySelector(".face").textContent = s.name;
        card.classList.remove("rolling");
        card.classList.add("revealed");
        spawnConfetti(card);
      }
      App.revealedCount = i + 1;
      if (App.revealedCount === students.length) {
        finishReveal(result);
      }
    }, i * perReveal));
  });
}

function finishReveal(result) {
  api().mark_reveal_complete(result.drawId).then((r) => {
    if (r && r.ok) {
      App.snapshot = { ...App.snapshot, draw: r.state };
      renderRoll({ ...App.snapshot, settings: App.snapshot.settings });
    }
  });
}

/* ---- 立即揭晓（V2.1 §6.2/§7.2）：终止剩余动画，展示全部锁定结果 ---- */
function revealNow() {
  if (!App.drawId) return;
  clearTimers();                        // 取消翻转/减速/逐张揭晓及粒子计时器
  const result = App.snapshot?.draw?.activeResult;
  const students = result ? result.students : null;
  if (!students) return;
  renderResultStatic(students);         // 直接静态展示全部已锁定姓名
  finishReveal({ drawId: App.drawId }); // 经既有 mark_reveal_complete 完成转换
}

/* ---- 静态结果（RESULT / ROUND_COMPLETE / 恢复） ---- */
function renderResultStatic(students) {
  buildCards(students, false);
}

/* ---- 粒子（有上限，自动清理，§7.4） ---- */
function spawnConfetti(card) {
  const stage = $("stage");
  if (stage.querySelectorAll(".confetti").length > 40) return;   // 上限
  const colors = ["#FFC78F", "#A8E6CF", "#9FD8FF", "#D8B4F8", "#FFE3A0"];
  for (let i = 0; i < 6; i++) {
    const p = document.createElement("div");
    p.className = "confetti";
    p.style.left = card.offsetLeft + Math.random() * card.offsetWidth + "px";
    p.style.top = card.offsetTop + card.offsetHeight - 8 + "px";
    p.style.background = colors[i % colors.length];
    p.style.animationDelay = Math.random() * 0.15 + "s";
    stage.appendChild(p);
    setTimeout(() => p.remove(), 1200);
  }
}

/* ============ 管理视图 ============ */
function initManageView() {
  bindManageControls();
  window.addEventListener("beforeunload", () => {
    // 管理窗关闭（含 X）→ 通知后端编排恢复（AT-39/40）
    try { api().notify_window_closed("manage"); } catch (e) {}
  });
}

function bindManageControls() {
  // Tabs
  document.querySelectorAll(".tab").forEach((t) => {
    t.onclick = () => {
      document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
      t.classList.add("active");
      ["list", "tools", "settings"].forEach((k) => {
        $(`tab-${k}`).classList.toggle("hidden", k !== t.dataset.tab);
      });
    };
  });

  // 返回点名 / 关闭（销毁本管理窗，恢复先前界面）
  $("btn-close-manage").onclick = () => api().close_manage();

  // 名单编辑
  $("btn-add").onclick = addStudent;
  $("btn-paste").onclick = () => $("paste-panel").classList.toggle("hidden");
  $("btn-paste-cancel").onclick = () => $("paste-panel").classList.add("hidden");
  $("btn-paste-preview").onclick = previewPaste;
  $("btn-import").onclick = importFile;
  $("btn-import-cancel").onclick = () => $("import-panel").classList.add("hidden");
  $("btn-import-commit").onclick = commitImport;

  // 更多工具
  $("btn-export-xlsx").onclick = () => api().export_students("xlsx");
  $("btn-export-csv").onclick = () => api().export_students("csv");
  $("btn-backup").onclick = () => api().backup_all();
  $("btn-restore").onclick = restorePreview;
  $("btn-restore-cancel").onclick = () => $("restore-panel").classList.add("hidden");
  $("btn-restore-commit").onclick = restoreCommit;

  // 设置
  $("set-anim").oninput = (e) => { $("set-anim-val").textContent = e.target.value + " 秒"; };
  $("set-anim").onchange = async (e) => {
    await api().set_preference("baseAnimationSeconds", parseInt(e.target.value, 10));
  };
  $("set-snap").onchange = async (e) => {
    await api().set_preference("edgeSnap", e.target.checked);
  };
  $("set-sww").onchange = async (e) => {
    // 开机自启：后端白名单处理（§9.2）
    await api().set_preference("startWithWindows", e.target.checked);
  };
  $("btn-reset-round").onclick = async () => {
    const r = await api().reset_round(false);
    if (r?.needsConfirm && await confirmDialog(r.message)) {
      await api().reset_round(true);
    }
  };
}

/* ---- 名单渲染 ---- */
function renderManage(snap) {
  $("manage-count").textContent = `${snap.students.length} / 100 人`;
  const list = $("student-list");
  list.innerHTML = "";
  snap.students.forEach((s, i) => {
    const row = document.createElement("div");
    row.className = "student-row";
    row.innerHTML = `<span class="idx">${i + 1}</span>` +
      `<span class="s-name">${esc(s.name)}</span>` +
      (s.studentNo ? `<span class="s-no">${esc(s.studentNo)}号</span>` : "") +
      `<span class="flex-spacer"></span>` +
      `<button title="编辑">✏️</button><button class="del" title="删除">🗑️</button>`;
    row.querySelector(".del").onclick = async () => {
      if (await confirmDialog(`删除学生「${s.name}」？\n（提交后本轮点名将重置）`)) {
        // 删除 = 替换为去掉该行的名单
        const draft = snap.students.filter((x) => x.id !== s.id)
          .map((x) => ({ name: x.name, studentNo: x.studentNo, id: x.id }));
        const r = await api().commit_students(draft, "replace", null, true, snap.revision);
        if (r?.ok) refreshSnapshot();
        else alert(r?.message || "删除失败");
      }
    };
    row.querySelector("button:not(.del)").onclick = () => {
      $("inp-name").value = s.name;
      $("inp-no").value = s.studentNo || "";
      $("inp-name").dataset.editId = s.id;
      $("inp-name").focus();
    };
    list.appendChild(row);
  });
  // 设置面板回填
  const st = snap.settings;
  $("set-anim").value = st.baseAnimationSeconds;
  $("set-anim-val").textContent = st.baseAnimationSeconds + " 秒";
  $("set-snap").checked = st.edgeSnap;
  $("set-sww").checked = st.startWithWindows;
}

async function addStudent() {
  const name = $("inp-name").value.trim();
  const no = $("inp-no").value.trim();
  if (!name) { alert("姓名不能为空"); return; }
  const snap = App.snapshot;
  const editId = $("inp-name").dataset.editId;

  let draft;
  if (editId) {
    draft = snap.students.map((s) =>
      s.id === editId ? { id: s.id, name, studentNo: no || null } :
        { id: s.id, name: s.name, studentNo: s.studentNo });
    delete $("inp-name").dataset.editId;
  } else {
    draft = snap.students.map((s) => ({ id: s.id, name: s.name, studentNo: s.studentNo }));
    draft.push({ name, studentNo: no || null });
  }
  const r = await api().commit_students(draft, "replace", null, true, snap.revision);
  if (r?.ok) {
    $("inp-name").value = ""; $("inp-no").value = "";
    refreshSnapshot();
  } else {
    alert(r?.message || "提交失败");
  }
}

/* ---- 批量粘贴 ---- */
async function previewPaste() {
  const text = $("paste-area").value;
  if (!text.trim()) { alert("请先粘贴名单"); return; }
  const r = await api().import_from_paste(text);
  if (!r?.ok) { alert(r?.message || "解析失败"); return; }
  showImportPanel(r);
  $("paste-panel").classList.add("hidden");
}

/* ---- Excel/CSV 导入 ---- */
async function importFile() {
  const r = await api().import_file_dialog();
  if (!r?.ok) {
    if (r && r.error !== "Cancelled") alert(r.message || "读取文件失败");
    return;
  }
  // 默认映射：第 1 列姓名，第 2 列学号；表头按首行含“姓名/学号”判断
  const rows = r.previewRows || [];
  const hasHeader = rows.length > 0 && /姓名|学号|name/i.test(rows[0].join(" "));
  const mapping = { name: 0, studentNo: hasHeader ? (rows[0].length > 1 ? 1 : -1) : (r.rowCount > 0 ? 1 : -1) };
  const p = await api().import_preview(r.fileToken, mapping, hasHeader);
  if (!p?.ok) { alert(p?.message || "解析失败"); return; }
  showImportPanel(p);
}

function showImportPanel(p) {
  App.pendingImport = p;
  $("import-summary").innerHTML =
    `共 <b>${p.totalCount}</b> 条，有效 <b>${p.validCount}</b> 条` +
    (p.emptyNameRows.length ? `，<span class="hint warn">${p.emptyNameRows.length} 条空姓名将跳过</span>` : "") +
    (p.overLimit ? `，<span class="hint warn">⚠️ 超过 100 人上限，提交将被拒绝</span>` : "");
  const box = $("import-issues");
  box.innerHTML = "";
  const decisions = {};
  (p.issues || []).forEach((iss) => {
    decisions[iss.key] = "keep";
    const row = document.createElement("div");
    row.className = `issue-row ${iss.kind}`;
    row.innerHTML = `<span><b>${esc(iss.name)}</b>${iss.studentNo ? " · " + esc(iss.studentNo) + "号" : ""}</span>` +
      `<span class="hint">${esc(iss.detail)}</span><span class="flex-spacer"></span>`;
    const keep = document.createElement("button");
    keep.className = "keep"; keep.textContent = "保留";
    const skip = document.createElement("button");
    skip.className = "skip"; skip.textContent = "跳过";
    keep.onclick = () => { decisions[iss.key] = "keep"; row.classList.remove("skipped"); };
    skip.onclick = () => { decisions[iss.key] = "skip"; row.classList.add("skipped"); };
    row.appendChild(keep); row.appendChild(skip);
    box.appendChild(row);
  });
  App.pendingDecisions = decisions;
  $("import-panel").classList.remove("hidden");
}

async function commitImport() {
  const p = App.pendingImport;
  if (!p) return;
  if (p.overLimit) {
    alert(`有效名单 ${p.validCount} 人，超过上限 ${p.maxStudents} 人，已阻止提交`);
    return;
  }
  const strategy = document.querySelector('input[name="strategy"]:checked').value;
  if (!(await confirmDialog(strategy === "replace"
      ? "替换原名单将重置本轮点名进度，确认提交吗？"
      : "追加名单将重置本轮点名进度，确认提交吗？"))) return;
  const r = await api().commit_students(p.drafts, strategy, App.pendingDecisions, true, App.snapshot.revision);
  if (r?.ok) {
    $("import-panel").classList.add("hidden");
    $("paste-area").value = "";
    refreshSnapshot();
  } else {
    alert(r?.message || "提交失败");
  }
}

/* ---- 备份恢复 ---- */
async function restorePreview() {
  const r = await api().restore_preview();
  if (!r?.ok) {
    if (r && r.error !== "Cancelled") alert(r.message || "读取备份失败");
    return;
  }
  App.pendingRestore = r;
  $("restore-summary").innerHTML =
    `备份包含 <b>${r.count}</b> 名学生。<br>` +
    `恢复将<b>替换当前数据并重置本轮</b>。`;
  $("restore-sww-row").classList.toggle("hidden", !r.startWithWindowsInBackup);
  $("restore-panel").classList.remove("hidden");
}

async function restoreCommit() {
  const r = App.pendingRestore;
  if (!r) return;
  if (!(await confirmDialog("确认恢复？当前名单与设置将被替换，本轮点名重置。"))) return;
  const res = await api().restore_commit(r.restoreToken, true, $("restore-sww").checked);
  if (res?.ok) {
    $("restore-panel").classList.add("hidden");
    if (res.startupNote) alert(res.startupNote);
    refreshSnapshot();
  } else {
    alert(res?.message || "恢复失败");
  }
}

/* ============ 工具 ============ */
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}
