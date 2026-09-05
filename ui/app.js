(() => {
  "use strict";

  const app = document.getElementById("app");
  const dialogBackdrop = document.getElementById("dialog-backdrop");
  const dialog = document.getElementById("dialog");
  const trayMenu = document.getElementById("tray-menu");
  const toast = document.getElementById("toast");
  const days = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"];
  const icons = {
    check: "check",
    success: "check-circle",
    warning: "alert",
    danger: "alert",
    playing: "volume",
    neutral: "check-circle",
  };

  let state = emptyState();
  let wizard = null;
  let lastDialogFocus = null;
  let toastTimer = 0;
  let helpTimer = 0;
  let helpSeconds = 0;
  let dialogKind = null;
  let draggedSongIndex = null;
  let mockApi = null;
  let taskWritePending = false;

  function emptyState() {
    return {
      clock: "00:00:00",
      tasks: [],
      tasks_revision: 0,
      music_files: [],
      startup_enabled: false,
      playback: { active: false, task_name: "", mode: "song", current_track: null, queue_length: 0 },
      next_run: null,
      status: { message: "就绪", tone: "neutral" },
      store: { load_state: "ready", read_only: false, backup_path: null, error: null },
      first_run_help: false,
      running: true,
    };
  }

  function svgIcon(name) {
    return `<svg class="icon" aria-hidden="true"><use href="#i-${name}"></use></svg>`;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function fileName(path) {
    const clean = String(path || "").replaceAll("\\", "/");
    return clean.split("/").pop() || clean;
  }

  function trackInfo(path) {
    return (state.music_files || []).find((item) => item.path === path) || {
      path,
      name: fileName(path),
      folder: String(path || "").split("/")[0] || "本地",
    };
  }

  function getBridge() {
    if (mockApi) return mockApi;
    if (window.pywebview && window.pywebview.api) return window.pywebview.api;
    return null;
  }

  async function invoke(name, ...args) {
    const bridge = getBridge();
    if (!bridge || typeof bridge[name] !== "function") {
      return { ok: false, error: "网页桥接尚未就绪" };
    }
    try {
      return await bridge[name](...args);
    } catch (error) {
      return { ok: false, error: error?.message || String(error) };
    }
  }

  function responseState(response) {
    if (!response) return null;
    if (response.state) return response.state;
    if (Array.isArray(response.tasks) && response.clock) return response;
    return null;
  }

  function applyResponse(response, fallbackMessage = "") {
    const next = responseState(response);
    if (next) renderState(next);
    if (response && response.ok === false) {
      showError("操作未完成", response.error || "请检查输入后重试");
      return false;
    }
    if (fallbackMessage) showToast(fallbackMessage);
    return true;
  }

  async function syncState() {
    const response = await invoke("get_state");
    const next = responseState(response);
    if (next) {
      renderState(next);
      if (next.first_run_help && dialogBackdrop.classList.contains("is-hidden")) openHelp(true);
    }
  }

  function showToast(message, tone = "neutral") {
    window.clearTimeout(toastTimer);
    toast.className = "toast";
    toast.dataset.tone = tone;
    toast.innerHTML = `${svgIcon(icons[tone] || "check-circle")}<span>${escapeHtml(message)}</span>`;
    toastTimer = window.setTimeout(() => toast.classList.add("is-hidden"), 3200);
  }

  function showError(title, message, detail = "") {
    lastDialogFocus = document.activeElement;
    dialogKind = "error";
    wizard = null;
    helpSeconds = 0;
    window.clearInterval(helpTimer);
    dialog.classList.remove("wide-dialog");
    dialog.innerHTML = `${dialogHeader(title, "请根据提示修正后重试")}
      <div class="error-panel" role="alert">${svgIcon("alert")}<div><strong>${escapeHtml(message)}</strong>${detail ? `<div class="form-hint">${escapeHtml(detail)}</div>` : ""}</div></div>
      <div class="dialog-actions"><span></span><div class="right-actions"><button type="button" class="primary-button" data-dialog-action="cancel">知道了</button></div></div>`;
    dialogBackdrop.classList.remove("is-hidden");
    focusDialog();
  }

  function statusIcon(tone) {
    return icons[tone] || "check-circle";
  }

  function renderState(nextState) {
    if (nextState && typeof nextState.tasks_revision === "number" && typeof state.tasks_revision === "number") {
      if (nextState.tasks_revision < state.tasks_revision) {
        return;
      }
    }
    state = { ...emptyState(), ...(nextState || {}) };
    state.tasks = Array.isArray(nextState?.tasks) ? nextState.tasks : [];
    state.tasks_revision = typeof nextState?.tasks_revision === "number" ? nextState.tasks_revision : (state.tasks_revision || 0);
    state.music_files = Array.isArray(nextState?.music_files) ? nextState.music_files : [];
    state.playback = { ...emptyState().playback, ...(nextState?.playback || {}) };
    state.status = { ...emptyState().status, ...(nextState?.status || {}) };
    state.store = { ...emptyState().store, ...(nextState?.store || {}) };
    app.classList.toggle("is-playing", Boolean(state.playback.active));
    document.getElementById("live-clock").textContent = state.clock || "00:00:00";
    renderNow();
    renderTasks();
    renderLibrary();
    const startupSwitch = document.getElementById("startup-switch");
    if (startupSwitch) startupSwitch.checked = Boolean(state.startup_enabled);
    const createButton = app.querySelector('[data-action="create"]');
    if (createButton) {
      createButton.disabled = Boolean(state.store.read_only) || taskWritePending;
      createButton.title = state.store.read_only ? "任务数据只读保护中" : "创建新任务";
    }
    renderStatus();
  }

  function renderNow() {
    const eyebrow = document.getElementById("now-eyebrow");
    const title = document.getElementById("now-title");
    const detail = document.getElementById("now-detail");
    const playback = state.playback || {};
    if (playback.active) {
      eyebrow.textContent = `正在播放 · ${playback.mode === "duration" ? "固定时长" : "固定曲目"}`;
      title.textContent = playback.task_name || "正在播放";
      detail.textContent = playback.current_track ? `当前曲目：${fileName(playback.current_track)}` : "队列准备中";
      return;
    }
    const next = state.next_run;
    if (!next) {
      eyebrow.textContent = "下一次播放";
      title.textContent = state.tasks.length ? "暂无下一次播放" : "暂无任务";
      detail.textContent = state.tasks.length ? "请启用任务并选择播放星期" : "添加任务后将在这里显示播放计划";
      return;
    }
    eyebrow.textContent = `下一次播放 · ${next.date_label} ${next.time}`;
    title.textContent = next.name || "未命名任务";
    const mode = next.mode === "duration" ? "固定时长" : "固定曲目";
    const count = `${next.files_count || 0} 首`;
    detail.textContent = `${mode} · ${count} · ${formatWeekdays(next.weekdays)}`;
  }

  function renderTasks() {
    const createButton = app.querySelector('[data-action="create"]');
    if (createButton) {
      createButton.disabled = Boolean(state.store.read_only) || taskWritePending;
      createButton.title = state.store.read_only ? "任务数据只读保护中" : "创建新任务";
    }
    const list = document.getElementById("task-list");
    if (!state.tasks.length) {
      list.innerHTML = `<div class="empty-state">还没有播放任务。<br><span class="form-hint">点击“创建新任务”开始设置时间、歌曲和星期。</span></div>`;
      return;
    }
    list.innerHTML = state.tasks.map((task, index) => {
      const name = task.name || "未命名任务";
      const mode = task.mode === "duration" ? "固定时长" : "固定曲目";
      const end = task.mode === "duration" && task.end_time ? `${task.end_next_day ? "次日 " : "至 "}${String(task.end_time).slice(0, 5)}` : `${Array.isArray(task.files) ? task.files.length : 0} 首`;
      const weekdays = formatWeekdays(task.weekdays);
      const enabled = task.enabled !== false;
      return `<article class="task-row" data-task-index="${index}" data-task="${escapeHtml(name)}">
        <div class="task-time">${escapeHtml(String(task.time || "").slice(0, 5))}</div>
        <div class="task-copy"><div class="task-name" title="${escapeHtml(name)}">${escapeHtml(name)}</div><div class="task-meta"><span class="tag">${mode}</span><span>${escapeHtml(end)}</span><span>${escapeHtml(weekdays)}</span></div></div>
        <div class="inline-actions"><label class="switch"><span class="sr-only">启用${escapeHtml(name)}</span><input type="checkbox" data-task-switch ${enabled ? "checked" : ""} ${taskWritePending ? "disabled" : ""}></label>
          <button type="button" class="mini-button" data-action="edit" aria-label="修改${escapeHtml(name)}" ${taskWritePending ? "disabled" : ""}>${svgIcon("pencil")}<span>修改</span></button>
          <button type="button" class="mini-button" data-action="delete" aria-label="删除${escapeHtml(name)}" ${taskWritePending ? "disabled" : ""}>${svgIcon("trash")}<span>删除</span></button></div>
      </article>`;
    }).join("");
  }

  function renderLibrary() {
    const count = document.getElementById("library-count");
    const list = document.getElementById("library-list");
    count.textContent = `已发现 ${state.music_files.length} 个音频文件`;
    if (!state.music_files.length) {
      list.innerHTML = `<div class="empty-state">暂无音频文件。<br><span class="form-hint">将音频放入 mp3 或 changyong 文件夹后点击刷新。</span></div>`;
      return;
    }
    list.innerHTML = state.music_files.map((track) => `<article class="library-row">
      <span class="track-icon">${svgIcon("music")}</span><div><div class="track-name" title="${escapeHtml(track.name)}">${escapeHtml(track.name)}</div><div class="track-folder">${escapeHtml(track.folder || "本地")}</div></div>
      <button type="button" class="mini-button" data-action="play" data-track="${escapeHtml(track.path)}">${svgIcon("play")}播放</button></article>`).join("");
  }

  function renderStatus() {
    const status = document.getElementById("status-message");
    const tone = state.status.tone || "neutral";
    status.className = `status-message ${escapeHtml(tone)}`;
    status.innerHTML = `${svgIcon(statusIcon(tone))}<span>${escapeHtml(state.status.message || "就绪")}</span>`;
  }

  function formatWeekdays(value) {
    if (!Array.isArray(value) || !value.length) return "未设置日期";
    const valid = value.filter((day) => Number.isInteger(day) && day >= 0 && day <= 6);
    if (valid.length === 7) return "每天";
    return valid.length ? `周${valid.map((day) => "一二三四五六日"[day]).join("、")}` : "未设置日期";
  }

  function setView(view) {
    app.dataset.view = view;
    document.getElementById("tasks-view").classList.toggle("is-hidden", view !== "tasks");
    document.getElementById("library-view").classList.toggle("is-hidden", view !== "library");
    app.querySelectorAll("[data-view-button]").forEach((button) => {
      if (button.dataset.viewButton === view) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    document.getElementById("main-content").focus({ preventScroll: true });
  }

  function dialogHeader(title, subtitle, step = null) {
    const track = step === null ? "" : `<div class="step-track" aria-label="任务创建进度">${[0, 1, 2, 3].map((index) => `<span class="${index <= step ? "active" : ""}"></span>`).join("")}</div>`;
    return `<div class="dialog-head"><div><div class="dialog-title" id="dialog-title">${escapeHtml(title)}</div><div class="dialog-subtitle">${escapeHtml(subtitle)}</div></div><button type="button" class="icon-button" data-dialog-action="cancel" aria-label="关闭">${svgIcon("x")}</button></div>${track}`;
  }

  function focusDialog() {
    window.requestAnimationFrame(() => {
      const focusable = dialog.querySelector("input,button,select,textarea");
      if (focusable) focusable.focus();
    });
  }

  function openWizard(index = null) {
    if (state.store.read_only) {
      showError("任务数据只读", "任务文件读取失败，本次运行只能查看。", state.store.backup_path ? `原文件备份为 ${state.store.backup_path}` : "请修复 tasks.json 后重启程序。");
      return;
    }
    const existing = index !== null && state.tasks[index] ? state.tasks[index] : null;
    const defaultFiles = existing ? [...(existing.files || [])] : state.music_files.slice(0, 2).map((item) => item.path);
    wizard = {
      index,
      expected_revision: state.tasks_revision,
      step: 0,
      error: "",
      data: {
        time: existing?.time?.slice(0, 5) || "09:00",
        mode: existing?.mode === "duration" ? "duration" : "song",
        end_time: existing?.end_time?.slice(0, 5) || "13:00",
        end_next_day: Boolean(existing?.end_next_day),
        files: defaultFiles,
        weekdays: Array.isArray(existing?.weekdays) ? [...existing.weekdays] : [0, 1, 2, 3, 4, 5, 6],
        name: existing?.name || "",
      },
    };
    dialogKind = "wizard";
    lastDialogFocus = document.activeElement;
    dialogBackdrop.classList.remove("is-hidden");
    renderWizard();
  }

  function renderWizard(errorMessage = "") {
    if (!wizard) return;
    if (errorMessage) wizard.error = errorMessage;
    const currentError = errorMessage || wizard.error || "";
    const titlePrefix = wizard.index === null ? "创建任务" : "修改任务";
    dialog.classList.toggle("wide-dialog", wizard.step === 1);
    const data = wizard.data;
    if (wizard.step === 0) {
      dialog.innerHTML = `${dialogHeader(`${titlePrefix} · 时间与模式`, "设置准确的开始与结束条件", 0)}
        <div class="time-row"><div class="form-group"><label class="form-label" for="start-time">开始时间</label><div class="time-control"><input class="time-field" id="start-time" type="time" value="${escapeHtml(data.time)}" required><div class="stepper"><button type="button" data-time-adjust="1" data-time-target="start-time" aria-label="开始时间增加一分钟">▲</button><button type="button" data-time-adjust="-1" data-time-target="start-time" aria-label="开始时间减少一分钟">▼</button></div></div></div>
          <div class="form-group ${data.mode === "duration" ? "" : "is-hidden"}" id="end-time-group"><label class="form-label" for="end-time">结束时间</label><div class="time-control"><input class="time-field" id="end-time" type="time" value="${escapeHtml(data.end_time)}"><div class="stepper"><button type="button" data-time-adjust="1" data-time-target="end-time" aria-label="结束时间增加一分钟">▲</button><button type="button" data-time-adjust="-1" data-time-target="end-time" aria-label="结束时间减少一分钟">▼</button></div></div></div></div>
        <fieldset class="form-group"><legend class="form-label">播放行为</legend><label class="mode-choice"><input type="radio" name="play-mode" value="song" ${data.mode === "song" ? "checked" : ""}><span><strong>固定曲目</strong><br><span class="form-hint">按顺序播放一次，全部播放完毕后停止</span></span></label><label class="mode-choice"><input type="radio" name="play-mode" value="duration" ${data.mode === "duration" ? "checked" : ""}><span><strong>固定时长</strong><br><span class="form-hint">循环播放歌单，到结束时间自动停止</span></span></label><label class="mode-choice ${data.mode === "duration" ? "" : "is-hidden"}" id="next-day-choice"><input id="next-day" type="checkbox" ${data.end_next_day ? "checked" : ""}><span>次日结束</span></label></fieldset>
        <div class="form-hint" id="time-feedback">${data.mode === "duration" ? `将在 ${escapeHtml(data.time)} 开始，循环播放至 ${escapeHtml(data.end_time)}` : `将在 ${escapeHtml(data.time)} 开始，所选歌曲播完后停止`}</div>${currentError ? `<div class="field-error" role="alert">${escapeHtml(currentError)}</div>` : ""}
        <div class="dialog-actions"><button type="button" class="secondary-button" data-dialog-action="cancel">取消</button><div class="right-actions"><button type="button" class="primary-button" data-dialog-action="next">下一步${svgIcon("arrow-right")}</button></div></div>`;
    } else if (wizard.step === 1) {
      const selected = data.files || [];
      const library = state.music_files.map((track) => {
        const added = selected.includes(track.path);
        return `<div class="song-row"><span class="song-name" title="${escapeHtml(track.name)}">${escapeHtml(track.name)}</span><button type="button" data-song-add="${escapeHtml(track.path)}" aria-label="添加 ${escapeHtml(track.name)}" ${added ? "disabled" : ""}>${svgIcon(added ? "check" : "plus")}</button></div>`;
      }).join("") || `<div class="empty-state">音乐库为空，请先刷新音乐列表。</div>`;
      const selectedRows = selected.map((path, index) => {
        const track = trackInfo(path);
        const ext = String(path).split(".").pop()?.toLowerCase();
        const isSupported = ["mp3", "wav", "flac", "ogg"].includes(ext);
        const badge = isSupported ? "" : ` <span class="tag tag-danger">格式不支持</span>`;
        return `<div class="song-row" draggable="true" data-song-index="${index}"><span class="drag-handle" aria-hidden="true">${svgIcon("grip")}</span><span class="song-name" title="${escapeHtml(track.name)}">${index + 1}. ${escapeHtml(track.name)}${badge}</span><button type="button" data-song-up="${index}" aria-label="上移 ${escapeHtml(track.name)}" ${index === 0 ? "disabled" : ""}>${svgIcon("arrow-up")}</button><button type="button" data-song-down="${index}" aria-label="下移 ${escapeHtml(track.name)}" ${index === selected.length - 1 ? "disabled" : ""}>${svgIcon("arrow-down")}</button><button type="button" data-song-remove="${index}" aria-label="移除 ${escapeHtml(track.name)}">${svgIcon("x")}</button></div>`;
      }).join("") || `<div class="empty-state">请至少选择一首歌曲</div>`;
      dialog.innerHTML = `${dialogHeader(`${titlePrefix} · 选择歌曲`, "从音乐库添加，并调整实际播放顺序", 1)}<div class="song-columns"><section class="song-column"><div class="song-column-title">音乐库 · 点击添加</div>${library}</section><section class="song-column"><div class="song-column-title">播放顺序 · 已选 ${selected.length} 首</div>${selectedRows}</section></div>${currentError ? `<div class="field-error" role="alert">${escapeHtml(currentError)}</div>` : ""}<div class="dialog-actions"><button type="button" class="secondary-button" data-dialog-action="back">${svgIcon("arrow-left")}上一步</button><div class="right-actions"><button type="button" class="secondary-button" data-dialog-action="cancel">取消</button><button type="button" class="primary-button" data-dialog-action="next" ${selected.length ? "" : "disabled"}>下一步${svgIcon("arrow-right")}</button></div></div>`;
    } else if (wizard.step === 2) {
      dialog.innerHTML = `${dialogHeader(`${titlePrefix} · 播放日期`, `${escapeHtml(data.time)} 开始 · 已选择 ${(data.files || []).length} 首歌曲`, 2)}<fieldset class="form-group"><legend class="form-label">选择需要播放的星期</legend><div class="week-grid">${days.map((day, index) => `<label><input type="checkbox" data-weekday="${index}" ${data.weekdays.includes(index) ? "checked" : ""}><span>${day}</span></label>`).join("")}</div></fieldset>${currentError ? `<div class="field-error" role="alert">${escapeHtml(currentError)}</div>` : ""}<div class="dialog-actions"><button type="button" class="secondary-button" data-dialog-action="back">${svgIcon("arrow-left")}上一步</button><div class="right-actions"><button type="button" class="secondary-button" data-dialog-action="cancel">取消</button><button type="button" class="primary-button" data-dialog-action="next">下一步${svgIcon("arrow-right")}</button></div></div>`;
    } else {
      dialog.innerHTML = `${dialogHeader(`${titlePrefix} · 任务命名`, "最后一步，名称会显示在任务列表与播放状态中", 3)}<div class="form-group"><label class="form-label" for="task-name">任务名称</label><input class="field" id="task-name" maxlength="80" value="${escapeHtml(data.name)}" placeholder="例如：晨间钢琴" required></div><div class="form-hint">任务会自动保存到本地，可随时修改、停用或删除。</div>${currentError ? `<div class="field-error" role="alert">${escapeHtml(currentError)}</div>` : ""}<div class="dialog-actions"><button type="button" class="secondary-button" data-dialog-action="back">${svgIcon("arrow-left")}上一步</button><div class="right-actions"><button type="button" class="primary-button" data-dialog-action="finish" ${taskWritePending ? "disabled" : ""}>${svgIcon("check")}完成</button></div></div>`;
    }
    focusDialog();
  }

  function saveWizardTimeInputs() {
    if (!wizard || wizard.step !== 0) return;
    const start = dialog.querySelector("#start-time")?.value;
    const end = dialog.querySelector("#end-time")?.value;
    const nextDay = dialog.querySelector("#next-day");
    if (typeof start === "string") wizard.data.time = start;
    if (typeof end === "string") wizard.data.end_time = end;
    if (nextDay) wizard.data.end_next_day = Boolean(nextDay.checked);
  }

  function collectWizardStep() {
    if (!wizard) return true;
    if (wizard.step === 0) {
      saveWizardTimeInputs();
      const start = wizard.data.time || "";
      const end = wizard.data.end_time || "";
      if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(start)) {
        renderWizard("请输入有效的开始时间。");
        return false;
      }
      if (wizard.data.mode === "duration") {
        if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(end)) {
          renderWizard("固定时长模式必须设置有效的结束时间。");
          return false;
        }
        if (!wizard.data.end_next_day && end <= start) {
          renderWizard("结束时间不晚于开始时间；如需跨午夜，请勾选“次日结束”。");
          return false;
        }
      }
    } else if (wizard.step === 1) {
      if (!wizard.data.files.length) {
        renderWizard("请至少选择一首歌曲。");
        return false;
      }
    } else if (wizard.step === 2) {
      wizard.data.weekdays = [...dialog.querySelectorAll("[data-weekday]:checked")].map((input) => Number(input.dataset.weekday));
      if (!wizard.data.weekdays.length) {
        renderWizard("请至少选择一个播放星期。");
        return false;
      }
    } else if (wizard.step === 3) {
      wizard.data.name = dialog.querySelector("#task-name")?.value.trim() || "未命名任务";
    }
    return true;
  }

  async function finishWizard() {
    if (taskWritePending) return;
    if (!wizard || !collectWizardStep()) return;
    const submittedWizard = wizard;
    const submittedName = submittedWizard.data.name || "未命名任务";
    const payload = {
      task: { ...submittedWizard.data },
      expected_revision: submittedWizard.expected_revision,
    };
    if (submittedWizard.index !== null) payload.index = submittedWizard.index;

    taskWritePending = true;
    renderWizard();

    try {
      const response = await invoke("save_task", payload);
      const next = responseState(response);
      if (next) renderState(next);
      if (response && response.ok === false) {
        if (wizard === submittedWizard) {
          renderWizard(response.error || "保存失败，请检查输入");
        } else {
          showToast(response.error || "保存失败", "danger");
        }
        return;
      }
      if (wizard === submittedWizard) {
        closeDialog();
        setView("tasks");
      }
      showToast(`已保存“${submittedName}”`, "success");
    } catch (err) {
      if (wizard === submittedWizard) {
        renderWizard(err?.message || "网络或桥接异常");
      }
    } finally {
      taskWritePending = false;
      renderTasks();
      if (wizard) renderWizard();
    }
  }

  function openHelp(forced = false) {
    lastDialogFocus = document.activeElement;
    wizard = null;
    dialogKind = "help";
    helpSeconds = forced ? 10 : 0;
    window.clearInterval(helpTimer);
    dialog.classList.remove("wide-dialog");
    renderHelp();
    dialogBackdrop.classList.remove("is-hidden");
    if (forced) {
      helpTimer = window.setInterval(() => {
        helpSeconds -= 1;
        renderHelp();
        if (helpSeconds <= 0) window.clearInterval(helpTimer);
      }, 1000);
    }
  }

  function renderHelp() {
    const button = helpSeconds > 0 ? `请阅读 (${helpSeconds}s)` : "我知道了";
    dialog.innerHTML = `${dialogHeader("使用说明", "完整保留当前播放器的使用流程")}<ol class="help-list"><li><span class="help-index">1</span><span><strong>准备音乐</strong><br><span class="form-hint">将音频放入 mp3 或 changyong 文件夹，然后刷新音乐列表。</span></span></li><li><span class="help-index">2</span><span><strong>创建任务</strong><br><span class="form-hint">依次设置时间与模式、歌曲顺序、播放星期和名称。</span></span></li><li><span class="help-index">3</span><span><strong>托盘运行</strong><br><span class="form-hint">关闭主窗口后继续运行；从托盘显示窗口或彻底退出。</span></span></li><li><span class="help-index">4</span><span><strong>播放模式</strong><br><span class="form-hint">固定曲目播放一次；固定时长循环播放，并支持次日结束。</span></span></li></ol><div class="dialog-actions"><span></span><div class="right-actions"><button type="button" class="primary-button" data-dialog-action="help-done" ${helpSeconds > 0 ? "disabled" : ""}>${button}</button></div></div>`;
    focusDialog();
  }

  function closeDialog() {
    if (helpSeconds > 0) return;
    window.clearInterval(helpTimer);
    helpTimer = 0;
    const wasHelp = dialogKind === "help";
    dialogBackdrop.classList.add("is-hidden");
    dialog.innerHTML = "";
    wizard = null;
    dialogKind = null;
    if (wasHelp && state.first_run_help) invoke("acknowledge_first_help");
    if (lastDialogFocus && document.contains(lastDialogFocus)) lastDialogFocus.focus();
  }

  function adjustTime(target, amount) {
    const input = dialog.querySelector(`#${target}`);
    if (!input) return;
    const [hour, minute] = (input.value || "00:00").split(":").map(Number);
    const total = ((hour * 60 + minute + amount) % 1440 + 1440) % 1440;
    input.value = `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
    saveWizardTimeInputs();
    const feedback = dialog.querySelector("#time-feedback");
    if (feedback && wizard) {
      feedback.textContent = wizard.data.mode === "duration"
        ? `将在 ${escapeHtml(wizard.data.time)} 开始，循环播放至 ${escapeHtml(wizard.data.end_time)}`
        : `将在 ${escapeHtml(wizard.data.time)} 开始，所选歌曲播完后停止`;
    }
  }

  function moveSong(from, to) {
    if (!wizard || from < 0 || to < 0 || from >= wizard.data.files.length || to >= wizard.data.files.length) return;
    const [song] = wizard.data.files.splice(from, 1);
    wizard.data.files.splice(to, 0, song);
    renderWizard();
  }

  function createMockApi() {
    const mockState = {
      ...emptyState(),
      clock: "08:24:16",
      tasks_revision: 0,
      tasks: [
        { time: "09:00:00", mode: "song", end_time: "", end_next_day: false, files: ["mp3/晨光钢琴曲.mp3", "changyong/Coffee Shop Ambience.flac", "mp3/午后爵士.wav", "changyong/雨夜白噪音.ogg"], name: "晨间钢琴", weekdays: [0, 1, 2, 3, 4], enabled: true },
        { time: "12:20:00", mode: "duration", end_time: "13:00:00", end_next_day: false, files: ["mp3/晨光钢琴曲.mp3"], name: "午间轻音乐", weekdays: [0, 1, 2, 3, 4, 5, 6], enabled: true },
        { time: "23:30:00", mode: "duration", end_time: "00:30:00", end_next_day: true, files: ["changyong/雨夜白噪音.ogg"], name: "夜间白噪音", weekdays: [4, 5], enabled: false },
      ],
      music_files: [
        { path: "mp3/晨光钢琴曲.mp3", name: "晨光钢琴曲.mp3", folder: "mp3" },
        { path: "changyong/Coffee Shop Ambience.flac", name: "Coffee Shop Ambience.flac", folder: "changyong" },
        { path: "mp3/午后爵士.wav", name: "午后爵士.wav", folder: "mp3" },
        { path: "changyong/雨夜白噪音.ogg", name: "雨夜白噪音.ogg", folder: "changyong" },
      ],
      next_run: { date_label: "今天", time: "09:00", name: "晨间钢琴", mode: "song", files_count: 4, weekdays: [0, 1, 2, 3, 4] },
      status: { message: "就绪 · 任务已自动保存", tone: "success" },
    };
    function reply() { return { ok: true, state: JSON.parse(JSON.stringify(mockState)) }; }
    return {
      get_state: async () => reply(),
      poll_events: async () => [],
      refresh_music: async () => reply(),
      save_task: async (payload) => {
        if (!payload || payload.expected_revision !== mockState.tasks_revision) {
          return { ok: false, error: "任务列表已更新，请重新操作", state: reply().state };
        }
        const raw = payload.task || payload;
        const task = { ...raw, time: `${raw.time}:00`.slice(0, 8), end_time: raw.end_time ? `${raw.end_time}:00`.slice(0, 8) : "" };
        if (payload.index === undefined) mockState.tasks.push(task);
        else mockState.tasks[payload.index] = task;
        mockState.tasks.sort((a, b) => (a.time || "").localeCompare(b.time || ""));
        mockState.tasks_revision += 1;
        return reply();
      },
      delete_task: async (index, expected_revision) => {
        if (expected_revision !== mockState.tasks_revision) {
          return { ok: false, error: "任务列表已更新，请重新操作", state: reply().state };
        }
        mockState.tasks.splice(index, 1);
        mockState.tasks_revision += 1;
        return reply();
      },
      set_task_enabled: async (index, enabled, expected_revision) => {
        if (expected_revision !== mockState.tasks_revision) {
          return { ok: false, error: "任务列表已更新，请重新操作", state: reply().state };
        }
        mockState.tasks[index].enabled = enabled;
        mockState.tasks_revision += 1;
        return reply();
      },
      play_track: async (path) => { mockState.playback = { active: true, task_name: "手动播放", mode: "song", current_track: path, queue_length: 1 }; mockState.status = { message: `正在播放：${fileName(path)}`, tone: "playing" }; return reply(); },
      stop_playback: async () => { mockState.playback = emptyState().playback; mockState.status = { message: "播放已停止", tone: "neutral" }; return reply(); },
      set_startup: async (enabled) => { mockState.startup_enabled = enabled; return reply(); },
      acknowledge_first_help: async () => { mockState.first_run_help = false; return reply(); },
      minimize: async () => reply(), hide: async () => reply(), show: async () => reply(), close: async () => reply(), exit: async () => reply(),
    };
  }

  // A fixture is opt-in for browser-harness visual QA only. Production pages
  // never enter this branch unless the explicit ?mock=1 query is supplied.
  if (new URLSearchParams(window.location.search).get("mock") === "1") mockApi = createMockApi();

  app.addEventListener("click", async (event) => {
    const dialogAction = event.target.closest("[data-dialog-action]");
    if (dialogAction) {
      const action = dialogAction.dataset.dialogAction;
      if (action === "cancel") closeDialog();
      if (action === "help-done") { closeDialog(); invoke("acknowledge_first_help"); }
      if (action === "next" && wizard && collectWizardStep()) { wizard.error = ""; wizard.step = Math.min(3, wizard.step + 1); renderWizard(); }
      if (action === "back" && wizard) { if (collectWizardStep()) { wizard.error = ""; wizard.step = Math.max(0, wizard.step - 1); renderWizard(); } }
      if (action === "finish") await finishWizard();
      return;
    }
    const viewButton = event.target.closest("[data-view-button]");
    if (viewButton) { setView(viewButton.dataset.viewButton); return; }
    const add = event.target.closest("[data-song-add]");
    if (add && wizard) { if (!wizard.data.files.includes(add.dataset.songAdd)) wizard.data.files.push(add.dataset.songAdd); renderWizard(); return; }
    const remove = event.target.closest("[data-song-remove]");
    if (remove && wizard) { wizard.data.files.splice(Number(remove.dataset.songRemove), 1); renderWizard(); return; }
    const up = event.target.closest("[data-song-up]");
    if (up && wizard) { moveSong(Number(up.dataset.songUp), Number(up.dataset.songUp) - 1); return; }
    const down = event.target.closest("[data-song-down]");
    if (down && wizard) { moveSong(Number(down.dataset.songDown), Number(down.dataset.songDown) + 1); return; }
    const adjust = event.target.closest("[data-time-adjust]");
    if (adjust && wizard) { adjustTime(adjust.dataset.timeTarget, Number(adjust.dataset.timeAdjust)); return; }
    const actionButton = event.target.closest("[data-action]");
    if (!actionButton) return;
    const action = actionButton.dataset.action;
    const row = actionButton.closest("[data-task-index]");
    const index = row ? Number(row.dataset.taskIndex) : null;
    if (action === "create" && !taskWritePending) openWizard();
    if (action === "edit" && Number.isInteger(index) && !taskWritePending) openWizard(index);
    if (action === "delete" && Number.isInteger(index)) {
      if (taskWritePending) return;
      taskWritePending = true;
      renderTasks();
      try {
        const response = await invoke("delete_task", index, state.tasks_revision);
        applyResponse(response);
      } finally {
        taskWritePending = false;
        renderTasks();
      }
    }
    if (action === "help") openHelp(false);
    if (action === "tray-menu") { trayMenu.classList.toggle("is-hidden"); actionButton.setAttribute("aria-expanded", String(!trayMenu.classList.contains("is-hidden"))); }
    if (action === "refresh") applyResponse(await invoke("refresh_music"), "音乐列表已刷新");
    if (action === "play") applyResponse(await invoke("play_track", actionButton.dataset.track));
    if (action === "stop") applyResponse(await invoke("stop_playback"));
    if (action === "minimize") applyResponse(await invoke("minimize"));
    if (action === "close") applyResponse(await invoke("close"));
    if (action === "show-window") { trayMenu.classList.add("is-hidden"); applyResponse(await invoke("show")); }
    if (action === "exit") { trayMenu.classList.add("is-hidden"); applyResponse(await invoke("exit")); }
  });

  app.addEventListener("change", async (event) => {
    if (event.target.id === "startup-switch") {
      const response = await invoke("set_startup", event.target.checked);
      applyResponse(response);
      if (response?.ok === false) event.target.checked = !event.target.checked;
    }
    if (event.target.matches("[data-task-switch]")) {
      if (taskWritePending) {
        event.target.checked = !event.target.checked;
        return;
      }
      const row = event.target.closest("[data-task-index]");
      if (!row) return;
      taskWritePending = true;
      renderTasks();
      try {
        const response = await invoke("set_task_enabled", Number(row.dataset.taskIndex), event.target.checked, state.tasks_revision);
        applyResponse(response);
        if (response?.ok === false) event.target.checked = !event.target.checked;
      } finally {
        taskWritePending = false;
        renderTasks();
      }
    }
    if (wizard && event.target.name === "play-mode") {
      saveWizardTimeInputs();
      wizard.error = "";
      wizard.data.mode = event.target.value;
      renderWizard();
    }
    if (wizard && event.target.id === "next-day") wizard.data.end_next_day = event.target.checked;
    if (wizard && event.target.matches("[data-weekday]")) wizard.data.weekdays = [...dialog.querySelectorAll("[data-weekday]:checked")].map((input) => Number(input.dataset.weekday));
  });

  dialog.addEventListener("dragstart", (event) => {
    const row = event.target.closest("[data-song-index]");
    if (!row || !wizard) return;
    draggedSongIndex = Number(row.dataset.songIndex);
    row.classList.add("dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", String(draggedSongIndex));
  });
  dialog.addEventListener("dragend", (event) => event.target.closest("[data-song-index]")?.classList.remove("dragging"));
  dialog.addEventListener("dragover", (event) => { if (event.target.closest("[data-song-index]")) event.preventDefault(); });
  dialog.addEventListener("drop", (event) => {
    const row = event.target.closest("[data-song-index]");
    if (!row || !wizard || draggedSongIndex === null) return;
    event.preventDefault();
    const target = Number(row.dataset.songIndex);
    moveSong(draggedSongIndex, target);
    draggedSongIndex = null;
  });

  dialogBackdrop.addEventListener("click", (event) => { if (event.target === dialogBackdrop) closeDialog(); });
  document.addEventListener("click", (event) => {
    if (!event.target.closest("#tray-menu") && !event.target.closest('[data-action="tray-menu"]')) trayMenu.classList.add("is-hidden");
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !dialogBackdrop.classList.contains("is-hidden")) closeDialog();
    if (event.key === "Escape") trayMenu.classList.add("is-hidden");
  });
  async function pollBridgeEvents() {
    const events = await invoke("poll_events");
    if (!Array.isArray(events)) return;
    for (const event of events) {
      if (event.type === "state" && event.state) renderState(event.state);
      if (event.type === "clock" && event.clock) document.getElementById("live-clock").textContent = event.clock;
      if (event.type === "toast") showToast(event.message, event.tone || "neutral");
      if (event.type === "error") showError(event.title || "操作失败", event.message || "请重试", event.detail || "");
    }
  }

  renderState(state);
  syncState();
  window.setInterval(syncState, 5000);
  window.setInterval(pollBridgeEvents, 500);
  window.setInterval(() => {
    if (state.clock && state.clock !== "00:00:00") {
      const now = new Date();
      document.getElementById("live-clock").textContent = now.toLocaleTimeString("zh-CN", { hour12: false });
    }
  }, 1000);
  window.addEventListener("pywebviewready", syncState);
})();
