/* 截图工装：在页面脚本执行前注入的 pywebview 桥接 mock。
 *
 * 目的：在不启动 Windows/pywebview 的前提下，用真实前端代码渲染界面。
 * 这是"视图层效果预览"，不是实机验收——数据是假的，Win32 悬浮球拍不到。
 *
 * 仅用于 tools/screenshot/，不进入打包产物。
 */
(function () {
  "use strict";

  // 一个典型的小学班级（含同名学生与学号，便于展示辅助文字）
  var ROSTER = [
    ["张小明", "01"], ["李小红", "02"], ["王小虎", "03"], ["赵思远", "04"],
    ["陈雨欣", "05"], ["刘子涵", "06"], ["杨浩然", "07"], ["黄诗琪", "08"],
    ["周天翊", "09"], ["吴梓萱", "10"], ["郑一鸣", "11"], ["孙悦然", "12"],
    ["张小明", "13"], ["林清和", "14"], ["徐若曦", "15"], ["何嘉树", "16"],
  ];

  var students = ROSTER.map(function (r, i) {
    return { id: "s" + i, name: r[0], studentNo: r[1] };
  });

  var settings = {
    mode: "no_repeat",
    drawCount: 1,
    baseAnimationSeconds: 4,
    edgeSnap: true,
    startWithWindows: false,
    lastOrbPosition: { x: 0, y: 0 },
    expandedSize: { width: 420, height: 340 }
  };

  var revision = 3;
  var drawn = {};          // id -> true（本轮已抽）
  var active = null;       // {drawId, students}
  var revealDone = false;
  var phase = students.length ? "IDLE" : "EMPTY";
  var seq = 0;

  function remaining() {
    if (settings.mode !== "no_repeat") return students.length;
    return students.filter(function (s) { return !drawn[s.id]; }).length;
  }

  function drawState() {
    return {
      state: phase,
      mode: settings.mode,
      studentCount: students.length,
      remaining: remaining(),
      roundComplete: phase === "ROUND_COMPLETE",
      activeResult: active ? { drawId: active.drawId, students: active.students } : null,
      revealDone: revealDone,
      serverTime: Date.now() / 1000
    };
  }

  function snapshot() {
    return {
      ok: true,
      revision: revision,
      students: students.map(function (s) { return { id: s.id, name: s.name, studentNo: s.studentNo }; }),
      settings: JSON.parse(JSON.stringify(settings)),
      draw: drawState()
    };
  }

  function delay(v) {
    return new Promise(function (res) { setTimeout(function () { res(v); }, 8); });
  }

  var api = {
    get_snapshot: function () { return delay(snapshot()); },

    draw: function (count) {
      var n = count || settings.drawCount;
      var pool = settings.mode === "no_repeat"
        ? students.filter(function (s) { return !drawn[s.id]; })
        : students.slice();
      // 洗牌后取前 n（与后端一致的"不放回"语义）
      for (var i = pool.length - 1; i > 0; i--) {
        var j = Math.floor(Math.random() * (i + 1));
        var t = pool[i]; pool[i] = pool[j]; pool[j] = t;
      }
      var picked = pool.slice(0, Math.min(n, pool.length));
      seq += 1;
      active = { drawId: "d" + seq + "-mock", students: picked };
      picked.forEach(function (s) { drawn[s.id] = true; });
      revealDone = false;
      phase = "ANIMATING";
      return delay({
        ok: true,
        drawId: active.drawId,
        students: picked,
        state: drawState(),
        baseAnimationSeconds: settings.baseAnimationSeconds,
        remaining: remaining()
      });
    },

    mark_reveal_complete: function (drawId) {
      if (!active || active.drawId !== drawId) return delay({ ok: true, applied: false, state: drawState() });
      if (!revealDone) {
        revealDone = true;
        phase = (settings.mode === "no_repeat" && remaining() === 0) ? "ROUND_COMPLETE" : "RESULT";
      }
      return delay({ ok: true, applied: true, state: drawState() });
    },

    set_draw_count: function (c) { settings.drawCount = c; revision += 1; return delay({ ok: true, value: c }); },
    change_mode: function (m, confirmed) {
      if (!confirmed) return delay({ ok: false, needsConfirm: true, message: "切换模式将重置本轮点名进度，确认切换吗？" });
      settings.mode = m; drawn = {}; active = null; revealDone = false; phase = "IDLE";
      revision += 1; return delay({ ok: true, state: drawState() });
    },
    reset_round: function (confirmed) {
      if (!confirmed) return delay({ ok: false, needsConfirm: true, message: "确认清空本轮已抽记录并重新开始吗？" });
      drawn = {}; active = null; revealDone = false; phase = students.length ? "IDLE" : "EMPTY";
      return delay({ ok: true, state: drawState() });
    },
    set_preference: function (k, v) { settings[k] = v; revision += 1; return delay({ ok: true, value: v }); },

    commit_students: function () { revision += 1; return delay(snapshot()); },
    import_from_paste: function () { return delay({ ok: true }); },
    export_students: function () { return delay({ ok: true }); },
    backup_all: function () { return delay({ ok: true }); },
    restore_preview: function () { return delay({ ok: false, error: "Cancelled" }); },
    restore_commit: function () { return delay({ ok: true }); },
    import_file_dialog: function () { return delay({ ok: false, error: "Cancelled" }); },
    import_preview: function () { return delay({ ok: true }); },

    show_manage: function () { return delay({ ok: true }); },
    close_manage: function () { return delay({ ok: true }); },
    collapse: function () { return delay({ ok: true }); },
    show_fullscreen: function () { return delay({ ok: true }); },
    quit_app: function () { return delay({ ok: true }); },
    notify_window_closed: function () { return delay({ ok: true }); }
  };

  window.pywebview = { api: api };

  // 截图工装专用钩子（生产代码里不存在）：用于构造特定状态的画面
  window.__mock = {
    // 把班级缩小到 n 人，便于演示"轮次完成"
    shrinkTo: function (n) {
      students = students.slice(0, n);
      drawn = {}; active = null; revealDone = false;
      phase = students.length ? "IDLE" : "EMPTY";
    },
    // 直接落定某个状态（用于无法自然地等到的画面）
    forcePhase: function (p) { phase = p; },
    forceActive: function () {
      active = { drawId: "d" + (++seq) + "-mock", students: students.slice(0, 3) };
      revealDone = false;
    },
    students: function () { return students; }
  };
})();
