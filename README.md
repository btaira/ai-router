# ai-router

Multi-LLM consensus and fact-checking app. One prompt fans out to six LLM
providers in their highest reasoning/thinking mode, a configurable set of
models cross-examine each other's answers for factual errors, and a
synthesis step produces one consolidated answer whose citations are
verified with real HTTP requests before being shown as trustworthy.

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
   stage-1 answers and stage-2 fact-check notes into one answer.
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

## Running locally

```bash
cp .env.example .env   # fill in the provider API keys you have
docker compose up --build
```

Then open http://localhost:8000.

Without Docker:

```bash
cd backend
pip install -r requirements.txt
cp ../.env.example ../.env   # fill in keys, then `source` or use a dotenv loader
uvicorn app.main:app --reload
```

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

## Cost control

- `GET /api/runs/{id}` returns a `cost_summary` broken down by stage
  (`stage1_usd`/`stage2_usd`/`stage3_usd`/`total_usd`), computed from each
  provider's `pricing` block in `providers.yaml` and the actual token
  counts returned by that call.
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
