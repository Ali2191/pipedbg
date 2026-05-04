let state = null;
let activeStep = null;
let isRunning = false;
let dagCache = {}; // Cache for DAG rendering

function qs(name) {
  const params = new URLSearchParams(window.location.search);
  return params.get(name);
}

function showError(message) {
  const panel = document.getElementById("errorPanel");
  const msgEl = document.getElementById("errorMessage");
  msgEl.textContent = message;
  panel.classList.remove("hidden");
  setTimeout(() => {
    panel.classList.add("hidden");
  }, 6000);
}

function closeError() {
  document.getElementById("errorPanel").classList.add("hidden");
}

function showLoading(show) {
  const panel = document.getElementById("loadingPanel");
  if (show) {
    panel.classList.remove("hidden");
  } else {
    panel.classList.add("hidden");
  }
}

async function api(path, options = {}) {
  try {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });

    const contentType = res.headers.get("content-type");
    let data;
    
    try {
      if (contentType && contentType.includes("application/json")) {
        data = await res.json();
      } else {
        data = await res.text();
      }
    } catch (e) {
      throw new Error("Failed to parse response: " + e.message);
    }

    if (!res.ok) {
      const error = typeof data === "object" ? data.error : data;
      const message = error || `HTTP ${res.status}`;
      const err = new Error(message);
      err.status = res.status;
      err.data = data;
      throw err;
    }

    return data;
  } catch (err) {
    console.error("API Error:", err);
    throw err;
  }
}

function timelineRow(item) {
  let statusClass = "ok";
  if (item.status === "FAILED" || item.status === "ERROR") {
    statusClass = "fail";
  } else if (item.status === "INFO") {
    statusClass = "info";
  }
  
  const errorText = item.error ? ` - ${item.error}` : "";
  return `<div class="timeline-item">${item.timestamp} - ${item.job} :: ${item.step} <span class="badge ${statusClass}">${item.status}</span>${errorText}</div>`;
}

function renderTimeline() {
  const box = document.getElementById("timeline");
  if (!state.timeline || state.timeline.length === 0) {
    box.innerHTML = "<div class='timeline-item'>No events yet. Click Run Pipeline.</div>";
    return;
  }
  
  // Limit timeline display to last 50 items for performance
  const visibleTimeline = state.timeline.slice(-50);
  box.innerHTML = visibleTimeline.map(timelineRow).join("\n");
  // Auto-scroll to bottom
  box.scrollTop = box.scrollHeight;
}

function renderStepInspector() {
  const stepInfo = document.getElementById("stepInfo");
  const envBox = document.getElementById("envBox");
  if (!activeStep) {
    stepInfo.textContent = "Select a step to inspect env vars and toggle breakpoints.";
    envBox.textContent = "";
    return;
  }

  const bp = state.breakpoints.includes(activeStep.id);
  stepInfo.innerHTML = `
    <strong>${activeStep.name}</strong> (id: ${activeStep.id})<br>
    Job: ${activeStep.jobName}<br>
    <button id="toggleBreakpoint" class="action-btn">${bp ? "Remove" : "Set"} breakpoint</button>
    <span class="badge ${bp ? "breakpoint" : ""}">${bp ? "breakpoint" : "no breakpoint"}</span>
  `;

  const merged = {
    ...(state.workflow.env || {}),
    ...(activeStep.jobEnv || {}),
    ...(activeStep.env || {}),
  };
  const lines = Object.keys(merged)
    .sort()
    .map((k) => `${k}=${merged[k]}`)
    .join("\n");
  envBox.textContent = lines || "No env vars for this step.";

  const btn = document.getElementById("toggleBreakpoint");
  if (btn) {
    btn.onclick = async () => {
      try {
        const enabled = !bp;
        await api("/api/breakpoint", {
          method: "POST",
          body: JSON.stringify({
            session_id: state.session_id,
            step_id: activeStep.id,
            enabled,
          }),
        });
        await loadState();
      } catch (err) {
        showError(`Breakpoint toggle failed: ${err.message}`);
      }
    };
  }
}

// Virtualized DAG rendering for performance
function renderDAG() {
  const dag = document.getElementById("dag");
  
  if (!state.workflow || !state.workflow.levels) {
    dag.innerHTML = "<div class='timeline-item'>Failed to load workflow.</div>";
    return;
  }

  // Fragment for batch DOM operations
  const fragment = document.createDocumentFragment();
  const numLevels = state.workflow.levels.length;
  
  state.workflow.levels.forEach((levelJobs, idx) => {
    const level = document.createElement("div");
    level.className = "level";
    level.innerHTML = `<h3>Level ${idx + 1}</h3>`;

    levelJobs.forEach((jobId) => {
      const job = state.workflow.jobs[jobId];
      if (!job) return;

      const jobEl = document.createElement("div");
      jobEl.className = "job";
      jobEl.innerHTML = `<strong>${job.name}</strong><span class='badge'>${job.runs_on}</span>`;

      const steps = job.steps || [];
      
      // Limit steps display for very large jobs
      const visibleSteps = steps.slice(0, 50);
      
      visibleSteps.forEach((step) => {
        const stepEl = document.createElement("div");
        const hasBp = state.breakpoints.includes(step.id);
        const isActive = activeStep && activeStep.id === step.id;
        
        stepEl.className = "step" + (hasBp ? " breakpoint" : "") + (isActive ? " active" : "");
        stepEl.textContent = step.name;
        
        // Add badges
        const badge1 = document.createElement("span");
        badge1.className = "badge";
        badge1.textContent = step.id;
        stepEl.appendChild(badge1);
        
        if (hasBp) {
          const badge2 = document.createElement("span");
          badge2.className = "badge breakpoint";
          badge2.textContent = "bp";
          stepEl.appendChild(badge2);
        }
        
        // Event delegation for performance
        stepEl.onclick = () => {
          activeStep = {
            ...step,
            jobName: job.name,
            jobEnv: job.env || {},
          };
          renderDAG();
          renderStepInspector();
        };
        
        jobEl.appendChild(stepEl);
      });

      // Show truncation notice if needed
      if (steps.length > 50) {
        const moreEl = document.createElement("div");
        moreEl.className = "step";
        moreEl.textContent = `+${steps.length - 50} more steps`;
        moreEl.style.opacity = "0.6";
        jobEl.appendChild(moreEl);
      }

      level.appendChild(jobEl);
    });

    fragment.appendChild(level);
  });

  dag.innerHTML = "";
  dag.appendChild(fragment);
}

async function loadState() {
  try {
    const session = qs("session");
    const path = session ? `/api/state?session=${encodeURIComponent(session)}` : "/api/state";
    state = await api(path);

    if (!state || !state.workflow) {
      showError("Failed to load workflow state");
      return;
    }

    document.getElementById("subtitle").textContent = `${state.workflow.name} (${state.workflow.platform}) - plan: ${state.plan.tier}`;
    renderDAG();
    renderStepInspector();
    renderTimeline();

    if (!qs("session")) {
      const u = new URL(window.location.href);
      u.searchParams.set("session", state.session_id);
      window.history.replaceState({}, "", u.toString());
    }
  } catch (err) {
    showError(`Failed to load state: ${err.message}`);
  }
}

async function runPipeline() {
  if (isRunning) return;
  
  try {
    isRunning = true;
    showLoading(true);
    
    await api("/api/run", {
      method: "POST",
      body: JSON.stringify({
        session_id: state.session_id,
        no_docker: true,
      }),
    });
    
    await loadState();
  } catch (err) {
    if (err.status === 402) {
      showError("AI explain limit reached. Create .pipedbg-team to unlock team mode.");
    } else {
      showError(`Pipeline execution failed: ${err.message}`);
    }
  } finally {
    isRunning = false;
    showLoading(false);
  }
}

function wireActions() {
  const runBtn = document.getElementById("runBtn");
  const shareBtn = document.getElementById("shareBtn");
  
  if (runBtn) {
    runBtn.onclick = runPipeline;
  }
  
  if (shareBtn) {
    shareBtn.onclick = async () => {
      try {
        if (navigator.clipboard) {
          await navigator.clipboard.writeText(state.share_url);
          showError("✓ Share URL copied to clipboard");
        } else {
          alert("Share URL: " + state.share_url);
        }
      } catch (err) {
        showError("Failed to copy: " + err.message);
      }
    };
  }
}

(async function boot() {
  try {
    wireActions();
    await loadState();
  } catch (err) {
    showError(`Boot failed: ${err.message}`);
    console.error("Boot error:", err);
  }
})();
