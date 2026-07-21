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
let defaultFactCheckers = [];

async function loadConfig() {
  const res = await fetch("/api/config");
  const cfg = await res.json();
  state.providers = cfg.providers;
  defaultSynthesisProvider = cfg.stage3.default_synthesis_provider;
  defaultFactCheckers = cfg.stage2.fact_checkers || [];

  refreshSynthesisDropdown();
  el("stage2-mode").value = cfg.stage2.default_mode;

  renderModelSettings();
  renderFactCheckerPicker();
  updateFactCheckersVisibility();
}

// One checkbox per enabled provider, pre-checked to match this deployment's
// configured default fact-checkers (pipeline.stage2.fact_checkers) — picks
// here override that default for the next run only, nothing is persisted.
function renderFactCheckerPicker() {
  const container = el("fact-checkers-list");
  const previouslyChecked = new Set(
    [...container.querySelectorAll(".fact-checker-checkbox:checked")].map((cb) => cb.value)
  );
  const isFirstRender = container.children.length === 0;
  const enabledKeys = state.providers.filter((p) => p.enabled).map((p) => p.key);
  // The deployment's configured default checkers might not even be enabled
  // right now (e.g. disabled for cost, or never had a key) — falling back
  // to "check everything enabled" instead of leaving every box unchecked
  // avoids designated-fact-checkers mode silently checking nothing.
  const defaultsStillAvailable = defaultFactCheckers.some((k) => enabledKeys.includes(k));
  container.innerHTML = "";
  for (const p of state.providers) {
    if (!p.enabled) continue; // disabled providers can't check anything
    let checked;
    if (isFirstRender) {
      checked = defaultsStillAvailable ? defaultFactCheckers.includes(p.key) : true;
    } else {
      checked = previouslyChecked.has(p.key);
    }
    const label = document.createElement("label");
    label.className = "row fact-checker-row";
    label.innerHTML = `
      <input type="checkbox" class="fact-checker-checkbox" value="${escapeHtml(p.key)}" ${checked ? "checked" : ""} />
      ${escapeHtml(p.display_name)}
    `;
    container.appendChild(label);
  }
}

function updateFactCheckersVisibility() {
  const isFullMesh = el("stage2-mode").value === "full_mesh";
  el("fact-checkers-field").classList.toggle("hidden", isFullMesh);
}

function getSelectedFactCheckers() {
  return [...document.querySelectorAll(".fact-checker-checkbox:checked")].map((cb) => cb.value);
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
    if (p.local) {
      container.appendChild(buildLocalModelRow(p));
      continue;
    }
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
      if (opt.dataset.in === undefined) return; // local rows: always $0, no live preview to update
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
  container.querySelectorAll(".local-model-refresh-btn").forEach((btn) => {
    btn.addEventListener("click", () => fetchLocalModels(btn.dataset.provider));
  });
  for (const p of state.providers) {
    if (p.local) fetchLocalModels(p.key); // best-effort — silently shows an error if the local server isn't reachable
  }
}

// Local providers (LM Studio, etc.) have no curated model catalog — what's
// available depends on what's currently loaded on that server, which can
// change mid-session. So instead of a static <select>, this row starts with
// just the currently-configured model and fills in the real list via a live
// fetch (see fetchLocalModels), with a manual refresh button for after
// swapping models on the server without reopening this panel.
function buildLocalModelRow(p) {
  const row = document.createElement("div");
  row.className = "model-row";
  row.innerHTML = `
    <label class="row model-row-toggle">
      <input type="checkbox" class="provider-enabled-toggle" data-provider="${escapeHtml(p.key)}" ${p.enabled ? "checked" : ""} />
      <strong>${escapeHtml(p.display_name)}</strong>
    </label>

    <label class="field-label">Model <span class="hint">(live from the local server)</span></label>
    <div class="local-model-row">
      <select class="model-select local-model-select" data-provider="${escapeHtml(p.key)}">
        <option value="${escapeHtml(p.model)}" selected>${escapeHtml(p.model)}</option>
      </select>
      <button type="button" class="local-model-refresh-btn ghost-btn" data-provider="${escapeHtml(p.key)}" title="Fetch available models from the local server">&#8635;</button>
    </div>
    <div class="model-cost-preview" data-provider="${escapeHtml(p.key)}">$0.00 in / $0.00 out per million tokens — local inference</div>
    <span class="local-model-status" data-provider="${escapeHtml(p.key)}"></span>

    <div class="sampling-row">
      <label class="field-label">Temperature <span class="hint">(default ${p.default_temperature ?? "—"})</span>
        <input type="number" class="temperature-input" data-provider="${escapeHtml(p.key)}" min="0" max="2" step="0.1" placeholder="${p.default_temperature ?? "default"}" value="${p.temperature ?? ""}" />
      </label>
      <label class="field-label">Top-p <span class="hint">(default ${p.default_top_p ?? "—"})</span>
        <input type="number" class="top-p-input" data-provider="${escapeHtml(p.key)}" min="0" max="1" step="0.05" placeholder="${p.default_top_p ?? "default"}" value="${p.top_p ?? ""}" />
      </label>
    </div>

    <div class="model-row-controls">
      <button type="button" class="model-save-btn ghost-btn" data-provider="${escapeHtml(p.key)}">Save</button>
      <span class="model-save-status" data-provider="${escapeHtml(p.key)}"></span>
    </div>
  `;
  return row;
}

async function fetchLocalModels(providerKey) {
  const select = document.querySelector(`.local-model-select[data-provider="${providerKey}"]`);
  const statusEl = document.querySelector(`.local-model-status[data-provider="${providerKey}"]`);
  if (!select || !statusEl) return;
  const currentValue = select.value;

  statusEl.textContent = "fetching models…";
  statusEl.className = "local-model-status";
  try {
    const res = await fetch(`/api/config/providers/${providerKey}/local-models`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      statusEl.textContent = `couldn't reach local server: ${err.detail || res.statusText}`;
      statusEl.className = "local-model-status error";
      return;
    }
    const data = await res.json();
    if (!data.models || data.models.length === 0) {
      statusEl.textContent = "reached the server, but it reported no loaded models";
      statusEl.className = "local-model-status error";
      return;
    }
    select.innerHTML = data.models
      .map((id) => `<option value="${escapeHtml(id)}" ${id === currentValue ? "selected" : ""}>${escapeHtml(id)}</option>`)
      .join("");
    if (![...select.options].some((o) => o.value === currentValue)) {
      // the currently-saved model isn't among what's loaded right now —
      // keep it in the list so hitting Save doesn't silently switch models
      const opt = document.createElement("option");
      opt.value = currentValue;
      opt.textContent = `${currentValue} (not currently loaded)`;
      opt.selected = true;
      select.prepend(opt);
    }
    statusEl.textContent = `${data.models.length} model(s) found`;
    statusEl.className = "local-model-status ok";
  } catch (e) {
    statusEl.textContent = `error: ${e}`;
    statusEl.className = "local-model-status error";
  }
}

// Bring-your-own-key management — deliberately its own modal (opened via
// the ⚙ button) rather than folded into "Model settings", since it's a
// deployment-level credential rather than a per-run tuning knob.
function renderApiKeySettings() {
  const container = el("api-key-settings-list");
  container.innerHTML = "";
  for (const p of state.providers) {
    if (p.local) continue; // local inference servers don't check a key at all
    const row = document.createElement("div");
    row.className = "api-key-settings-row";
    row.innerHTML = `
      <div class="api-key-settings-header">
        <strong>${escapeHtml(p.display_name)}</strong>
        <span class="hint api-key-hint" data-provider="${escapeHtml(p.key)}">${p.has_api_key ? "— currently set" : "— not set"}</span>
      </div>
      <div class="api-key-row">
        <input type="password" class="api-key-input" data-provider="${escapeHtml(p.key)}" placeholder="paste key to set/replace…" autocomplete="off" />
        <button type="button" class="api-key-save-btn ghost-btn" data-provider="${escapeHtml(p.key)}">Save key</button>
        <button type="button" class="api-key-clear-btn ghost-btn" data-provider="${escapeHtml(p.key)}" ${p.has_api_key ? "" : "disabled"}>Clear</button>
      </div>
      <span class="api-key-status" data-provider="${escapeHtml(p.key)}"></span>
    `;
    container.appendChild(row);
  }

  container.querySelectorAll(".api-key-save-btn").forEach((btn) => {
    btn.addEventListener("click", () => saveApiKey(btn.dataset.provider));
  });
  container.querySelectorAll(".api-key-clear-btn").forEach((btn) => {
    btn.addEventListener("click", () => clearApiKey(btn.dataset.provider));
  });
}

function openSettingsModal() {
  renderApiKeySettings();
  el("settings-modal").classList.remove("hidden");
}

function closeSettingsModal() {
  el("settings-modal").classList.add("hidden");
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
    renderFactCheckerPicker(); // ditto for the fact-checker checkboxes
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

// Updates the "currently set / not set" hint and the Clear button's
// disabled state for one provider's API key row in place, instead of a
// full renderModelSettings() re-render — the same reasoning as
// refreshSynthesisDropdown(): a full re-render right after an action would
// wipe out this row's own "key saved"/error status message a moment after
// it appears, and (worse) any other row's in-flight status too.
function updateApiKeyHint(providerKey, hasKey) {
  const hint = document.querySelector(`.api-key-hint[data-provider="${providerKey}"]`);
  if (hint) hint.textContent = hasKey ? "— currently set" : "— not set";
  const clearBtn = document.querySelector(`.api-key-clear-btn[data-provider="${providerKey}"]`);
  if (clearBtn) clearBtn.disabled = !hasKey;
}

async function saveApiKey(providerKey) {
  const input = document.querySelector(`.api-key-input[data-provider="${providerKey}"]`);
  const statusEl = document.querySelector(`.api-key-status[data-provider="${providerKey}"]`);
  const key = input.value.trim();
  if (!key) {
    statusEl.textContent = "paste a key first";
    statusEl.className = "api-key-status error";
    return;
  }

  statusEl.textContent = "saving…";
  statusEl.className = "api-key-status";
  try {
    const res = await fetch(`/api/config/providers/${providerKey}/api-key`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: key }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      statusEl.textContent = `error: ${err.detail || res.statusText}`;
      statusEl.className = "api-key-status error";
      return;
    }
    const data = await res.json();
    input.value = ""; // never leave the plaintext key sitting in the DOM after it's saved
    const p = state.providers.find((p) => p.key === providerKey);
    if (p) p.has_api_key = data.has_api_key;
    updateApiKeyHint(providerKey, data.has_api_key);
    statusEl.textContent = "key saved — takes effect immediately";
    statusEl.className = "api-key-status ok";
  } catch (e) {
    statusEl.textContent = `error: ${e}`;
    statusEl.className = "api-key-status error";
  }
}

async function clearApiKey(providerKey) {
  if (!confirm("Clear the saved API key for this provider? It'll stop working until a new key is set.")) return;

  const statusEl = document.querySelector(`.api-key-status[data-provider="${providerKey}"]`);
  statusEl.textContent = "clearing…";
  statusEl.className = "api-key-status";
  try {
    const res = await fetch(`/api/config/providers/${providerKey}/api-key`, { method: "DELETE" });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      statusEl.textContent = `error: ${err.detail || res.statusText}`;
      statusEl.className = "api-key-status error";
      return;
    }
    const data = await res.json();
    const p = state.providers.find((p) => p.key === providerKey);
    if (p) p.has_api_key = data.has_api_key;
    updateApiKeyHint(providerKey, data.has_api_key);
    statusEl.textContent = "cleared";
    statusEl.className = "api-key-status ok";
  } catch (e) {
    statusEl.textContent = `error: ${e}`;
    statusEl.className = "api-key-status error";
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

// Small hand-written Markdown -> HTML renderer for the synthesized answer.
// Supports just enough structure for typical LLM-generated prose: headings
// (# .. ######), bold/italic, inline code, unordered/ordered lists,
// paragraphs, and auto-linked bare URLs. All text content is run through
// escapeHtml() before any tag is layered on top, so raw LLM output can never
// inject live HTML/script into the page.
function renderMarkdownInline(escapedText) {
  const stash = [];
  // Placeholder must be a token real prose can't plausibly contain — plain
  // digits/spaces would collide with ordinary text (e.g. "rose 1 percent").
  const save = (html) => {
    const token = `@@MDSTASH${stash.length}@@`;
    stash.push(html);
    return token;
  };

  let out = escapedText;

  // Inline code spans — stashed first so their contents are immune to the
  // bold/italic/link passes below.
  out = out.replace(/`([^`]+)`/g, (_, code) => save(`<code>${code}</code>`));

  // Bare URLs. Only http(s) schemes are ever matched, and the href is given
  // its own quote-escaping pass (independent of escapeHtml's behavior)
  // before being placed inside an attribute, so an adversarial URL can never
  // break out of the href="" string.
  out = out.replace(/(https?:\/\/[^\s<>"']+)/g, (_, url) => {
    let cleanUrl = url;
    let trailing = "";
    const trailingMatch = cleanUrl.match(/[.,;:!?)\]]+$/);
    if (trailingMatch) {
      trailing = trailingMatch[0];
      cleanUrl = cleanUrl.slice(0, -trailing.length);
    }
    if (!cleanUrl) return url;
    const safeHref = cleanUrl.replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    return save(`<a href="${safeHref}" target="_blank" rel="noopener">${cleanUrl}</a>`) + trailing;
  });

  // Bold before italic so "**x**" isn't first read as two adjacent italics.
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/\*([^*]+)\*/g, "<em>$1</em>");
  out = out.replace(/_([^_]+)_/g, "<em>$1</em>");

  out = out.replace(/@@MDSTASH(\d+)@@/g, (_, idx) => stash[Number(idx)]);
  return out;
}

// A GFM table separator row, e.g. "|---|:--:|---|" or "---|---" — every
// character is one of |, :, -, or whitespace, and at least one "-" appears.
function isTableSeparatorLine(line) {
  return /-/.test(line) && /^[|:\-\s]+$/.test(line);
}

function splitTableRow(line) {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|")) s = s.slice(0, -1);
  return s.split("|").map((c) => c.trim());
}

function renderMarkdown(text) {
  if (!text) return "";
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks = [];
  let paragraphLines = [];

  const flushParagraph = () => {
    if (paragraphLines.length) {
      const combined = paragraphLines.join(" ").trim();
      if (combined) {
        blocks.push(`<p>${renderMarkdownInline(escapeHtml(combined))}</p>`);
      }
      paragraphLines = [];
    }
  };

  let i = 0;
  while (i < lines.length) {
    const trimmed = lines[i].trim();

    if (trimmed === "") {
      flushParagraph();
      i++;
      continue;
    }

    const headingMatch = trimmed.match(/^(#{1,6})\s+(.+)$/);
    if (headingMatch) {
      flushParagraph();
      const level = headingMatch[1].length;
      blocks.push(`<h${level}>${renderMarkdownInline(escapeHtml(headingMatch[2].trim()))}</h${level}>`);
      i++;
      continue;
    }

    if (trimmed.includes("|") && i + 1 < lines.length && isTableSeparatorLine(lines[i + 1].trim())) {
      flushParagraph();
      const headerCells = splitTableRow(trimmed);
      i += 2; // header row + separator row
      const bodyRows = [];
      while (i < lines.length && lines[i].trim() !== "" && lines[i].trim().includes("|") && !isTableSeparatorLine(lines[i].trim())) {
        bodyRows.push(splitTableRow(lines[i].trim()));
        i++;
      }
      const thead = `<thead><tr>${headerCells.map((c) => `<th>${renderMarkdownInline(escapeHtml(c))}</th>`).join("")}</tr></thead>`;
      const tbody = `<tbody>${bodyRows.map((r) => `<tr>${r.map((c) => `<td>${renderMarkdownInline(escapeHtml(c))}</td>`).join("")}</tr>`).join("")}</tbody>`;
      blocks.push(`<table>${thead}${tbody}</table>`);
      continue;
    }

    if (/^>\s?/.test(trimmed)) {
      flushParagraph();
      const quoteLines = [];
      while (i < lines.length && /^>\s?/.test(lines[i].trim())) {
        quoteLines.push(lines[i].trim().replace(/^>\s?/, ""));
        i++;
      }
      blocks.push(`<blockquote><p>${renderMarkdownInline(escapeHtml(quoteLines.join(" ").trim()))}</p></blockquote>`);
      continue;
    }

    if (/^[-*]\s+/.test(trimmed)) {
      flushParagraph();
      const items = [];
      while (i < lines.length && /^[-*]\s+/.test(lines[i].trim())) {
        const itemText = lines[i].trim().replace(/^[-*]\s+/, "");
        items.push(`<li>${renderMarkdownInline(escapeHtml(itemText))}</li>`);
        i++;
      }
      blocks.push(`<ul>${items.join("")}</ul>`);
      continue;
    }

    if (/^\d+[.)]\s+/.test(trimmed)) {
      flushParagraph();
      const items = [];
      while (i < lines.length && /^\d+[.)]\s+/.test(lines[i].trim())) {
        const itemText = lines[i].trim().replace(/^\d+[.)]\s+/, "");
        items.push(`<li>${renderMarkdownInline(escapeHtml(itemText))}</li>`);
        i++;
      }
      blocks.push(`<ol>${items.join("")}</ol>`);
      continue;
    }

    paragraphLines.push(trimmed);
    i++;
  }
  flushParagraph();

  return blocks.join("\n");
}

// A provider's model/temperature/top-p only takes effect once its own
// "Save" button in Model settings is clicked — but it's easy to change a
// dropdown, not notice the still-unsaved row, and hit the big Run button
// expecting the visibly-selected model to be used (it wouldn't be; the
// previously-saved one would run instead). Auto-saves anything left
// unsaved right before a run starts, so what's shown in Model settings is
// always what actually runs.
async function saveAllDirtyModelSettings() {
  const dirtyProviderKeys = [];
  document.querySelectorAll(".model-select[data-provider]").forEach((select) => {
    const providerKey = select.dataset.provider;
    const p = state.providers.find((pr) => pr.key === providerKey);
    if (!p) return;
    const tempInput = document.querySelector(`.temperature-input[data-provider="${providerKey}"]`);
    const topPInput = document.querySelector(`.top-p-input[data-provider="${providerKey}"]`);
    const currentTemp = tempInput && tempInput.value !== "" ? Number(tempInput.value) : null;
    const currentTopP = topPInput && topPInput.value !== "" ? Number(topPInput.value) : null;
    const dirty = select.value !== p.model || currentTemp !== (p.temperature ?? null) || currentTopP !== (p.top_p ?? null);
    if (dirty) dirtyProviderKeys.push(providerKey);
  });
  // One at a time, not Promise.all — providers.yaml is a single shared file
  // (locked server-side now, but there's still no reason to fire a pile of
  // concurrent writes to it when sequential is just as fast in practice).
  for (const providerKey of dirtyProviderKeys) {
    await saveProviderSettings(providerKey);
  }
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
    await saveAllDirtyModelSettings();
    const res = await fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt,
        skip_stage2: el("skip-stage2").checked,
        stage2_mode: el("stage2-mode").value,
        synthesis_provider: el("synthesis-provider").value,
        fact_checkers: getSelectedFactCheckers(),
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

// Loads a past run's prompt (and the settings it was run with) back into
// the sidebar form so it can be tweaked and submitted as a new run —
// "further interaction" with a recent run beyond just re-reading its
// results. Doesn't touch the current run being viewed; only the form.
function reusePrompt(run) {
  el("prompt").value = run.prompt;
  el("skip-stage2").checked = !!run.skip_stage2;
  if (run.stage2_mode) el("stage2-mode").value = run.stage2_mode;
  const synthesisSelect = el("synthesis-provider");
  if (run.synthesis_provider && [...synthesisSelect.options].some((o) => o.value === run.synthesis_provider)) {
    synthesisSelect.value = run.synthesis_provider;
  }
  if (run.fact_checkers) {
    document.querySelectorAll(".fact-checker-checkbox").forEach((cb) => {
      cb.checked = run.fact_checkers.includes(cb.value);
    });
  }
  updateFactCheckersVisibility();
  el("prompt").scrollIntoView({ behavior: "smooth", block: "center" });
  el("prompt").focus();
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

  el("run-prompt-text").textContent = run.prompt;
  el("reuse-prompt-btn").onclick = () => reusePrompt(run);

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
  renderFactChecks(fact_check_results, stage1_responses);
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
    // Badge citation URLs in the raw Markdown first (same substring-append
    // logic as before), then convert the whole thing to HTML — rather than
    // splicing badges into already-escaped/rendered HTML.
    let rawText = text;
    for (const c of citationVerifications) {
      const badge = c.verified ? " ✅" : " ❌";
      rawText = rawText.split(c.url).join(`${c.url}${badge}`);
    }
    el("synthesis-text").innerHTML = renderMarkdown(rawText);
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

  const thinkingSection = el("synthesis-thinking-section");
  if (synthesis.status === "ok" && synthesis.thinking_text) {
    thinkingSection.classList.remove("hidden");
    el("synthesis-thinking-text").textContent = synthesis.thinking_text;
  } else {
    thinkingSection.classList.add("hidden");
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

function renderFactChecks(factChecks, stage1Responses) {
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

  // Labeled by actual model name, not the provider key/slot (e.g.
  // "moonshot") — a slot's underlying model can be changed at any time, so
  // its key or current display name doesn't reliably say which model
  // actually did the reviewing for *this* run. subjectModel comes from
  // this run's own stage1 data; checkerModel is captured at fact-check
  // time and falls back to the provider key for runs from before that was
  // tracked.
  const modelByProvider = Object.fromEntries(stage1Responses.map((r) => [r.provider, r.model]));

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
    const checkerLabel = fc.checker_model || fc.checker_provider;
    const subjectLabel = modelByProvider[fc.subject_provider] || fc.subject_provider;
    card.innerHTML = `<div class="fc-header">${escapeHtml(checkerLabel)} reviewing ${escapeHtml(subjectLabel)}</div>${claimRows}`;
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
el("stage2-mode").addEventListener("change", updateFactCheckersVisibility);
el("doc-file").addEventListener("change", uploadDocument);
el("followup-form").addEventListener("submit", submitFollowup);
el("followup-input").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey) {
    ev.preventDefault();
    el("followup-form").requestSubmit();
  }
});
el("settings-btn").addEventListener("click", openSettingsModal);
el("settings-close-btn").addEventListener("click", closeSettingsModal);
el("settings-modal").addEventListener("click", (ev) => {
  if (ev.target.id === "settings-modal") closeSettingsModal(); // click landed on the backdrop, not the dialog
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && !el("settings-modal").classList.contains("hidden")) closeSettingsModal();
});
loadConfig();
loadRunList();
