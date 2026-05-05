let sessionId = null;
let sessionState = null;
let selectedJobId = null;
let selectedStepIndex = null;
let network = null;
let nodes = null;
let edges = null;
let stepLogs = {};
let timerInterval = null;
let startTime = null;
let isViewer = false;

const STATUS_COLORS = {
  pending: "#5b6b7a",
  running: "#f5a524",
  passed: "#31d17c",
  failed: "#f75f5f",
  canceled: "#9ab0c6",
};

function qs(name) {
  const params = new URLSearchParams(window.location.search);
  return params.get(name);
}

async function fetchJSON(url, options = {}) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return data;
}

function setStatus(status) {
  const badge = document.getElementById("statusBadge");
  badge.className = `badge ${status}`;
  badge.textContent = status;
}

function startTimer() {
  startTime = Date.now();
  if (timerInterval) clearInterval(timerInterval);
  timerInterval = setInterval(() => {
    const elapsed = Math.floor((Date.now() - startTime) / 1000);
    const mins = String(Math.floor(elapsed / 60)).padStart(2, "0");
    const secs = String(elapsed % 60).padStart(2, "0");
    document.getElementById("timer").textContent = `${mins}:${secs}`;
  }, 1000);
}

function stopTimer() {
  if (timerInterval) clearInterval(timerInterval);
  timerInterval = null;
}

async function loadSession() {
  sessionId = qs("session") || sessionId;
  const viewer = qs("viewer");
  const url = sessionId ? `/api/session?session=${encodeURIComponent(sessionId)}${viewer ? "&viewer=1" : ""}` : "/api/session";
  sessionState = await fetchJSON(url);
  sessionId = sessionState.session_id;
  isViewer = sessionState.viewer || viewer === "1";

  if (!qs("session")) {
    const u = new URL(window.location.href);
    u.searchParams.set("session", sessionId);
    window.history.replaceState({}, "", u.toString());
  }

  if (sessionState.workflow) {
    document.getElementById("workflowName").textContent = `${sessionState.workflow.name} (${sessionState.workflow.platform})`;
    renderDag(sessionState.workflow);
  }

  if (sessionState.status) {
    setStatus(sessionState.status);
  }

  renderSteps();
  updateControls();
}

async function loadWorkflows() {
  const data = await fetchJSON("/api/workflows");
  const select = document.getElementById("workflowSelect");
  select.innerHTML = "";
  data.workflows.forEach((wf) => {
    const opt = document.createElement("option");
    opt.value = wf;
    opt.textContent = wf;
    select.appendChild(opt);
  });

  if (sessionState && sessionState.workflow_path) {
    select.value = sessionState.workflow_path;
  }
}

function renderDag(workflow) {
  const container = document.getElementById("dag");
  nodes = new vis.DataSet();
  edges = new vis.DataSet();

  Object.values(workflow.jobs).forEach((job) => {
    nodes.add({
      id: job.id,
      label: job.name,
      color: STATUS_COLORS.pending,
      shape: "box",
      font: { color: "#e8eef6" },
    });
    (job.needs || []).forEach((dep) => {
      edges.add({ from: dep, to: job.id, arrows: "to" });
    });
  });

  network = new vis.Network(container, { nodes, edges }, {
    layout: { hierarchical: false },
    edges: { color: "#2b3f56" },
    physics: { enabled: true, stabilization: true },
  });

  network.on("click", (params) => {
    if (params.nodes.length) {
      selectedJobId = params.nodes[0];
      selectedStepIndex = null;
      renderSteps();
    }
  });
}

function renderSteps() {
  const stepList = document.getElementById("stepList");
  stepList.innerHTML = "";

  const jobs = sessionState.jobs || {};
  const jobId = selectedJobId || Object.keys(jobs)[0];
  if (!jobId || !jobs[jobId]) {
    stepList.innerHTML = "<div class='muted'>No steps</div>";
    return;
  }

  selectedJobId = jobId;
  document.getElementById("selectedJob").textContent = `Steps: ${jobs[jobId].name}`;

  jobs[jobId].steps.forEach((step, idx) => {
    const item = document.createElement("div");
    item.className = `step-item ${step.status}`;
    item.innerHTML = `<span>${step.name}</span><span class='muted'>${step.duration ? step.duration.toFixed(1) + "s" : ""}</span>`;
    item.onclick = () => {
      selectedStepIndex = idx;
      renderLogs();
    };
    stepList.appendChild(item);
  });
}

function renderLogs() {
  const logPanel = document.getElementById("logPanel");
  const logStatus = document.getElementById("logStatus");
  const jobs = sessionState.jobs || {};
  if (!selectedJobId || selectedStepIndex === null || !jobs[selectedJobId]) {
    logPanel.textContent = "Select a step to view logs.";
    logStatus.textContent = "";
    return;
  }

  const step = jobs[selectedJobId].steps[selectedStepIndex];
  const logs = (stepLogs[selectedJobId] || {})[selectedStepIndex] || [];
  document.getElementById("selectedStep").textContent = step.name;
  logStatus.textContent = step.status || "";
  logPanel.textContent = logs.join("\n");
  logPanel.scrollTop = logPanel.scrollHeight;
}

function updateNode(jobId, status) {
  if (!nodes) return;
  const color = STATUS_COLORS[status] || STATUS_COLORS.pending;
  nodes.update({ id: jobId, color });
}

function handleEvent(event) {
  const type = event.type;
  if (type === "job_start") {
    updateNode(event.job_id, "running");
  }
  if (type === "job_end") {
    updateNode(event.job_id, event.status);
  }
  if (type === "step_start" || type === "step_end") {
    if (sessionState.jobs[event.job_id]) {
      const step = sessionState.jobs[event.job_id].steps[event.step_index];
      if (step) {
        step.status = type === "step_start" ? "running" : event.status;
        step.duration = event.duration || step.duration;
      }
    }
    renderSteps();
  }
  if (type === "log") {
    stepLogs[event.job_id] = stepLogs[event.job_id] || {};
    stepLogs[event.job_id][event.step_index] = stepLogs[event.job_id][event.step_index] || [];
    stepLogs[event.job_id][event.step_index].push(event.line);
    if (event.job_id === selectedJobId && event.step_index === selectedStepIndex) {
      renderLogs();
    }
  }
  if (type === "breakpoint_hit") {
    showBreakpointBar(event);
  }
  if (type === "breakpoint_resume") {
    hideBreakpointBar();
  }
  if (type === "pipeline_end") {
    setStatus(event.status || "idle");
    stopTimer();
  }
}

function showBreakpointBar(event) {
  if (isViewer) return;
  const bar = document.getElementById("breakpointBar");
  bar.classList.remove("hidden");
  document.getElementById("breakpointMsg").textContent = `Breakpoint: ${event.step_name}`;
  bar.classList.add("pulse");
}

function hideBreakpointBar() {
  const bar = document.getElementById("breakpointBar");
  bar.classList.add("hidden");
  bar.classList.remove("pulse");
}

function updateControls() {
  document.getElementById("runBtn").disabled = isViewer;
  document.getElementById("cancelBtn").disabled = isViewer;
}

async function runPipeline() {
  if (isViewer) return;
  const workflow = document.getElementById("workflowSelect").value;
  await fetchJSON("/api/run", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, workflow }),
  });
  setStatus("running");
  startTimer();
}

async function cancelPipeline() {
  if (isViewer) return;
  await fetchJSON("/api/cancel", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId }),
  });
}

async function handleShare() {
  const lic = await fetchJSON("/api/license");
  if (lic.tier !== "pro") {
    alert("Session sharing is a Pro feature.");
    return;
  }

  const shareUrl = sessionState.share_url || "";
  if (!shareUrl) {
    alert("Share URL not available. Start with pipedbg share.");
    return;
  }
  await navigator.clipboard.writeText(shareUrl);
  alert("Share URL copied to clipboard.");
}

async function initShareButton() {
  const shareBtn = document.getElementById("shareBtn");
  const lic = await fetchJSON("/api/license");
  if (lic.tier !== "pro") {
    shareBtn.disabled = true;
    shareBtn.title = "Pro feature: session sharing";
  }
}

function connectWebSocket() {
  const ws = new WebSocket(`ws://${window.location.host}/ws`);
  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      handleEvent(data);
    } catch (e) {
      console.error("Bad WS message", e);
    }
  };
}

function wireActions() {
  document.getElementById("runBtn").onclick = runPipeline;
  document.getElementById("cancelBtn").onclick = cancelPipeline;
  document.getElementById("shareBtn").onclick = handleShare;
  document.getElementById("resumeBtn").onclick = async () => {
    await fetchJSON("/api/breakpoint/resume", { method: "POST", body: JSON.stringify({ session_id: sessionId }) });
    hideBreakpointBar();
  };
  document.getElementById("skipBtn").onclick = async () => {
    await fetchJSON("/api/breakpoint/skip", { method: "POST", body: JSON.stringify({ session_id: sessionId }) });
    hideBreakpointBar();
  };
}

async function boot() {
  await loadSession();
  await loadWorkflows();
  await initShareButton();
  wireActions();
  connectWebSocket();
}

boot();
