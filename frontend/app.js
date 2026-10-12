(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const el = {
    setup:    $("setup"),
    run:      $("run"),
    hits:     $("hits"),
    combo:    $("combo"),
    devices:  $("devices"),
    tgToken:  $("tgToken"),
    tgChat:   $("tgChat"),
    start:    $("start"),
    stop:     $("stop"),
    reset:    $("reset"),
    dlValid:  $("dlValid"),
    dlFull:   $("dlFull"),
    err:      $("err"),
    log:      $("log"),
    hitList:  $("hitList"),
    cProg:    $("cProg"),
    cValid:   $("cValid"),
    cInvalid: $("cInvalid"),
    cError:   $("cError"),
  };

  let state = {
    ws: null,
    jobId: null,
    total: 0,
    valid: 0,
    invalid: 0,
    error: 0,
    processed: 0,
    hits: [],
  };

  function showError(msg) {
    el.err.textContent = msg;
    el.err.hidden = false;
  }
  function clearError() {
    el.err.hidden = true;
    el.err.textContent = "";
  }

  function appendLog(text, kind = "info") {
    const span = document.createElement("span");
    span.className = "line " + (kind || "info");
    span.textContent = text;
    el.log.appendChild(span);
    el.log.scrollTop = el.log.scrollHeight;
  }

  function updateCounters() {
    el.cProg.textContent = `${state.processed} / ${state.total}`;
    el.cValid.textContent = String(state.valid);
    el.cInvalid.textContent = String(state.invalid);
    el.cError.textContent = String(state.error);
  }

  function renderHit(hit) {
    const wrap = document.createElement("div");
    wrap.className = "hit";
    const cred = document.createElement("span");
    cred.className = "cred";
    cred.textContent = `${hit.user}:${hit.pw}`;
    wrap.appendChild(cred);
    if (hit.pipe) {
      const info = document.createElement("div");
      info.className = "info";
      info.textContent = hit.pipe;
      wrap.appendChild(info);
    }
    el.hitList.appendChild(wrap);
    el.hits.hidden = false;
  }

  function resetView() {
    el.log.innerHTML = "";
    el.hitList.innerHTML = "";
    el.hits.hidden = true;
    state.processed = 0;
    state.valid = 0;
    state.invalid = 0;
    state.error = 0;
    updateCounters();
  }

  function setRunning(running) {
    el.start.disabled = running;
    el.stop.disabled = !running;
  }

  function connectWs(jobId) {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${location.host}/ws/${jobId}`);
    state.ws = ws;

    ws.addEventListener("message", (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); }
      catch { return; }
      handleEvent(msg);
    });

    ws.addEventListener("close", () => {
      setRunning(false);
    });

    ws.addEventListener("error", () => {
      appendLog("[ws] connection error", "warn");
    });
  }

  function handleEvent(msg) {
    const t = msg.type;

    if (t === "snapshot") {
      const d = msg.data || {};
      state.total = d.total || 0;
      state.valid = d.valid || 0;
      state.invalid = d.invalid || 0;
      state.error = d.error || 0;
      state.processed = d.processed || 0;
      updateCounters();
      if (Array.isArray(d.valid_hits)) {
        d.valid_hits.forEach(renderHit);
      }
      if (Array.isArray(d.log)) {
        d.log.forEach((l) => appendLog(l.text, l.kind));
      }
      return;
    }

    if (t === "start") {
      state.total = msg.total || 0;
      updateCounters();
      appendLog(`starting ${state.total} accounts`, "done");
      return;
    }

    if (t === "progress") {
      state.processed = msg.index || state.processed;
      updateCounters();
      return;
    }

    if (t === "log") {
      appendLog(msg.text || "", msg.kind || "info");
      return;
    }

    if (t === "result") {
      if (msg.label === "VALID") {
        state.valid += 1;
        appendLog(`[VALID] ${msg.user}:${msg.pw}  ${msg.status || ""}`, "ok");
      } else {
        state.invalid += 1;
        appendLog(`[INVALID] ${msg.user}:${msg.pw}  ${msg.status || ""}`, "dim");
      }
      updateCounters();
      return;
    }

    if (t === "valid") {
      const hit = { user: msg.user, pw: msg.pw, pipe: msg.pipe || "" };
      state.hits.push(hit);
      renderHit(hit);
      return;
    }

    if (t === "error") {
      state.error += 1;
      updateCounters();
      appendLog(`[ERR] ${msg.user ? msg.user + " — " : ""}${msg.msg || ""}`, "warn");
      return;
    }

    if (t === "done") {
      const status = msg.status || "done";
      appendLog(`=== ${status.toUpperCase()} ===`, "done");
      setRunning(false);
      if (state.valid > 0) {
        el.dlValid.disabled = false;
        el.dlFull.disabled = false;
      }
      return;
    }
  }

  async function startJob() {
    clearError();
    const combolistText = el.combo.value || "";
    if (!combolistText.trim()) {
      showError("paste a combolist first");
      return;
    }

    resetView();
    setRunning(true);

    const fd = new FormData();
    fd.append("combolist_text", combolistText);
    if (el.devices.files && el.devices.files[0]) {
      fd.append("devices", el.devices.files[0]);
    }
    fd.append("telegram_token", el.tgToken.value || "");
    fd.append("telegram_chat", el.tgChat.value || "");

    let data;
    try {
      const res = await fetch("/api/jobs", { method: "POST", body: fd });
      data = await res.json();
      if (!res.ok) {
        showError(data.error || "failed to create job");
        setRunning(false);
        return;
      }
    } catch (e) {
      showError("network error: " + e.message);
      setRunning(false);
      return;
    }

    state.jobId = data.job_id;
    state.total = data.total || 0;
    updateCounters();

    el.run.hidden = false;
    appendLog(`job ${state.jobId} queued — ${state.total} accounts`, "done");

    connectWs(state.jobId);
  }

  async function stopJob() {
    if (!state.jobId) return;
    try {
      await fetch(`/api/jobs/${state.jobId}/stop`, { method: "POST" });
    } catch {}
    appendLog("[stop] requested", "warn");
  }

  function resetRun() {
    if (state.ws) {
      try { state.ws.close(); } catch {}
      state.ws = null;
    }
    state.jobId = null;
    el.run.hidden = true;
    el.dlValid.disabled = true;
    el.dlFull.disabled = true;
    setRunning(false);
    resetView();
  }

  function download(kind) {
    if (!state.jobId) return;
    const url = `/api/jobs/${state.jobId}/download/${kind}`;
    window.open(url, "_blank");
  }

  el.start.addEventListener("click", startJob);
  el.stop.addEventListener("click", stopJob);
  el.reset.addEventListener("click", resetRun);
  el.dlValid.addEventListener("click", () => download("valid"));
  el.dlFull.addEventListener("click", () => download("full"));

  updateCounters();
})();
