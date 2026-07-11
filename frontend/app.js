const state = {
  currentRunId: null,
  pollTimer: null,
  activeProvider: null,
  providers: [],
};

const el = (id) => document.getElementById(id);

async function loadConfig() {
  const res = await fetch("/api/config");
  const cfg = await res.json();
  state.providers = cfg.providers;

  const select = el("synthesis-provider");
  select.innerHTML = "";
  for (const p of cfg.providers) {
    const opt = document.createElement("option");
    opt.value = p.key;
    opt.textContent = p.display_name;
    if (p.key === cfg.stage3.default_synthesis_provider) opt.selected = true;
    select.appendChild(opt);
  }
  el("stage2-mode").value = cfg.stage2.default_mode;

  renderModelSettings();
}

function renderModelSettings() {
  const container = el("model-settings-list");
  container.innerHTML = "";
  for (const p of state.providers) {
    const row = document.createElement("div");
    row.className = "model-row";
    row.innerHTML = `
      <label>${escapeHtml(p.display_name)}</label>
      <div class="model-row-controls">
        <input type="text" class="model-input" value="${escapeHtml(p.model)}" data-provider="${escapeHtml(p.key)}" />
        <button type="button" class="model-save-btn ghost-btn" data-provider="${escapeHtml(p.key)}">Save</button>
      </div>
      <span class="model-save-status" data-provider="${escapeHtml(p.key)}"></span>
    `;
    container.appendChild(row);
  }
  container.querySelectorAll(".model-save-btn").forEach((btn) => {
    btn.addEventListener("click", () => saveProviderModel(btn.dataset.provider));
  });
}

async function saveProviderModel(providerKey) {
  const input = document.querySelector(`.model-input[data-provider="${providerKey}"]`);
  const statusEl = document.querySelector(`.model-save-status[data-provider="${providerKey}"]`);
  const newModel = input.value.trim();
  if (!newModel) return;

  statusEl.textContent = "saving…";
  statusEl.className = "model-save-status";
  try {
    const res = await fetch(`/api/config/providers/${providerKey}/model`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: newModel }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      statusEl.textContent = `error: ${err.detail || res.statusText}`;
      statusEl.className = "model-save-status error";
      return;
    }
    const data = await res.json();
    const p = state.providers.find((p) => p.key === providerKey);
    if (p) p.model = data.model;
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
    li.innerHTML = `<span class="prompt-preview">${escapeHtml(run.prompt)}</span><span class="meta">${run.status}</span>`;
    li.addEventListener("click", () => viewRun(run.run_id));
    list.appendChild(li);
  }
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s ?? "";
  return d.innerHTML;
}

async function submitRun(ev) {
  ev.preventDefault();
  const prompt = el("prompt").value.trim();
  if (!prompt) return;

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
    await loadRunList();
    viewRun(run_id);
  } finally {
    el("submit-btn").disabled = false;
  }
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

  const terminal = data.run.status === "complete" || data.run.status === "failed";
  if (!terminal) {
    state.pollTimer = setTimeout(poll, 2500);
  }
}

function render(data) {
  const { run, stage1_responses, fact_check_results, synthesis, citation_verifications, cost_summary, cost_by_provider } = data;

  const statusBadge = el("status-badge");
  statusBadge.textContent = run.status;
  statusBadge.className = "badge " + (run.status === "complete" ? "complete" : run.status === "failed" ? "failed" : "");

  el("cost-summary").textContent =
    `stage1 $${cost_summary.stage1_usd.toFixed(4)} · stage2 $${cost_summary.stage2_usd.toFixed(4)} · ` +
    `stage3 $${cost_summary.stage3_usd.toFixed(4)} · total $${cost_summary.total_usd.toFixed(4)}`;

  el("resume-btn").classList.toggle("hidden", run.status !== "complete" && run.status !== "failed");
  el("resume-btn").onclick = () => resumeRun(run.run_id);

  renderBubbles(stage1_responses, cost_by_provider || {});
  renderSynthesis(synthesis, citation_verifications);
  renderFactChecks(fact_check_results);
  renderStage1(stage1_responses);
}

function fmtCost(n) {
  return `$${(n ?? 0).toFixed(4)}`;
}

function fmtTokens(stage) {
  if (!stage || (!stage.input_tokens && !stage.output_tokens)) return null;
  return `${stage.input_tokens}in / ${stage.output_tokens}out`;
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

    if (!p.has_api_key) {
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
      }
    }

    const bubble = document.createElement("div");
    bubble.className = `bubble ${stateClass}`;
    if (title) bubble.title = title;
    bubble.innerHTML = `<div class="bubble-header"><span class="bubble-dot"></span><span class="bubble-name">${escapeHtml(p.display_name)}</span><span class="bubble-label">${escapeHtml(label)}</span></div>`;

    const costs = costByProvider[p.key];
    if (costs) {
      const costLines = document.createElement("div");
      costLines.className = "bubble-costs";
      const stageLabels = [["stage1", "S1"], ["stage2", "S2"], ["stage3", "S3"]];
      for (const [key, short] of stageLabels) {
        const stage = costs[key];
        if (!stage || (!stage.input_tokens && !stage.output_tokens && !stage.cost_usd)) continue;
        const tokens = fmtTokens(stage);
        costLines.innerHTML += `<div class="bubble-cost-row"><span>${short}</span><span>${fmtCost(stage.cost_usd)}</span>${tokens ? `<span class="bubble-tokens">${tokens}</span>` : ""}</div>`;
      }
      if (costs.total && costs.total.cost_usd) {
        costLines.innerHTML += `<div class="bubble-cost-row total"><span>Total</span><span>${fmtCost(costs.total.cost_usd)}</span></div>`;
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
    const btn = document.createElement("button");
    btn.className = "tab-btn" + (r.provider === state.activeProvider ? " active" : "");
    btn.innerHTML = `<span class="status-dot ${r.status}"></span>${escapeHtml(r.provider)}`;
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
            <span>tokens: ${r.input_tokens ?? "?"} in / ${r.output_tokens ?? "?"} out</span>
            <span>cost: $${(r.cost_usd ?? 0).toFixed(4)}</span>
            <span>latency: ${r.latency_ms ? Math.round(r.latency_ms) + "ms" : "?"}</span>
          </div>
          <div class="answer-text">${escapeHtml(r.response_text)}</div>
          ${r.thinking_text ? `<details><summary>Show reasoning/thinking trace</summary><div class="thinking-text">${escapeHtml(r.thinking_text)}</div></details>` : ""}
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
loadConfig();
loadRunList();
