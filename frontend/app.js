const state = {
  currentRunId: null,
  pollTimer: null,
  activeProvider: null,
  providers: [],
  isRunning: false,
  docText: null,
  docFilename: null,
};

const TERMINAL_STATUSES = ["complete", "failed", "cancelled"];

const el = (id) => document.getElementById(id);

let defaultSynthesisProvider = null;

async function loadConfig() {
  const res = await fetch("/api/config");
  const cfg = await res.json();
  state.providers = cfg.providers;
  defaultSynthesisProvider = cfg.stage3.default_synthesis_provider;

  refreshSynthesisDropdown();
  el("stage2-mode").value = cfg.stage2.default_mode;

  renderModelSettings();
}

// Rebuilds only the "Synthesis model" <select> from state.providers, without
// touching the Model settings panel — used after toggling a provider's
// enabled state so an in-progress "saved" status message isn't wiped out by
// a full renderModelSettings() re-render a moment after it appears.
function refreshSynthesisDropdown() {
  const select = el("synthesis-provider");
  const previous = select.value;
  select.innerHTML = "";
  for (const p of state.providers) {
    if (!p.enabled) continue; // disabled vendors aren't offered as the synthesis model
    const opt = document.createElement("option");
    opt.value = p.key;
    opt.textContent = p.display_name;
    select.appendChild(opt);
  }
  if ([...select.options].some((o) => o.value === previous)) {
    select.value = previous;
  } else if ([...select.options].some((o) => o.value === defaultSynthesisProvider)) {
    select.value = defaultSynthesisProvider;
  }
}

function fmtRate(n) {
  return `$${Number(n).toFixed(2)}`;
}

function modelOptionsFor(p) {
  const options = [...p.available_models];
  if (!options.some((m) => m.id === p.model)) {
    // current model isn't in the curated catalog (e.g. set via the API
    // directly) — still show it as a selectable option so the dropdown
    // reflects reality instead of silently switching models on the user.
    options.unshift({ id: p.model, pricing: p.pricing });
  }
  return options;
}

function renderModelSettings() {
  const container = el("model-settings-list");
  container.innerHTML = "";
  for (const p of state.providers) {
    const options = modelOptionsFor(p);
    const optionsHtml = options
      .map((m) => {
        const broken = m.status && m.status !== "working";
        const label = `${m.id} — ${fmtRate(m.pricing.input_per_million)}/${fmtRate(m.pricing.output_per_million)} per M${broken ? ` (${m.status})` : ""}`;
        return `<option value="${escapeHtml(m.id)}" data-in="${m.pricing.input_per_million}" data-out="${m.pricing.output_per_million}" ${m.id === p.model ? "selected" : ""} ${broken ? "disabled" : ""}>${escapeHtml(label)}</option>`;
      })
      .join("");

    const lockedNote = p.sampling_locked
      ? `<div class="sampling-locked-note">Locked to default — ${escapeHtml(p.display_name)} rejects a custom temperature/top-p while its reasoning mode is enabled.</div>`
      : "";

    const row = document.createElement("div");
    row.className = "model-row";
    row.innerHTML = `
      <label class="row model-row-toggle">
        <input type="checkbox" class="provider-enabled-toggle" data-provider="${escapeHtml(p.key)}" ${p.enabled ? "checked" : ""} />
        <strong>${escapeHtml(p.display_name)}</strong>
      </label>

      <label class="field-label">Model</label>
      <select class="model-select" data-provider="${escapeHtml(p.key)}">${optionsHtml}</select>
      <div class="model-cost-preview" data-provider="${escapeHtml(p.key)}">${fmtRate(p.pricing.input_per_million)} in / ${fmtRate(p.pricing.output_per_million)} out per million tokens</div>

      <div class="sampling-row">
        <label class="field-label">Temperature <span class="hint">(default ${p.default_temperature ?? "—"})</span>
          <input type="number" class="temperature-input" data-provider="${escapeHtml(p.key)}" min="0" max="2" step="0.1" placeholder="${p.default_temperature ?? "default"}" value="${p.temperature ?? ""}" ${p.sampling_locked ? "disabled" : ""} />
        </label>
        <label class="field-label">Top-p <span class="hint">(default ${p.default_top_p ?? "—"})</span>
          <input type="number" class="top-p-input" data-provider="${escapeHtml(p.key)}" min="0" max="1" step="0.05" placeholder="${p.default_top_p ?? "default"}" value="${p.top_p ?? ""}" ${p.sampling_locked ? "disabled" : ""} />
        </label>
      </div>
      ${lockedNote}

      <div class="model-row-controls">
        <button type="button" class="model-save-btn ghost-btn" data-provider="${escapeHtml(p.key)}">Save</button>
        <span class="model-save-status" data-provider="${escapeHtml(p.key)}"></span>
      </div>
    `;
    container.appendChild(row);
  }

  container.querySelectorAll(".model-select").forEach((sel) => {
    sel.addEventListener("change", () => {
      const opt = sel.selectedOptions[0];
      const preview = document.querySelector(`.model-cost-preview[data-provider="${sel.dataset.provider}"]`);
      preview.textContent = `${fmtRate(opt.dataset.in)} in / ${fmtRate(opt.dataset.out)} out per million tokens`;
    });
  });
  container.querySelectorAll(".provider-enabled-toggle").forEach((cb) => {
    cb.addEventListener("change", () => toggleProviderEnabled(cb.dataset.provider, cb.checked));
  });
  container.querySelectorAll(".model-save-btn").forEach((btn) => {
    btn.addEventListener("click", () => saveProviderSettings(btn.dataset.provider));
  });
}

async function toggleProviderEnabled(providerKey, enabled) {
  const statusEl = document.querySelector(`.model-save-status[data-provider="${providerKey}"]`);
  try {
    const res = await fetch(`/api/config/providers/${providerKey}/enabled`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      statusEl.textContent = `error: ${err.detail || res.statusText}`;
      statusEl.className = "model-save-status error";
      return;
    }
    const data = await res.json();
    const p = state.providers.find((p) => p.key === providerKey);
    if (p) p.enabled = data.enabled;
    statusEl.textContent = data.enabled ? "enabled" : "disabled — skipped in future runs";
    statusEl.className = "model-save-status ok";
    refreshSynthesisDropdown(); // only enabled providers are offered as the synthesis model
  } catch (e) {
    statusEl.textContent = `error: ${e}`;
    statusEl.className = "model-save-status error";
  }
}

async function saveProviderSettings(providerKey) {
  const select = document.querySelector(`.model-select[data-provider="${providerKey}"]`);
  const tempInput = document.querySelector(`.temperature-input[data-provider="${providerKey}"]`);
  const topPInput = document.querySelector(`.top-p-input[data-provider="${providerKey}"]`);
  const statusEl = document.querySelector(`.model-save-status[data-provider="${providerKey}"]`);
  const newModel = select.value;
  const temperature = tempInput.value === "" ? null : Number(tempInput.value);
  const topP = topPInput.value === "" ? null : Number(topPInput.value);

  statusEl.textContent = "saving…";
  statusEl.className = "model-save-status";
  try {
    const modelRes = await fetch(`/api/config/providers/${providerKey}/model`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: newModel }),
    });
    const paramsRes = await fetch(`/api/config/providers/${providerKey}/params`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ temperature, top_p: topP }),
    });

    if (!modelRes.ok || !paramsRes.ok) {
      const failed = !modelRes.ok ? modelRes : paramsRes;
      const err = await failed.json().catch(() => ({ detail: failed.statusText }));
      statusEl.textContent = `error: ${err.detail || failed.statusText}`;
      statusEl.className = "model-save-status error";
      return;
    }

    const modelData = await modelRes.json();
    const paramsData = await paramsRes.json();
    const p = state.providers.find((p) => p.key === providerKey);
    if (p) {
      p.model = modelData.model;
      p.pricing = modelData.pricing;
      p.temperature = paramsData.temperature;
      p.top_p = paramsData.top_p;
    }
    statusEl.textContent = "saved — takes effect next run";
    statusEl.className = "model-save-status ok";
  } catch (e) {
    statusEl.textContent = `error: ${e}`;
    statusEl.className = "model-save-status error";
  }
}

async function loadRunList() {
  const res = await fetch("/api/runs");
  const runs = await res.json();
  const list = el("run-list");
  list.innerHTML = "";
  for (const run of runs) {
    const li = document.createElement("li");
    li.className = run.run_id === state.currentRunId ? "active" : "";
    li.innerHTML = `
      <div class="run-info">
        <span class="prompt-preview">${escapeHtml(run.prompt)}</span>
        <span class="meta">${run.status}</span>
      </div>
      <button type="button" class="run-delete-btn" title="Delete this run" data-run-id="${escapeHtml(run.run_id)}">✕</button>
    `;
    li.addEventListener("click", () => viewRun(run.run_id));
    list.appendChild(li);
  }
  list.querySelectorAll(".run-delete-btn").forEach((btn) => {
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation(); // don't also trigger the parent <li>'s viewRun click
      deleteRun(btn.dataset.runId);
    });
  });
}

async function deleteRun(runId) {
  if (!confirm("Delete this run? This can't be undone.")) return;

  const res = await fetch(`/api/runs/${runId}`, { method: "DELETE" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    alert(`Failed to delete run: ${err.detail || res.statusText}`);
    return;
  }

  if (state.currentRunId === runId) {
    // the run being viewed was just deleted — back out to the empty state
    state.currentRunId = null;
    if (state.pollTimer) clearTimeout(state.pollTimer);
    el("run-view").classList.add("hidden");
    el("empty-state").classList.remove("hidden");
  }
  await loadRunList();
}

async function uploadDocument() {
  const input = el("doc-file");
  const statusEl = el("doc-status");
  const file = input.files[0];
  state.docText = null;
  state.docFilename = null;
  if (!file) {
    statusEl.classList.add("hidden");
    return;
  }

  statusEl.classList.remove("hidden");
  statusEl.textContent = `Extracting ${file.name}…`;
  statusEl.className = "doc-status";

  const formData = new FormData();
  formData.append("file", file);
  try {
    const res = await fetch("/api/documents/extract", { method: "POST", body: formData });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      statusEl.textContent = `error: ${err.detail || res.statusText}`;
      statusEl.className = "doc-status error";
      return;
    }
    const data = await res.json();
    state.docText = data.text;
    state.docFilename = data.filename;
    statusEl.textContent = `${data.filename} attached (${data.char_count.toLocaleString()} chars)${data.truncated ? " — truncated" : ""}`;
    statusEl.className = "doc-status ok";
  } catch (e) {
    statusEl.textContent = `error: ${e}`;
    statusEl.className = "doc-status error";
  }
}

function clearDocument() {
  state.docText = null;
  state.docFilename = null;
  el("doc-file").value = "";
  el("doc-status").classList.add("hidden");
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s ?? "";
  return d.innerHTML;
}

async function submitRun(ev) {
  ev.preventDefault();

  if (state.isRunning) {
    await stopRun();
    return;
  }

  let prompt = el("prompt").value.trim();
  if (!prompt) return;
  if (state.docText) {
    prompt = `Attached document (${state.docFilename}):\n"""\n${state.docText}\n"""\n\n${prompt}`;
  }

  el("submit-btn").disabled = true;
  try {
    const res = await fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt,
        skip_stage2: el("skip-stage2").checked,
        stage2_mode: el("stage2-mode").value,
        synthesis_provider: el("synthesis-provider").value,
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      alert(`Failed to start run: ${err.detail || res.statusText}`);
      return;
    }
    const { run_id } = await res.json();
    // Optimistically flip to "Stop" right away — poll()/render() will keep
    // it in sync with the run's actual status from here.
    setRunning(true);
    clearDocument(); // it's baked into the prompt now — don't resend on the next run
    await loadRunList();
    viewRun(run_id);
  } finally {
    el("submit-btn").disabled = false;
  }
}

function setRunning(isRunning) {
  state.isRunning = isRunning;
  const btn = el("submit-btn");
  btn.textContent = isRunning ? "Stop" : "Run";
  btn.classList.toggle("stop-btn", isRunning);
}

async function stopRun() {
  if (!state.currentRunId) return;
  const btn = el("submit-btn");
  btn.disabled = true;
  try {
    const res = await fetch(`/api/runs/${state.currentRunId}/cancel`, { method: "POST" });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      alert(`Failed to stop run: ${err.detail || res.statusText}`);
    }
  } finally {
    btn.disabled = false;
  }
  poll();
}

function viewRun(runId) {
  state.currentRunId = runId;
  state.activeProvider = null;
  el("empty-state").classList.add("hidden");
  el("run-view").classList.remove("hidden");
  if (state.pollTimer) clearTimeout(state.pollTimer);
  poll();
  loadRunList();
}

async function poll() {
  if (!state.currentRunId) return;
  const res = await fetch(`/api/runs/${state.currentRunId}`);
  if (!res.ok) return;
  const data = await res.json();
  render(data);

  if (!TERMINAL_STATUSES.includes(data.run.status)) {
    state.pollTimer = setTimeout(poll, 2500);
  }
}

// Maps the run's raw status column to a human label and a badge style.
// Anything not in TERMINAL_STATUSES is an in-progress stage, shown in yellow.
const STATUS_LABELS = {
  pending: "Pending",
  running_stage1: "Stage 1: dispatching to providers",
  running_stage2: "Stage 2: fact-checking",
  running_stage3: "Stage 3: synthesizing",
  verifying_citations: "Verifying citations",
  complete: "Complete",
  failed: "Failed",
  cancelled: "Cancelled",
};

function render(data) {
  const { run, stage1_responses, fact_check_results, synthesis, citation_verifications, cost_summary, cost_by_provider, followup_messages } = data;

  const statusBadge = el("status-badge");
  statusBadge.textContent = STATUS_LABELS[run.status] || run.status;
  statusBadge.className = "badge " + (
    run.status === "complete" ? "complete"
      : run.status === "failed" ? "failed"
      : run.status === "cancelled" ? "cancelled"
      : "running"
  );

  el("cost-summary").textContent =
    `stage1 $${cost_summary.stage1_usd.toFixed(4)} · stage2 $${cost_summary.stage2_usd.toFixed(4)} · ` +
    `stage3 $${cost_summary.stage3_usd.toFixed(4)} · followup $${(cost_summary.followup_usd ?? 0).toFixed(4)} · ` +
    `total $${cost_summary.total_usd.toFixed(4)}`;

  const terminal = TERMINAL_STATUSES.includes(run.status);
  setRunning(!terminal);

  el("resume-btn").classList.toggle("hidden", !terminal);
  el("resume-btn").onclick = () => resumeRun(run.run_id);

  el("export-btn").classList.toggle("hidden", !terminal);
  el("export-btn").onclick = () => { window.location.href = `/api/runs/${run.run_id}/export`; };

  renderBubbles(stage1_responses, cost_by_provider || {});
  renderSynthesis(synthesis, citation_verifications);
  renderFollowup(synthesis, followup_messages || []);
  renderFactChecks(fact_check_results);
  renderStage1(stage1_responses);
}

function fmtCost(n) {
  return `$${(n ?? 0).toFixed(4)}`;
}

// Tokens are shown in millions (not raw counts) since pricing in
// providers.yaml is per-million-tokens — this keeps the displayed numbers
// directly comparable to the $/M rate that produced the cost figure.
function fmtTokensM(n) {
  if (n == null) return "?";
  return `${(n / 1_000_000).toFixed(6)}M`;
}

function fmtTokens(stage) {
  if (!stage || (!stage.input_tokens && !stage.output_tokens)) return null;
  return `${fmtTokensM(stage.input_tokens)} in / ${fmtTokensM(stage.output_tokens)} out`;
}

// Output-token throughput for the stage-1 response specifically (the one
// generation call with a single well-defined latency) — not meaningful to
// average across stage 2/3 calls that happened at different times.
function tokensPerSecond(stage1Response) {
  if (!stage1Response || stage1Response.status !== "ok") return null;
  const { output_tokens, latency_ms } = stage1Response;
  if (!output_tokens || !latency_ms) return null;
  return (output_tokens / (latency_ms / 1000)).toFixed(1);
}

function renderBubbles(stage1Responses, costByProvider) {
  const container = el("provider-bubbles");
  container.innerHTML = "";
  const byProvider = Object.fromEntries(stage1Responses.map((r) => [r.provider, r]));

  const providers = state.providers.length ? state.providers : stage1Responses.map((r) => ({
    key: r.provider, display_name: r.provider, has_api_key: true,
  }));

  for (const p of providers) {
    const r = byProvider[p.key];
    let stateClass = "pending";
    let label = "waiting…";
    let title = "";

    if (p.enabled === false) {
      stateClass = "no-key";
      label = "disabled";
    } else if (!p.has_api_key) {
      stateClass = "no-key";
      label = "no API key";
    } else if (r) {
      if (r.status === "running") {
        stateClass = "running";
        label = "thinking…";
      } else if (r.status === "ok") {
        stateClass = "ok";
        label = r.latency_ms ? `${Math.round(r.latency_ms)}ms` : "done";
      } else if (r.status === "timeout") {
        stateClass = "error";
        label = "timed out";
        title = r.error || "";
      } else if (r.status === "error") {
        stateClass = "error";
        label = "error";
        title = r.error || "";
      } else if (r.status === "cancelled") {
        stateClass = "no-key";
        label = "cancelled";
      }
    }

    const bubble = document.createElement("div");
    bubble.className = `bubble ${stateClass}`;
    if (title) bubble.title = title;
    const model = (r && r.model) || p.model;
    bubble.innerHTML = `
      <div class="bubble-header"><span class="bubble-dot"></span><span class="bubble-name">${escapeHtml(p.display_name)}</span><span class="bubble-label">${escapeHtml(label)}</span></div>
      ${model ? `<div class="bubble-model">${escapeHtml(model)}</div>` : ""}
    `;

    if (r) {
      // Any provider with a stage-1 row (running, ok, or errored) can be
      // clicked to jump straight to its full response/error below, instead
      // of making the user hunt for the matching tab.
      bubble.classList.add("clickable");
      bubble.title = title || "Click to see the full response";
      bubble.addEventListener("click", () => {
        state.activeProvider = p.key;
        renderStage1(stage1Responses);
        document.querySelector(".stage1-section").scrollIntoView({ behavior: "smooth", block: "start" });
      });
    }

    const costs = costByProvider[p.key];
    if (costs) {
      const costLines = document.createElement("div");
      costLines.className = "bubble-costs";
      const stageLabels = [["stage1", "S1"], ["stage2", "S2"], ["stage3", "S3"]];
      for (const [key, short] of stageLabels) {
        const stage = costs[key];
        if (!stage || (!stage.input_tokens && !stage.output_tokens && !stage.cost_usd)) continue;
        const tokens = fmtTokens(stage);
        costLines.innerHTML += `
          <div class="bubble-cost-row">
            <span>${short}</span><span>${fmtCost(stage.cost_usd)}</span>
          </div>
          ${tokens ? `<div class="bubble-tokens">${tokens}</div>` : ""}
        `;
      }
      if (costs.total && costs.total.cost_usd) {
        const tokPerSec = tokensPerSecond(r);
        costLines.innerHTML += `<div class="bubble-cost-row total"><span>Total</span><span>${fmtCost(costs.total.cost_usd)}</span>${tokPerSec ? `<span class="bubble-tok-per-sec">${tokPerSec} tok/s</span>` : ""}</div>`;
      }
      if (costLines.innerHTML) bubble.appendChild(costLines);
    }

    container.appendChild(bubble);
  }
}

function renderSynthesis(synthesis, citationVerifications) {
  const section = el("synthesis-section");
  if (!synthesis) {
    section.classList.add("hidden");
    return;
  }
  section.classList.remove("hidden");

  let text = synthesis.synthesis_text || "";
  if (synthesis.status !== "ok") {
    el("synthesis-text").innerHTML = `<span class="error-text">Synthesis ${synthesis.status}: ${escapeHtml(synthesis.error)}</span>`;
  } else {
    const verifiedUrls = new Set(citationVerifications.filter((c) => c.verified).map((c) => c.url));
    const removedUrls = new Set(citationVerifications.filter((c) => !c.verified).map((c) => c.url));
    let html = escapeHtml(text);
    for (const c of citationVerifications) {
      const escapedUrl = escapeHtml(c.url);
      const badge = c.verified ? " ✅" : " ❌";
      html = html.split(escapedUrl).join(`${escapedUrl}${badge}`);
    }
    el("synthesis-text").innerHTML = html;
  }

  const citationsDiv = el("citations");
  citationsDiv.innerHTML = "";
  if (citationVerifications.length === 0) {
    citationsDiv.innerHTML = '<div class="no-flags">No citations were output by the synthesis step.</div>';
  }
  for (const c of citationVerifications) {
    const div = document.createElement("div");
    div.className = "citation-badge " + (c.verified ? "verified" : "removed");
    const icon = c.verified ? "✅" : "❌";
    const statusText = c.http_status ? `HTTP ${c.http_status}` : (c.error || "unreachable");
    div.innerHTML = `<span class="icon">${icon}</span><span>${escapeHtml(c.url)}</span><span style="margin-left:auto;color:var(--muted)">${escapeHtml(statusText)}${c.found_in_sources ? " · in sources" : ""}</span>`;
    citationsDiv.appendChild(div);
  }
}

function renderFollowup(synthesis, followupMessages) {
  const section = el("followup-section");
  if (!synthesis || synthesis.status !== "ok") {
    section.classList.add("hidden");
    return;
  }
  section.classList.remove("hidden");

  const providerLabel = (state.providers.find((p) => p.key === synthesis.provider) || {}).display_name || synthesis.provider;

  const thread = el("followup-thread");
  thread.innerHTML = "";
  for (const m of followupMessages) {
    const div = document.createElement("div");
    if (m.role === "user") {
      div.className = "followup-msg followup-user";
      div.innerHTML = `<div class="followup-msg-label">You</div><div class="followup-msg-text">${escapeHtml(m.content)}</div>`;
    } else if (m.status === "ok") {
      div.className = "followup-msg followup-assistant";
      div.innerHTML = `<div class="followup-msg-label">${escapeHtml(providerLabel)}</div><div class="followup-msg-text">${escapeHtml(m.content)}</div>`;
    } else {
      div.className = "followup-msg followup-assistant";
      div.innerHTML = `<div class="followup-msg-label">${escapeHtml(providerLabel)}</div><div class="followup-msg-text error-text">${escapeHtml(m.status)}: ${escapeHtml(m.error || "")}</div>`;
    }
    thread.appendChild(div);
  }
  thread.scrollTop = thread.scrollHeight;
}

async function submitFollowup(ev) {
  ev.preventDefault();
  if (!state.currentRunId) return;

  const input = el("followup-input");
  const message = input.value.trim();
  if (!message) return;

  const btn = el("followup-submit");
  btn.disabled = true;
  input.disabled = true;
  try {
    const res = await fetch(`/api/runs/${state.currentRunId}/followup`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      alert(`Follow-up failed: ${err.detail || res.statusText}`);
      return;
    }
    input.value = "";
    await poll();
  } finally {
    btn.disabled = false;
    input.disabled = false;
    input.focus();
  }
}

function renderFactChecks(factChecks) {
  const container = el("factcheck-list");
  container.innerHTML = "";
  const withClaims = factChecks.filter((fc) => fc.status === "ok" && fc.claims && fc.claims.length > 0);

  if (factChecks.length === 0) {
    container.innerHTML = '<div class="no-flags">Fact-check stage skipped or not yet run.</div>';
    return;
  }
  if (withClaims.length === 0) {
    container.innerHTML = '<div class="no-flags">No claims were flagged by the fact-checkers.</div>';
    return;
  }

  for (const fc of withClaims) {
    const card = document.createElement("div");
    card.className = "factcheck-card";
    const claimRows = fc.claims
      .map(
        (c) => `<div class="claim-row">
          <span class="verdict ${c.verdict}">${escapeHtml(c.verdict)}</span>
          <span>${escapeHtml(c.claim)}</span>
          ${c.confidence != null ? `<span style="color:var(--muted)"> (confidence ${c.confidence})</span>` : ""}
          ${c.correction ? `<div class="correction">Suggested correction: ${escapeHtml(c.correction)}</div>` : ""}
        </div>`
      )
      .join("");
    card.innerHTML = `<div class="fc-header">${escapeHtml(fc.checker_provider)} reviewing ${escapeHtml(fc.subject_provider)}</div>${claimRows}`;
    container.appendChild(card);
  }
}

function renderStage1(responses) {
  const tabs = el("provider-tabs");
  const panels = el("provider-panels");
  tabs.innerHTML = "";
  panels.innerHTML = "";

  if (!state.activeProvider && responses.length > 0) {
    state.activeProvider = responses[0].provider;
  }

  for (const r of responses) {
    const p = state.providers.find((p) => p.key === r.provider);
    const displayName = p ? p.display_name : r.provider;

    const btn = document.createElement("button");
    btn.className = "tab-btn" + (r.provider === state.activeProvider ? " active" : "");
    if (r.model) btn.title = r.model;
    btn.innerHTML = `<span class="status-dot ${r.status}"></span>${escapeHtml(displayName)}`;
    btn.addEventListener("click", () => {
      state.activeProvider = r.provider;
      renderStage1(responses);
    });
    tabs.appendChild(btn);

    if (r.provider === state.activeProvider) {
      const panel = document.createElement("div");
      panel.className = "provider-panel";
      if (r.status !== "ok") {
        panel.innerHTML = `<div class="error-text">${escapeHtml(r.status)}: ${escapeHtml(r.error)}</div>`;
      } else {
        panel.innerHTML = `
          <div class="meta-row">
            <span>model: ${escapeHtml(r.model)}</span>
            <span>tokens: ${fmtTokensM(r.input_tokens)} in / ${fmtTokensM(r.output_tokens)} out</span>
            <span>cost: $${(r.cost_usd ?? 0).toFixed(4)}</span>
            <span>latency: ${r.latency_ms ? Math.round(r.latency_ms) + "ms" : "?"}</span>
          </div>
          ${r.thinking_text ? `
            <div class="answer-block">
              <div class="answer-block-label">Thinking</div>
              <div class="thinking-text">${escapeHtml(r.thinking_text)}</div>
            </div>
          ` : ""}
          <div class="answer-block">
            <div class="answer-block-label">Final answer</div>
            <div class="answer-text">${escapeHtml(r.response_text)}</div>
          </div>
        `;
      }
      panels.appendChild(panel);
    }
  }
}

async function resumeRun(runId) {
  await fetch(`/api/runs/${runId}/resume`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  poll();
}

el("run-form").addEventListener("submit", submitRun);
el("doc-file").addEventListener("change", uploadDocument);
el("followup-form").addEventListener("submit", submitFollowup);
el("followup-input").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey) {
    ev.preventDefault();
    el("followup-form").requestSubmit();
  }
});
loadConfig();
loadRunList();
