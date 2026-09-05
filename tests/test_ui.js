// Comprehensive regression test suite for ui/app.js
const fs = require("fs");
const path = require("path");
const assert = require("assert");
const vm = require("vm");

console.log("Running UI regression tests...");

const appJsPath = path.resolve(__dirname, "../ui/app.js");
const appJsContent = fs.readFileSync(appJsPath, "utf-8");

// 1. Static checks on app.js code
assert(appJsContent.includes('["mp3", "wav", "flac", "ogg"]'), "Supported formats should be defined");
assert(appJsContent.includes('action === "close"'), "Close action should be handled in click listener");
assert(appJsContent.includes('invoke("close")'), "Close button must call invoke('close') instead of 'hide'");
assert(appJsContent.includes("taskWritePending"), "taskWritePending mutex should be present");
assert(appJsContent.includes("saveWizardTimeInputs"), "saveWizardTimeInputs should be present");
assert(appJsContent.includes("格式不支持"), "Unsupported format tag should be present in renderWizard");

// 2. Test mockApi implementation directly extracted from app.js
const mockApiMatch = appJsContent.match(/function createMockApi\(\) \{([\s\S]*?)\n  \}/);
assert(mockApiMatch, "createMockApi should be extractable");

const emptyStateMatch = appJsContent.match(/function emptyState\(\) \{([\s\S]*?)\n  \}/);
assert(emptyStateMatch, "emptyState should be extractable");

const fileName = (p) => String(p || "").replaceAll("\\", "/").split("/").pop();

const createMockApi = new Function("fileName", `
  ${emptyStateMatch[0]}
  ${mockApiMatch[0]}
  return createMockApi();
`);

const api = createMockApi(fileName);

(async () => {
  // Check initial state
  const stateRes = await api.get_state();
  assert(stateRes.ok, "get_state should succeed");
  assert.strictEqual(stateRes.state.tasks_revision, 0, "initial tasks_revision should be 0");
  const initialCount = stateRes.state.tasks.length;

  // Test close() exists
  assert(typeof api.close === "function", "api.close must exist");
  const closeRes = await api.close();
  assert(closeRes.ok, "api.close() should succeed");

  // Test save_task revision mismatch
  const badSave = await api.save_task({
    expected_revision: 999,
    task: { time: "10:00", mode: "song", files: ["mp3/test.mp3"], weekdays: [0], name: "Bad" },
  });
  assert.strictEqual(badSave.ok, false, "save_task with stale revision should fail");
  assert.strictEqual(badSave.error, "任务列表已更新，请重新操作");

  // Test save_task correct revision
  const goodSave = await api.save_task({
    expected_revision: 0,
    task: { time: "08:00", mode: "song", files: ["mp3/test.mp3"], weekdays: [0], name: "Good" },
  });
  assert.strictEqual(goodSave.ok, true, "save_task with expected_revision should succeed");
  assert.strictEqual(goodSave.state.tasks_revision, 1, "tasks_revision should increment to 1");
  assert.strictEqual(goodSave.state.tasks.length, initialCount + 1);
  // Verify tasks are sorted by time
  assert.strictEqual(goodSave.state.tasks[0].time, "08:00:00");

  // Test delete_task stale revision
  const badDel = await api.delete_task(0, 0); // stale revision 0, current is 1
  assert.strictEqual(badDel.ok, false, "delete_task with stale revision should fail");
  assert.strictEqual(badDel.error, "任务列表已更新，请重新操作");

  // Test delete_task matching revision
  const goodDel = await api.delete_task(0, 1);
  assert.strictEqual(goodDel.ok, true, "delete_task with matching revision should succeed");
  assert.strictEqual(goodDel.state.tasks_revision, 2);

  // Test set_task_enabled stale revision
  const badEn = await api.set_task_enabled(0, false, 1);
  assert.strictEqual(badEn.ok, false, "set_task_enabled with stale revision should fail");

  const goodEn = await api.set_task_enabled(0, false, 2);
  assert.strictEqual(goodEn.ok, true, "set_task_enabled with matching revision should succeed");
  assert.strictEqual(goodEn.state.tasks_revision, 3);

  // 3. Test DOM simulation running actual app.js functions
  function createDomEnvironment() {
    const elements = {};
    function createElement(tag = "div", id = "") {
      const listeners = {};
      const attrs = {};
      const el = {
        tagName: tag.toUpperCase(),
        id,
        dataset: {},
        attributes: attrs,
        value: "",
        checked: false,
        disabled: false,
        title: "",
        innerHTML: "",
        textContent: "",
        className: "",
        classList: {
          _set: new Set(),
          add(c) { this._set.add(c); },
          remove(c) { this._set.delete(c); },
          toggle(c, force) {
            if (force !== undefined) { force ? this.add(c) : this.remove(c); }
            else { this._set.has(c) ? this.remove(c) : this.add(c); }
            return this._set.has(c);
          },
          contains(c) { return this._set.has(c); }
        },
        setAttribute(k, v) { attrs[k] = String(v); },
        removeAttribute(k) { delete attrs[k]; },
        getAttribute(k) { return attrs[k]; },
        addEventListener(evt, fn) { (listeners[evt] = listeners[evt] || []).push(fn); },
        async dispatchEvent(evt) {
          const list = listeners[evt.type] || [];
          for (const fn of list) {
            await fn(evt);
          }
        },
        querySelector(selector) {
          if (selector === "#start-time") return elements["start-time"];
          if (selector === "#end-time") return elements["end-time"];
          if (selector === "#next-day") return elements["next-day"];
          if (selector === "#time-feedback") return elements["time-feedback"];
          if (selector === "#task-name") return elements["task-name"];
          if (selector === '[data-action="create"]') return elements["create-btn"];
          if (selector.includes("field-error")) return elements["field-error"];
          return null;
        },
        querySelectorAll(selector) {
          if (selector === "[data-weekday]:checked") return elements["checked-weekdays"] || [];
          if (selector === "[data-view-button]") return [];
          return [];
        },
        closest(selector) {
          if (selector === "[data-dialog-action]" && this.dataset.dialogAction) return this;
          if (selector === "[data-time-adjust]" && this.dataset.timeAdjust) return this;
          if (selector === "[data-action]" && this.dataset.action) return this;
          if (selector === "[data-task-index]" && this.dataset.taskIndex !== undefined) return this;
          if (selector === "#tray-menu") return null;
          return null;
        },
        matches(selector) {
          if (selector === "[data-task-switch]") return Boolean(this.dataset.taskSwitch !== undefined);
          return false;
        },
        focus() {}
      };
      if (id) elements[id] = el;
      return el;
    }

    const appEl = createElement("div", "app");
    const backdropEl = createElement("div", "dialog-backdrop");
    const dialogEl = createElement("div", "dialog");
    const trayMenuEl = createElement("div", "tray-menu");
    const toastEl = createElement("div", "toast");
    const clockEl = createElement("div", "live-clock");
    const eyebrowEl = createElement("div", "now-eyebrow");
    const titleEl = createElement("div", "now-title");
    const detailEl = createElement("div", "now-detail");
    const taskListEl = createElement("div", "task-list");
    const libraryCountEl = createElement("div", "library-count");
    const libraryListEl = createElement("div", "library-list");
    const statusMsgEl = createElement("div", "status-message");
    const createBtnEl = createElement("button", "create-btn");
    createBtnEl.dataset.action = "create";
    elements["create-btn"] = createBtnEl;
    elements["main-content"] = createElement("div", "main-content");
    elements["tasks-view"] = createElement("div", "tasks-view");
    elements["library-view"] = createElement("div", "library-view");

    // Dynamic inner elements created by wizard
    const startTimeEl = createElement("input", "start-time");
    startTimeEl.value = "09:00";
    const endTimeEl = createElement("input", "end-time");
    endTimeEl.value = "13:00";
    const nextDayEl = createElement("input", "next-day");
    nextDayEl.checked = false;
    const timeFeedbackEl = createElement("div", "time-feedback");
    const taskNameEl = createElement("input", "task-name");
    taskNameEl.value = "My Task";

    const docListeners = {};
    const fakeDoc = {
      getElementById(id) { return elements[id] || null; },
      querySelector(s) { return appEl.querySelector(s); },
      querySelectorAll() { return []; },
      addEventListener(evt, fn) { (docListeners[evt] = docListeners[evt] || []).push(fn); },
      async dispatchEvent(evt) {
        for (const fn of docListeners[evt.type] || []) {
          await fn(evt);
        }
      },
      contains() { return true; },
      activeElement: null
    };

    const winListeners = {};
    const fakeWin = {
      document: fakeDoc,
      setTimeout(fn) { return 1; },
      clearTimeout() {},
      setInterval(fn) { return 1; },
      clearInterval() {},
      requestAnimationFrame(fn) { fn(); },
      addEventListener(evt, fn) { (winListeners[evt] = winListeners[evt] || []).push(fn); },
      location: { search: "" },
      pywebview: { api: {} }
    };

    return { fakeWin, fakeDoc, elements, appEl, dialogEl, backdropEl, createBtnEl, createElement };
  }

  const { fakeWin, fakeDoc, elements, appEl, dialogEl, backdropEl, createBtnEl, createElement } = createDomEnvironment();

  let bridgeCallbacks = {};
  fakeWin.pywebview.api = {
    get_state: async () => ({
      ok: true,
      state: {
        clock: "12:00:00",
        tasks: [
          { time: "09:00:00", mode: "song", name: "Task 1", weekdays: [0], files: ["mp3/a.mp3"], enabled: true },
        ],
        tasks_revision: 0,
        music_files: [{ path: "mp3/a.mp3", name: "a.mp3", folder: "mp3" }],
        playback: { active: false },
        status: { message: "就绪", tone: "neutral" },
        store: { load_state: "ready", read_only: false }
      }
    }),
    save_task: async (payload) => {
      if (bridgeCallbacks.save_task) return bridgeCallbacks.save_task(payload);
      return { ok: true, state: { tasks_revision: 1, tasks: [], music_files: [{ path: "mp3/a.mp3", name: "a.mp3", folder: "mp3" }] } };
    },
    delete_task: async (idx, rev) => {
      if (bridgeCallbacks.delete_task) return bridgeCallbacks.delete_task(idx, rev);
      return { ok: true, state: { tasks_revision: rev + 1, tasks: [], music_files: [{ path: "mp3/a.mp3", name: "a.mp3", folder: "mp3" }] } };
    },
    set_task_enabled: async (idx, en, rev) => {
      if (bridgeCallbacks.set_task_enabled) return bridgeCallbacks.set_task_enabled(idx, en, rev);
      return { ok: true, state: { tasks_revision: rev + 1, tasks: [], music_files: [{ path: "mp3/a.mp3", name: "a.mp3", folder: "mp3" }] } };
    },
    poll_events: async () => []
  };

  // Run app.js inside VM context
  const context = vm.createContext({
    window: fakeWin,
    document: fakeDoc,
    console,
    URLSearchParams,
    Math,
    String,
    Number,
    Boolean,
    Array,
    JSON,
    Date
  });

  vm.runInContext(appJsContent, context);

  // Allow initial state sync to execute
  await new Promise((resolve) => setTimeout(resolve, 10));

  // Test A: createButton is enabled initially
  assert.strictEqual(createBtnEl.disabled, false, "createButton should be enabled initially");

  // Test B: Open wizard via create action
  await appEl.dispatchEvent({ type: "click", target: createBtnEl });
  assert.strictEqual(backdropEl.classList.contains("is-hidden"), false, "Wizard dialog should be open");

  // Test C: Wizard draft input preservation and stepper adjustments
  // Set start time to 15:42
  elements["start-time"].value = "15:42";
  const adjustBtn = createElement("button");
  adjustBtn.dataset.timeAdjust = "1";
  adjustBtn.dataset.timeTarget = "start-time";
  await appEl.dispatchEvent({ type: "click", target: adjustBtn });
  assert.strictEqual(elements["start-time"].value, "15:43", "Start time should adjust to 15:43");

  // Switch mode to duration
  elements["end-time"].value = "18:20";
  elements["next-day"].checked = true;
  const modeDuration = createElement("input");
  modeDuration.name = "play-mode";
  modeDuration.value = "duration";
  await appEl.dispatchEvent({ type: "change", target: modeDuration });

  // Switch mode back to song
  const modeSong = createElement("input");
  modeSong.name = "play-mode";
  modeSong.value = "song";
  await appEl.dispatchEvent({ type: "change", target: modeSong });

  // Switch mode back to duration: start_time, end_time, and next_day must be preserved!
  await appEl.dispatchEvent({ type: "change", target: modeDuration });
  assert.strictEqual(elements["start-time"].value, "15:43", "Start time 15:43 preserved across mode switch");
  assert.strictEqual(elements["end-time"].value, "18:20", "End time 18:20 preserved across mode switch");
  assert.strictEqual(elements["next-day"].checked, true, "Next-day preserved across mode switch");
  assert(dialogEl.innerHTML.includes('value="15:43"'), "dialog.innerHTML should contain preserved start time 15:43");
  assert(dialogEl.innerHTML.includes('value="18:20"'), "dialog.innerHTML should contain preserved end time 18:20");

  // Negative test: Clearing time input in step 0 must fail validation and stay on step 0
  const nextBtn = createElement("button");
  nextBtn.dataset.dialogAction = "next";

  // Clear start time to "" in duration mode
  elements["start-time"].value = "";
  await appEl.dispatchEvent({ type: "click", target: nextBtn });
  assert(dialogEl.innerHTML.includes("请输入有效的开始时间。"), "Clearing start time should show validation error");
  assert(dialogEl.innerHTML.includes("时间与模式"), "Wizard should remain on step 0 when start time is cleared");

  // Switch mode while start-time is cleared: empty value must be preserved and not resurrect old 15:43
  await appEl.dispatchEvent({ type: "change", target: modeSong });
  assert(dialogEl.innerHTML.includes('id="start-time" type="time" value=""'), "Start time empty string preserved across mode switch to song");
  await appEl.dispatchEvent({ type: "click", target: nextBtn });
  assert(dialogEl.innerHTML.includes("请输入有效的开始时间。"), "Clearing start time in song mode should show validation error");
  assert(dialogEl.innerHTML.includes("时间与模式"), "Wizard should remain on step 0 in song mode");

  // Switch back to duration mode: start-time should still be empty
  await appEl.dispatchEvent({ type: "change", target: modeDuration });
  assert(dialogEl.innerHTML.includes('id="start-time" type="time" value=""'), "Start time empty string preserved across mode switch back to duration");

  // Restore start time and clear end time in duration mode
  elements["start-time"].value = "15:43";
  elements["end-time"].value = "";
  await appEl.dispatchEvent({ type: "click", target: nextBtn });
  assert(dialogEl.innerHTML.includes("固定时长模式必须设置有效的结束时间。"), "Clearing end time in duration mode should show validation error");
  assert(dialogEl.innerHTML.includes("时间与模式"), "Wizard should remain on step 0 when end time is cleared");

  // Switch mode while end-time is cleared: empty value preserved and not resurrect old 18:20
  await appEl.dispatchEvent({ type: "change", target: modeSong });
  await appEl.dispatchEvent({ type: "change", target: modeDuration });
  assert(dialogEl.innerHTML.includes('id="end-time" type="time" value=""'), "End time empty string preserved across mode switch cycle");
  await appEl.dispatchEvent({ type: "click", target: nextBtn });
  assert(dialogEl.innerHTML.includes("固定时长模式必须设置有效的结束时间。"), "Clearing end time persists across mode toggle and shows error");

  // Restore end time
  elements["end-time"].value = "18:20";

  // Test D: Wizard error message persistence on save failure
  // Navigate to step 3 (finish)
  elements["checked-weekdays"] = [{ dataset: { weekday: "0" } }];
  await appEl.dispatchEvent({ type: "click", target: nextBtn }); // to step 1
  await appEl.dispatchEvent({ type: "click", target: nextBtn }); // to step 2
  await appEl.dispatchEvent({ type: "click", target: nextBtn }); // to step 3

  // Simulate save_task failure (e.g. revision mismatch)
  let savedPayload = null;
  bridgeCallbacks.save_task = async (payload) => {
    savedPayload = payload;
    return {
      ok: false,
      error: "任务列表已更新，请重新操作",
      state: { tasks_revision: 5, tasks: [] }
    };
  };

  const finishBtn = createElement("button");
  finishBtn.dataset.dialogAction = "finish";
  await appEl.dispatchEvent({ type: "click", target: finishBtn });

  assert.ok(savedPayload, "save_task should be called with payload");
  assert.strictEqual(savedPayload.task.time, "15:43", "Payload should preserve start time 15:43");
  assert.strictEqual(savedPayload.task.end_time, "18:20", "Payload should preserve end time 18:20");
  assert.strictEqual(savedPayload.task.end_next_day, true, "Payload should preserve end_next_day true");
  assert.strictEqual(savedPayload.task.mode, "duration", "Payload should preserve mode duration");

  // Verify wizard is STILL open (draft preserved)
  assert.strictEqual(backdropEl.classList.contains("is-hidden"), false, "Wizard must remain open on failure");
  // Verify error message is displayed in dialog HTML and was NOT wiped out
  assert(dialogEl.innerHTML.includes("任务列表已更新，请重新操作"), "Error message must be preserved and visible in wizard");
  // Verify createButton is enabled again after failure
  assert.strictEqual(createBtnEl.disabled, false, "createButton should be re-enabled after save finishes");

  // Test E: Double-click prevention in finishWizard
  let saveCalls = 0;
  bridgeCallbacks.save_task = async () => {
    saveCalls++;
    await new Promise((r) => setTimeout(r, 20));
    return { ok: true, state: { tasks_revision: 6, tasks: [], music_files: [{ path: "mp3/a.mp3", name: "a.mp3", folder: "mp3" }] } };
  };
  const p1 = appEl.dispatchEvent({ type: "click", target: finishBtn });
  const p2 = appEl.dispatchEvent({ type: "click", target: finishBtn }); // rapid double-click
  await Promise.all([p1, p2]);
  assert.strictEqual(saveCalls, 1, "Double-clicking finish must only invoke save_task once");

  // Test F: User cancellation during in-flight save
  await appEl.dispatchEvent({ type: "click", target: createBtnEl }); // re-open wizard
  await appEl.dispatchEvent({ type: "click", target: nextBtn });
  await appEl.dispatchEvent({ type: "click", target: nextBtn });
  await appEl.dispatchEvent({ type: "click", target: nextBtn });

  let saveFinished = false;
  bridgeCallbacks.save_task = async () => {
    await new Promise((r) => setTimeout(r, 30));
    saveFinished = true;
    return { ok: true, state: { tasks_revision: 7, tasks: [], music_files: [{ path: "mp3/a.mp3", name: "a.mp3", folder: "mp3" }] } };
  };
  const savePromise = appEl.dispatchEvent({ type: "click", target: finishBtn });
  // User cancels during wait
  const cancelBtn = createElement("button");
  cancelBtn.dataset.dialogAction = "cancel";
  await appEl.dispatchEvent({ type: "click", target: cancelBtn });
  assert.strictEqual(backdropEl.classList.contains("is-hidden"), true, "Wizard closed on cancel");
  await savePromise;
  assert.strictEqual(saveFinished, true, "Save completed without crashing after user cancel");
  assert.strictEqual(backdropEl.classList.contains("is-hidden"), true, "Wizard remains closed");

  // Test G: Delete and switch synchronize taskWritePending and createButton
  let deletePendingChecked = false;
  bridgeCallbacks.delete_task = async () => {
    // While delete is pending, verify createButton is disabled
    assert.strictEqual(createBtnEl.disabled, true, "createButton must be disabled while delete is pending");
    deletePendingChecked = true;
    return { ok: true, state: { tasks_revision: 8, tasks: [] } };
  };
  const deleteBtn = createElement("button");
  deleteBtn.dataset.action = "delete";
  deleteBtn.dataset.taskIndex = "0";
  await appEl.dispatchEvent({ type: "click", target: deleteBtn });
  assert.strictEqual(deletePendingChecked, true);
  assert.strictEqual(createBtnEl.disabled, false, "createButton must be re-enabled after delete completes");

  console.log("All UI regression tests passed!");
})();
