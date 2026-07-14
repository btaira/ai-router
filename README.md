# ai-router

Ask one question, get answers from up to six different AI providers
(Anthropic, OpenAI, Google, DeepSeek, MiniMax, and Moonshot/Kimi) running in
their highest reasoning/thinking mode at once, have a configurable set of
those models cross-check each other's answers for factual errors, then get
one consolidated answer back — synthesized from whichever answer(s) actually
held up best, with every citation verified by a real HTTP request before
it's shown as trustworthy. The point is a second opinion you don't have to
assemble by hand: instead of asking one model and hoping, or copy-pasting
the same question into six tabs yourself, you get the disagreements
surfaced, the weak claims flagged, and one answer built from the strongest
evidence across all of them.

## Status

Working end-to-end: all three pipeline stages, citation verification, and
the full settings UI described below have been exercised against live
provider APIs, not just tests. All six providers ship **enabled by default**
(toggle any off in "Model settings" if you don't have a key for one — a
disabled or unconfigured provider is skipped cleanly rather than failing the
run). Every model in the catalog was verified against live vendor/OpenRouter
docs as of 2026-07-14 (see [`MODELS_STATUS.md`](MODELS_STATUS.md)) — one,
`gemini-2.5-pro`, was found deprecated and is blocked from selection. Every
vendor carries a 5th backup model beyond the 4 the UI guarantees, so a
single future deprecation (like that one) doesn't drop anyone below 4
working choices. 31 backend tests cover the pipeline logic, citation
verification, sampling overrides, and the config-editing endpoints.

## Pipeline

1. **Stage 1 — parallel dispatch.** The prompt is sent to all enabled
   providers concurrently (`asyncio.gather`, per-provider timeout and error
   isolation — one provider failing/timing out never blocks the others).
   Every request/response, token count, latency, and cost is logged to
   SQLite.
2. **Stage 2 — fact-check mesh.** Configurable via `pipeline.stage2.mode` in
   `backend/config/providers.yaml` (or a per-run override):
   - `designated_fact_checkers` (default) — a fixed list of models each
     review every stage-1 answer against the other five.
   - `full_mesh` — every stage-1 model reviews every answer, including its
     own. Most thorough, most expensive; use as a "deep audit" toggle.
   - `diff_then_check` — a cheap, non-LLM claim diff
     (`backend/app/pipeline/claim_diff.py`) flags claims with no close match
     in any other answer, and only those go to the fact-checkers for
     adjudication.

   Every fact-check call returns structured JSON
   (`{"claims": [{"claim", "verdict", "confidence", "correction"}]}`), never
   free text, so stage 3 can consume it mechanically. Set `skip_stage2` on a
   run to skip this stage entirely for a cheap/fast pass.
3. **Stage 3 — synthesis.** One designated provider
   (`pipeline.stage3.synthesis_provider`, default `anthropic`) combines the
   stage-1 answers and stage-2 fact-check notes into one answer. The
   synthesis prompt explicitly instructs the model not to just blend/average
   all six answers together: it evaluates each answer's accuracy using the
   fact-check notes as evidence, builds the consolidated answer primarily
   from whichever answer(s) are actually strongest, and only pulls in extra
   detail from the others where it adds real value.
4. **Citation verification.** This is the part that matters most: the
   synthesis model's citations are never trusted as-is. Every URL it emits
   is checked with a live `HEAD` (falling back to `GET`) request — no LLM
   involved — and must return a 2xx/3xx to be marked verified. Requests to
   private/loopback/link-local/reserved addresses are refused outright
   (basic SSRF guard, since the URLs originate from model output). The UI
   shows ✅ verified vs ❌ removed for every citation.

The whole pipeline is resumable: each stage checks what's already in SQLite
before spending money re-calling a provider, so `POST /api/runs/{id}/resume`
can re-run just stage 3 (e.g. while iterating on the synthesis prompt)
without re-paying for stage 1.

Stage 2 and stage 3 always run with each provider's *default* sampling
params, even if you set a temperature/top-p override for stage 1 — an
override that happens to break a provider's stage-1 call shouldn't also
knock that provider out as a fact-checker or synthesizer.

### Stopping a run

The Run button becomes a **Stop** button the instant a run starts. Clicking
it calls `POST /api/runs/{id}/cancel`, which cancels the pipeline's asyncio
task — `asyncio.gather` propagates that cancellation into every in-flight
provider call immediately, so it actually stops mid-request rather than
finishing the current stage first. Anything already written to SQLite is
kept (a cancelled run can be resumed later like any other partial run); any
provider call that was still in flight is marked `cancelled` rather than
being left stuck showing "running" forever.

### Exporting results

Once a run reaches a terminal state (complete, failed, or cancelled), an
**Export as Markdown** button appears next to Resume/re-run. It downloads a
single self-contained `.md` file — prompt, run metadata, the synthesized
answer with citation verification status, every fact-check flag, and each
provider's full stage-1 answer (reasoning trace included, if it returned
one) with its cost/token/latency breakdown — via
`GET /api/runs/{id}/export`.

## Quick start

First, on any platform: copy the env template and fill in whichever provider
API keys you have (a provider left blank just reports "missing API key" in
stage 1 instead of blocking the other five — you don't need all six).

```bash
cp .env.example .env
```

The app loads `.env` automatically on startup (via `python-dotenv`) — no
need to `source` it or export vars by hand.

### Option A — Docker Desktop (any OS, recommended)

Install [Docker Desktop](https://www.docker.com/products/docker-desktop/)
and make sure it's running (its whale icon shows in your system tray/menu
bar — `docker info` succeeding on the command line confirms it's up).

**Build and run**, from the repo root:

```bash
docker compose up --build -d
```

- `--build` builds the image from `backend/Dockerfile` the first time (or
  rebuilds it if code changed since the last build).
- `-d` runs it detached (in the background) so the container keeps running
  after the command returns — recommended for normal use. Drop `-d` if you
  want to watch logs stream in your terminal instead.

Open **http://localhost:8000**.

**Managing it from the Docker Desktop app** (instead of the CLI): open
Docker Desktop → **Containers** tab → you'll see `ai-router-ai-router-1`
(grouped under the `ai-router` project). From there you can:
- Click the row to view live logs, inspect env vars, or open a shell inside
  the container
- Use the ⏹ / ▶ buttons to stop/start it without losing data (the SQLite DB
  lives in a named volume, `ai-router_ai-router-data`, visible under the
  **Volumes** tab — it survives container restarts and rebuilds)
- Click the port link (`8000:8000`) to jump straight to the app in your
  browser

**Stopping it:**

```bash
docker compose down
```

(This removes the container but keeps the named volume — your run history
and SQLite data are preserved for next time. Add `-v` only if you actually
want to wipe that data too.)

**After changing backend or frontend code**, rebuild the image — `backend/config`
and the data volume are live-mounted, but everything else (`backend/app`,
`frontend/`) is baked into the image at build time:

```bash
docker compose up --build -d
```

### Option B — Run it directly

<details open>
<summary><strong>macOS / Linux</strong></summary>

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

</details>

<details>
<summary><strong>Windows (PowerShell)</strong></summary>

```powershell
cd backend
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

If `Activate.ps1` is blocked by execution policy, run PowerShell as your
normal user and allow it for the current session only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
```

</details>

<details>
<summary><strong>Windows (Git Bash)</strong></summary>

```bash
cd backend
python -m venv .venv
source .venv/Scripts/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

</details>

Then open http://localhost:8000. The SQLite DB is created at
`backend/data/ai_router.db` on first run.

Stop the server with `Ctrl+C`. Deactivate the venv with `deactivate`.

## Configuration

`backend/config/providers.yaml` holds every provider's endpoint, model ID,
and reasoning/thinking parameters, plus the stage 2/3 pipeline settings.
Swapping a model string (e.g. when a provider ships a new flagship) is a
one-line YAML edit, not a code change. API keys are never stored in YAML —
only the environment variable name to read them from
(see `.env.example`).

**Provider model names and reasoning-param names move fast.** The defaults
checked into `providers.yaml` were current as of this build; verify against
each provider's live docs before depending on them in production, and
expect to update the YAML periodically.

### Live web access

Every provider has its native web search tool enabled by default (Anthropic's
`web_search_20250305`, OpenAI's `web_search`, Google's `google_search`
grounding, and OpenRouter's `openrouter:web_search` for the three
OpenRouter-routed models below) — so answers can cite current information
instead of being limited to the model's training cutoff. Remove a
provider's `tools:` block in `providers.yaml` to turn it off; it's billed
per-search on top of normal token costs.

### DeepSeek, MiniMax, and Moonshot (Kimi) via OpenRouter

These three are routed through [OpenRouter](https://openrouter.ai) rather
than called directly, using a single `OPENROUTER_API_KEY`. OpenRouter speaks
the OpenAI chat-completions format and normalizes reasoning effort
(`reasoning: {effort: ...}`) and web search across vendors, so all three use
`request_style: openai_chat` with OpenRouter's `"vendor/model-name"` slugs
(e.g. `deepseek/deepseek-v4-pro`), not the vendors' own native model names.
Pricing in `providers.yaml` is each provider's list/base per-token rate,
double-checked against current pricing pages (not accounting for prompt
caching discounts, which can cut effective cost 60-90% on repeat context —
so these are a conservative upper bound). Re-verify periodically; they
change often.

### Model settings — choosing models, enabling/disabling vendors, sampling params

Open "Model settings" in the sidebar for per-provider controls, all applied
live (no restart) and persisted to `providers.yaml`:

- **Model dropdown.** Each provider offers a curated `models:` catalog — 5
  entries per vendor by design: 4 as the guaranteed minimum plus one spare,
  so a single model going deprecated doesn't drop anyone below 4 working
  choices (see the policy note at the top of
  [`MODELS_STATUS.md`](MODELS_STATUS.md)). Live per-million-token pricing
  is shown right in the dropdown and updates instantly on selection, before
  you even hit Save. Cost tracking automatically follows whichever model is
  selected, since `pricing` is looked up from the catalog entry matching
  `model`, not stored separately — no separate step to keep them in sync.
  A catalog entry can carry `status: "deprecated"` — the dropdown greys it
  out and the backend rejects selecting it with a 400, so you can't
  accidentally point a run at a model that's about to stop working.
- **Enable/disable toggle.** Flips a provider's `enabled` flag immediately
  on click (no Save needed). A disabled provider is skipped entirely in
  stage 1, can't be picked as a stage-2 fact-checker, and is removed from
  the "Synthesis model" dropdown; the backend also rejects a run that names
  a disabled provider as the synthesis provider.
- **Temperature / top-p.** Optional per-provider sampling params, left
  blank by default so each model just uses its own native default (1.0 for
  most; shown as a hint next to each field, from
  `default_temperature`/`default_top_p` in `providers.yaml` — informational
  only, never sent unless you actually set an override). A "Sampling
  parameters (ⓘ)" info bubble at the top of the panel explains what
  temperature and top-p actually do. Providers whose reasoning mode
  provably rejects a custom value (`sampling_locked: true` in
  `providers.yaml` — currently Anthropic and OpenAI, both of which require
  `temperature=1` while their extended-thinking/reasoning-effort mode is
  on) have the input fields disabled entirely in the UI, and the backend
  rejects a `PUT .../params` call that tries to set one anyway — so there's
  no way to enter a value that vendor is known to reject.

### Run status and provider responses

- The status badge at the top of a run shows which stage is currently
  running ("Stage 1: dispatching to providers", "Stage 2: fact-checking",
  "Stage 3: synthesizing", "Verifying citations") in yellow, then flips to
  green (Complete) or red (Failed).
- Click any provider's status card to jump straight to its full response
  (and reasoning trace, if it returned one) below — no need to hunt for the
  matching tab.
- Each card's cost total also shows output tokens/second for that
  provider's stage-1 call, a quick throughput comparison across providers.
- The prompt box has a visibly distinct border so it's unambiguous where to
  type, and "Fact-check mode" has an (ⓘ) info bubble — hover or focus it —
  explaining what each of the three modes actually does before you pick one.

All of this is `PUT /api/config/providers/{key}/{model,enabled,params}`,
each doing a targeted rewrite of just the relevant line(s) in
`providers.yaml` (`config.set_provider_field`) — comments and formatting
survive, and the in-memory config hot-reloads so the very next run picks up
the change.

To add more models to a provider's dropdown, add entries to its `models:`
list in `providers.yaml` with verified current pricing.

## Cost control

- `GET /api/runs/{id}` returns a `cost_summary` broken down by stage
  (`stage1_usd`/`stage2_usd`/`stage3_usd`/`total_usd`) and a
  `cost_by_provider` breakdown of the same, per provider — how much each
  model's own stage-1 answer cost, how much it cost when acting as a
  stage-2 fact-checker, and how much stage 3 cost if it was the synthesis
  provider, each with input/output token counts. The UI shows this inline
  on every provider's status card.
- Set `skip_stage2: true` on a run (or check "Skip fact-check stage" in the
  UI) to drop the most expensive stage for quick/cheap iterations.

## Tests

```bash
cd backend
pytest
```

Pipeline logic is tested against mocked providers (`respx`); citation
verification is tested against real HTTP behavior (a live URL, a 404, and
an unreachable host).
