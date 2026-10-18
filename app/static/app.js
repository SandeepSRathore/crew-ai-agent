"use strict";

const $ = (id) => document.getElementById(id);

const STEP_COPY = {
  research: { running: "Researcher is searching the web and writing notes", empty: "Research notes will appear here." },
  coding: { running: "Coder is writing the Python program", empty: "The program will appear here once research is done." },
  review: { running: "Reviewer is checking the code", empty: "Review findings will appear here once the code is written." },
};
const SEVERITIES = ["critical", "major", "minor"];

const state = {
  runId: null,
  run: null,
  selectedStep: null, // null = follow the active step automatically
  source: null,
};

// ---- helpers --------------------------------------------------------------------------

function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function markdown(text) {
  return DOMPurify.sanitize(marked.parse(text || ""));
}

function duration(seconds) {
  const s = Math.max(0, Math.round(seconds));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}

function clock(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function relativeDay(ts) {
  const d = new Date(ts * 1000);
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  return sameDay ? `Today, ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function stepElapsed(step) {
  if (!step.started_at) return null;
  return (step.finished_at || Date.now() / 1000) - step.started_at;
}

async function api(path, options) {
  const resp = await fetch(path, options);
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const detail = body.detail;
    const message = typeof detail === "string" ? detail : detail?.message || (Array.isArray(detail) ? detail[0]?.msg : null);
    const error = new Error(message || `Request failed (${resp.status})`);
    error.status = resp.status;
    error.body = body;
    throw error;
  }
  return body;
}

// ---- health + history ---------------------------------------------------------------

async function loadHealth() {
  const el = $("health");
  try {
    const h = await api("/api/health");
    el.dataset.state = h.ok ? "ok" : "bad";
    el.textContent = h.ok ? `${h.model.replace(/^ollama\//, "")} on Ollama` : h.detail;
    el.title = h.detail;
  } catch {
    el.dataset.state = "bad";
    el.textContent = "Server not reachable";
  }
}

async function loadRuns() {
  const { runs, active_run_id } = await api("/api/runs");
  const list = $("run-list");
  list.innerHTML = runs
    .map(
      (r) => `<li><button type="button" data-run="${escapeHtml(r.id)}" aria-current="${r.id === state.runId}">
        <span class="run-dot" data-status="${escapeHtml(r.status)}" aria-label="${escapeHtml(r.status)}"></span>
        <span class="run-title">${escapeHtml(r.topic)}</span>
        <span class="run-when">${escapeHtml(relativeDay(r.created_at))}</span>
      </button></li>`
    )
    .join("");
  $("run-list-empty").hidden = runs.length > 0;
  return active_run_id;
}

// ---- opening a run + live stream ----------------------------------------------------

async function openRun(runId) {
  if (state.source) state.source.close();
  state.source = null;
  state.runId = runId;
  state.selectedStep = null;
  render(await api(`/api/runs/${encodeURIComponent(runId)}`));
  highlightHistory();
  if (state.run.status === "running") stream(runId);
}

function stream(runId) {
  const source = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events`);
  state.source = source;
  const onMessage = (e) => {
    if (state.runId !== runId) return;
    render(JSON.parse(e.data).run);
  };
  source.addEventListener("snapshot", onMessage);
  source.addEventListener("progress", onMessage);
  source.addEventListener("end", (e) => {
    onMessage(e);
    source.close();
    if (state.source === source) state.source = null;
    loadRuns();
    setRunning(false);
  });
  // EventSource reconnects on its own after network blips; the server replays a fresh snapshot.
}

function highlightHistory() {
  document.querySelectorAll("#run-list button").forEach((b) => b.setAttribute("aria-current", String(b.dataset.run === state.runId)));
}

function setRunning(isRunning) {
  $("run-button").disabled = isRunning;
  $("run-button").textContent = isRunning ? "Run in progress" : "Start run";
}

// ---- rendering ----------------------------------------------------------------------

function activeStepId(run) {
  if (run.current_step) return run.current_step;
  const failed = Object.values(run.steps).find((s) => s.status === "failed");
  if (failed) return failed.id;
  const done = Object.values(run.steps).filter((s) => s.status === "done");
  return done.length ? done[done.length - 1].id : "research";
}

function render(run) {
  if (!run) return;
  state.run = run;
  $("empty").hidden = true;
  $("run").hidden = false;
  setRunning(run.status === "running");

  $("run-topic").textContent = run.topic;
  renderStatusLine(run);
  renderTrack(run);
  $("warnings").innerHTML = (run.warnings || []).map((w) => `<p>${escapeHtml(w)}</p>`).join("");
  renderDetail(run);
}

function renderStatusLine(run) {
  const el = $("run-status");
  el.dataset.status = run.status;
  if (run.status === "running") {
    const step = run.current_step ? run.steps[run.current_step] : null;
    const elapsed = duration(Date.now() / 1000 - run.created_at);
    el.textContent = step ? `${STEP_COPY[step.id].running}. Running for ${elapsed}.` : `Starting the agents. Running for ${elapsed}.`;
  } else if (run.status === "done") {
    const issues = run.results.review?.issues?.length;
    const found = issues == null ? "The review is below." : issues === 0 ? "The reviewer found no problems." : `The reviewer found ${issues} problem${issues === 1 ? "" : "s"}.`;
    el.textContent = `Finished in ${duration(run.finished_at - run.created_at)}. ${found}`;
  } else {
    el.textContent = `Run failed: ${run.error || "unknown error"}`;
  }
}

function stepMeta(step) {
  const elapsed = stepElapsed(step);
  switch (step.status) {
    case "running": return `Working for ${duration(elapsed)}`;
    case "done": return `Done in ${duration(elapsed)}`;
    case "failed": return "Failed";
    default: return "Waiting";
  }
}

function renderTrack(run) {
  const selected = state.selectedStep || activeStepId(run);
  $("track").innerHTML = Object.values(run.steps)
    .map(
      (step, i) => `<li class="station" data-status="${step.status}">
        <button type="button" class="station-button" data-step="${step.id}" aria-selected="${step.id === selected}">
          <span class="station-node" aria-hidden="true">${step.status === "done" ? "✓" : i + 1}</span>
          <span class="station-name">${escapeHtml(step.agent)}</span>
          <span class="station-meta" data-meta="${step.id}">${stepMeta(step)}</span>
        </button>
      </li>`
    )
    .join("");
}

function renderDetail(run) {
  const stepId = state.selectedStep || activeStepId(run);
  const step = run.steps[stepId];
  const results = run.results;
  let actions = "";
  let body = "";

  if (step.status === "failed") {
    // The run error is the pipeline's explanation; the step error is CrewAI's raw message.
    body += `<div class="error-box">${escapeHtml(run.error || step.error || "This step failed.")}</div>`;
  }

  if (stepId === "research" && results.research) {
    body += `<div class="markdown">${markdown(results.research)}</div>`;
  } else if (stepId === "coding" && results.code) {
    actions = `<button type="button" data-action="copy">Copy code</button><button type="button" data-action="download">Download main.py</button>`;
    body += `<pre><code class="language-python">${escapeHtml(results.code)}</code></pre>`;
  } else if (stepId === "review" && (results.review || results.review_raw)) {
    body += renderReview(results.review, results.review_raw);
  } else if (step.status !== "failed") {
    const text = step.status === "running" ? `${STEP_COPY[stepId].running}…` : STEP_COPY[stepId].empty;
    body += `<p class="waiting" data-status="${step.status}">${escapeHtml(text)}</p>`;
  }

  if (step.activity.length) {
    body += `<ol class="activity" aria-label="Activity">${step.activity
      .map((a) => `<li><time>${clock(a.ts)}</time>${escapeHtml(a.text)}</li>`)
      .join("")}</ol>`;
  }

  const detail = $("detail");
  detail.innerHTML = `<div class="detail-head"><h2>${escapeHtml(step.label)}</h2><div class="actions">${actions}</div></div>${body}`;
  detail.querySelectorAll("pre code").forEach((el) => window.hljs && hljs.highlightElement(el));
}

function renderReview(review, raw) {
  if (!review) {
    return `<p class="raw-note">The reviewer didn't return structured findings, so here is its full answer.</p>
      <div class="markdown">${markdown(raw)}</div>`;
  }
  const counts = SEVERITIES.map((sev) => `<span><strong>${review.issues.filter((i) => i.severity === sev).length}</strong> ${sev}</span>`).join("");
  const order = (i) => SEVERITIES.indexOf(i.severity);
  const issues = [...review.issues].sort((a, b) => order(a) - order(b));
  const list = issues.length
    ? `<ol class="issues">${issues
        .map(
          (i) => `<li class="issue" data-severity="${escapeHtml(i.severity)}">
            <div class="issue-head">
              <span class="issue-sev">${escapeHtml(i.severity)}</span>
              <h3>${escapeHtml(i.title)}</h3>
              ${i.line_hint ? `<span class="issue-where">${escapeHtml(i.line_hint)}</span>` : ""}
            </div>
            <p>${escapeHtml(i.detail)}</p>
            <p class="fix"><strong>Fix:</strong> ${escapeHtml(i.suggestion)}</p>
          </li>`
        )
        .join("")}</ol>`
    : `<p class="summary">No problems were listed.</p>`;
  return `<div class="verdict">
      <span class="verdict-badge" data-verdict="${escapeHtml(review.verdict)}">${review.verdict === "approve" ? "Approved" : "Needs changes"}</span>
      <span class="severity-counts">${counts}</span>
    </div>
    <p class="summary">${escapeHtml(review.summary)}</p>
    ${list}`;
}

// Keep the "Working · 1m 05s" timers moving between events without re-rendering everything.
setInterval(() => {
  const run = state.run;
  if (!run || run.status !== "running") return;
  renderStatusLine(run);
  Object.values(run.steps).forEach((step) => {
    const el = document.querySelector(`[data-meta="${step.id}"]`);
    if (el) el.textContent = stepMeta(step);
  });
}, 1000);

// ---- events -------------------------------------------------------------------------

$("prompt-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const topic = $("topic").value.trim();
  const errorEl = $("form-error");
  errorEl.hidden = true;
  if (!topic) {
    errorEl.textContent = "Describe what the agents should build first.";
    errorEl.hidden = false;
    return;
  }
  setRunning(true);
  try {
    const { run_id } = await api("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ topic }),
    });
    await openRun(run_id);
    await loadRuns();
  } catch (err) {
    setRunning(false);
    if (err.status === 409 && err.body?.detail?.run_id) {
      errorEl.textContent = "A run is already in progress. Showing it now; start a new one when it finishes.";
      await openRun(err.body.detail.run_id);
      setRunning(true);
    } else {
      errorEl.textContent = `Couldn't start the run: ${err.message}`;
    }
    errorEl.hidden = false;
  }
});

$("track").addEventListener("click", (e) => {
  const button = e.target.closest("[data-step]");
  if (!button || !state.run) return;
  state.selectedStep = button.dataset.step;
  renderTrack(state.run);
  renderDetail(state.run);
});

$("detail").addEventListener("click", async (e) => {
  const action = e.target.closest("[data-action]")?.dataset.action;
  const code = state.run?.results.code;
  if (!action || !code) return;
  if (action === "copy") {
    await navigator.clipboard.writeText(code);
    e.target.textContent = "Copied";
    setTimeout(() => (e.target.textContent = "Copy code"), 1500);
  } else if (action === "download") {
    const url = URL.createObjectURL(new Blob([code], { type: "text/x-python" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: "main.py" });
    a.click();
    URL.revokeObjectURL(url);
  }
});

$("run-list").addEventListener("click", (e) => {
  const button = e.target.closest("[data-run]");
  if (button) openRun(button.dataset.run);
});

$("new-run").addEventListener("click", () => {
  $("topic").focus();
  $("topic").select();
});

(async function init() {
  loadHealth();
  const active = await loadRuns().catch(() => null);
  if (active) openRun(active);
})();
