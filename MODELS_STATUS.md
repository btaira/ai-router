# Model catalog status

Tracks whether each model in `backend/config/providers.yaml`'s `models:`
catalogs is still valid/callable, and what it costs. Update this whenever
you re-verify the catalog (see "How to refresh" below) — it's the record of
*why* the YAML looks the way it does, so future edits don't have to
re-derive it from scratch.

**Last verified:** 2026-07-14, via 8 parallel research passes (6 one per
vendor, plus 2 follow-up passes for OpenAI and Google specifically).
**DeepSeek/MiniMax/Moonshot catalogs expanded 2026-07-20** — see
"OpenRouter: any-vendor picks" below.

## Policy: always keep 4+ working models per vendor

Every provider's catalog carries **5 entries** on purpose, not 4 — one
spare beyond the minimum of 4 the UI is meant to always offer. That way a
single model going deprecated (as happened to `gemini-2.5-pro` below)
doesn't drop a vendor below 4 working choices; there's already a buffer
entry to fall back on. When you next refresh this file, if any vendor drops
below 5 *working* entries (i.e. a second one in the same vendor goes
deprecated), research and add a replacement immediately rather than waiting
for the next scheduled audit. This is the floor, not the ceiling — DeepSeek,
MiniMax, and Moonshot each carry well more than 5 now (see below), since
they can pick from any OpenRouter-hosted model, not just their own vendor's.

## OpenRouter: any-vendor picks (2026-07-20, refreshed 2026-07-20)

DeepSeek, MiniMax, and Moonshot all route through the same
`OPENROUTER_API_KEY` (see the note at the top of `providers.yaml`), so
unlike Anthropic/OpenAI/Google — each locked to their own vendor's models —
these three can each be pointed at *any* OpenRouter-hosted model. Their
`models:` catalogs now carry their original vendor-specific lineup **plus**
a shared block covering the **top 15 models on OpenRouter's [LLM
Leaderboard](https://openrouter.ai/rankings#leaderboard-table)** by weekly
usage, excluding anything from Anthropic/OpenAI/Google (pick those from
their own native slot — better feature support) and anything already
covered by one of the three vendor-specific lists in this file (so e.g.
`deepseek/deepseek-v4-pro` doesn't appear twice in DeepSeek's own dropdown
— it's already there). That's why the shared block below is 9 entries, not
15: of the top 15 leaderboard entries, 6 are Anthropic/OpenAI/Google
(excluded) and 6 more are already vendor-list duplicates
(`deepseek-v4-flash`, `deepseek-v4-pro`, `deepseek-v3.2`, `minimax-m3`,
`kimi-k2.6`, `kimi-k3`), leaving these 9 net-new entries. Pricing verified
the same day via OpenRouter's public `/api/v1/models` endpoint:

| Rank | Model | Pricing (in/out per M) |
|---|---|---|
| 1 | `tencent/hy3` | $0.14 / $0.58 |
| 2 | `xiaomi/mimo-v2.5` | $0.14 / $0.28 |
| 5 | `z-ai/glm-5.2` | $0.959 / $3.014 |
| 6 | `nvidia/nemotron-3-ultra-550b-a55b:free` | $0 / $0 |
| 13 | `stepfun/step-3.7-flash` | $0.20 / $1.15 |
| 15 | `poolside/laguna-m.1:free` | $0 / $0 |
| 17 | `xiaomi/mimo-v2.5-pro` | $0.435 / $0.87 |
| 29 | `x-ai/grok-4.5` | $2.00 / $6.00 |
| 33 | `mistralai/mistral-nemo` | $0.019 / $0.03 |

(Rank = position on the LLM Leaderboard as of 2026-07-20, before excluding
Anthropic/OpenAI/Google/already-covered entries — included so this list can
be re-derived from a future leaderboard snapshot without redoing the whole
selection from scratch.)

**Update 2026-07-21:** `tencent/hy3:free` was retired by OpenRouter within
a day of being added — confirmed gone entirely from
`/api/v1/models`, and calling it returned an HTTP 404 pointing at
`tencent/hy3` (the paid version, $0.14/$0.58) as the replacement. Swapped
in `providers.yaml` (catalog entry and the OpenRouter 1 slot's active
`model:`, which had been set to the dead slug). **Lesson for next
refresh:** the `:free` variants in this shared block
(`nvidia/nemotron-3-ultra-550b-a55b:free`, `poolside/laguna-m.1:free`)
carry the same risk — OpenRouter's free tiers can disappear with no
notice, unlike paid ones. Re-verify both still resolve before relying on
this file being current; as of 2026-07-21 both are still confirmed live.

**Update 2026-07-21 (later):** `poolside/laguna-m.1:free` turned out to be
unreliable beyond just the retirement risk above — live-tested across this
session it failed three different ways (HTTP 500, HTTP 400 "Server tool
request failed", and HTTP 429 rate-limit) with the app's exact
`reasoning.effort: xhigh` + `openrouter:web_search` tool combo, including
one case where it still failed after the tools-retry fallback. A 3x
back-to-back retest of the free tier confirmed it's just flaky capacity,
not a one-off. Switched the OpenRouter 3 slot's active `model:` to
`z-ai/glm-5.2` (3/3 clean in the same retest, vs. `xiaomi/mimo-v2.5`'s 1/3
HTTP 500 in the same test — picked GLM over the cheaper Xiaomi entry for
that reason). `poolside/laguna-m.1:free` is left in the catalog as a
selectable option, just no longer the default.

Also added `moonshotai/kimi-k3` ($3.00 / $15.00) directly to Moonshot's own
lineup — it was listed as "rumored, not released" the last time this file
was verified (2026-07-14); it's out now and ranks #23 on the leaderboard
and #3 on the Artificial Analysis Intelligence Index. Notably pricier than
the rest of Moonshot's lineup, so it was added as an available pick, not
made the default.

## Local LLMs (LM Studio) — added 2026-07-20

Two provider slots, `local1`/`local2` (`local: true` in `providers.yaml`),
talk to a local OpenAI-compatible server (LM Studio by default) instead of
a hosted vendor API. No `models:` catalog and no pricing tracking here —
by design, since the whole point is that a local server's model list
changes as the user loads/unloads models, and the app queries it live via
`GET /api/config/providers/{key}/local-models` rather than tracking it in
this file. See the "Local LLMs (LM Studio)" section in `README.md` for the
full behavior (live model dropdown, `host.docker.internal` networking,
$0/M pricing).

## Summary

28 of 30 *originally-curated* catalog entries (5 per vendor × 6 vendors,
before the OpenRouter any-vendor expansion above) are confirmed working.
Two are deprecated, both Google, both already superseded by working entries
already in the catalog:

| Provider | Model | Status | Notes |
|---|---|---|---|
| Google | `gemini-2.5-pro` | ⚠️ **deprecated** | Shuts down 2026-10-16. Replacement: `gemini-3.1-pro-preview` (already in the catalog, now the active default). |
| Google | `gemini-2.5-flash-lite` | ⚠️ **not added** — considered and rejected | Also shuts down 2026-10-16 (~3 months out, fails our 6-month buffer bar) and reported throwing intermittent 404s as of 2026-07-09. Deliberately not added to the catalog; noted here so nobody re-adds it without checking first. |

Everything else below is `working` with pricing verified against the
provider's own current docs / OpenRouter model pages, unless noted.

## Anthropic

| Model | Status | Pricing (in/out per M) | Notes |
|---|---|---|---|
| `claude-opus-4-8` | working | $5.00 / $25.00 | Flagship for complex agentic work. Tentative retirement not before 2027-05-28. |
| `claude-sonnet-5` | working | $2.00 / $10.00 | Introductory pricing confirmed through 2026-08-31; standard $3/$15 after. Tentative retirement not before 2027-02-17. |
| `claude-haiku-4-5` | working | $1.00 / $5.00 | Tentative retirement not before 2026-10-15. |
| `claude-opus-4-7` | working | $5.00 / $25.00 | Moved to Anthropic's "legacy models" table (Opus 4.8 recommended for new work) but not deprecated. Tentative retirement not before 2027-04-16. |
| `claude-fable-5` | working | $10.00 / $50.00 | Newest top-tier flagship, above Opus 4.8, GA since 2026-06-09. 1M context, always-on adaptive thinking. Added as the 5th/buffer entry. |

Sources: platform.claude.com/docs/en/about-claude/models/overview, platform.claude.com/docs/en/about-claude/model-deprecations

## OpenAI

| Model | Status | Pricing (in/out per M) | Notes |
|---|---|---|---|
| `gpt-5.6-sol` | working | $5.00 / $30.00 | Flagship of the GPT-5.6 family (launched 2026-07-09). |
| `gpt-5.6-terra` | working | $2.50 / $15.00 | Mid-tier; "competitive with GPT-5.5 at half the cost" per OpenAI. |
| `gpt-5.6-luna` | working | $1.00 / $6.00 | Cheapest GPT-5.6 tier. |
| `gpt-5.5` | working | $5.00 / $30.00 | Still active, no sunset date. |
| `gpt-5.4-mini` | working | $0.75 / $4.50 | Prior generation, smaller/cheaper size class — deliberately distinct from the four GPT-5.6/5.5 entries above. Added as the 5th/buffer entry. Confirmed not on the deprecation list (only cited as the *migration target* for retiring `gpt-5-mini-2025-08-07` and `o4-mini-2025-04-16`). |

**Retiring 2026-12-11 (not in our catalog, but worth knowing if anyone hand-edits a model string):** `gpt-5-2025-08-07`, `gpt-5-mini-2025-08-07`, `gpt-5-nano-2025-08-07`, `gpt-5-pro-2025-10-06`, `o3-2025-04-16`, `o3-pro-2025-06-10`.

Sources: developers.openai.com/api/docs/pricing, developers.openai.com/api/docs/deprecations, developers.openai.com/api/docs/models/gpt-5.4-mini

## Google Gemini

| Model | Status | Pricing (in/out per M, ≤200K tokens) | Notes |
|---|---|---|---|
| `gemini-3.1-pro-preview` | working | $2.00 / $12.00 | Flagship; still officially "Preview" despite being out since 2026-02-19. Doubles to $4/$18 above 200K tokens (not tracked by our cost estimator). **Active default model.** |
| `gemini-3.5-flash` | working | $1.50 / $9.00 | Stable, released 2026-05-19. |
| `gemini-3.1-flash-lite` | working | $0.25 / $1.50 | Stable, released 2026-05-07. Routine ~1yr shutdown date of 2027-05-07 (not urgent). |
| `gemini-3-flash-preview` | working | $0.50 / $3.00 | Older preview tier (released 2025-12-17) that `gemini-3.5-flash` eventually supersedes, but "no shutdown date announced" as of 2026-07. Added as the 4th working entry to replace the deprecated `gemini-2.5-pro` below — re-check on next audit since it's explicitly a preview model. |
| `gemini-2.5-pro` | ⚠️ **deprecated** | $1.25 / $10.00 | Shuts down 2026-10-16. Replacement: `gemini-3.1-pro-preview`. Kept in the catalog (not deleted) with `status: "deprecated"` so the UI/API block selecting it, for a record of why it's gone. |

**Considered and rejected:** `gemini-2.5-flash` and `gemini-2.5-flash-lite` — both also shut down 2026-10-16 and were reported throwing intermittent 404s as of 2026-07-09. Do not add either without re-verifying first.

Sources: ai.google.dev/gemini-api/docs/models, .../pricing, .../deprecations, .../changelog, discuss.ai.google.dev (404 report thread)

## DeepSeek (via OpenRouter)

| Model | Status | Pricing (in/out per M) | Notes |
|---|---|---|---|
| `deepseek/deepseek-v4-pro` | working | $0.435 / $0.87 | Flagship, released 2026-04-24. 1.6T/49B active MoE, 1M context. |
| `deepseek/deepseek-v4-flash` | working | $0.09 / $0.18 | Released alongside V4 Pro; efficiency-tuned MoE. |
| `deepseek/deepseek-r1` | working | $0.70 / $2.50 | Original R1 (2025-01-20), still live. |
| `deepseek/deepseek-v3.2` | working | $0.269 / $0.40 | Sparse-attention model, released 2025-12-01. Price corrected 2026-07-20 (was listed at $0.2145/$0.3218). |
| `deepseek/deepseek-r1-0528` | working | $0.50 / $2.15 | Newer point release of R1, cheaper than the base R1 entry above. Added as the 5th/buffer entry. |

Source: openrouter.ai/deepseek and per-model pages

## MiniMax (via OpenRouter)

| Model | Status | Pricing (in/out per M) | Notes |
|---|---|---|---|
| `minimax/minimax-m2.7` | working | $0.24 / $0.96 | 205K context, released 2026-03-18. |
| `minimax/minimax-m2` | working | $0.255 / $1.02 | Coding/agentic-focused, released 2025-10-23. |
| `minimax/minimax-m3` | working | $0.30 / $1.20 | Flagship — multimodal, 1M context, released 2026-05-31. |
| `minimax/minimax-m1` | working | $0.40 / $2.20 | Oldest of the four, superseded in capability but not deprecated. |
| `minimax/minimax-m2.5` | working | $0.15 / $0.90 | Cheapest tier. Added as the 5th/buffer entry. |

Source: openrouter.ai/minimax and per-model pages

## Moonshot AI / Kimi (via OpenRouter)

| Model | Status | Pricing (in/out per M) | Notes |
|---|---|---|---|
| `moonshotai/kimi-k2-thinking` | working | $0.60 / $2.50 | Released 2025-11-06. |
| `moonshotai/kimi-k2.6` | working | $0.66 / $3.41 | Released 2026-04-20. |
| `moonshotai/kimi-k2.7-code` | working | $0.719 / $3.49 | Flagship (coding-focused), released 2026-06-12. |
| `moonshotai/kimi-k2` | working | $0.57 / $2.30 | Original K2 (2025-07-11, aka "K2 0711"). |
| `moonshotai/kimi-k2-0905` | working | $0.60 / $2.50 | Mid-point release between K2 and K2.6. Added as the 5th/buffer entry. |

**Update 2026-07-20:** Kimi K3 is out — see "OpenRouter: any-vendor picks"
above. It's now in the catalog at $3.00 / $15.00, added as an extra option
(not the default).

Source: openrouter.ai/moonshotai and per-model pages

## How to refresh this file

Have 6 agents research in parallel (one per vendor), each given the current
5 catalog entries for that vendor and asked to verify status/pricing and
flag a newer flagship. Then, for any vendor that drops below 4 *working*
entries, spawn one more targeted agent to find a replacement immediately
(see the OpenAI/Google follow-up passes above for the prompt shape). Then:

1. Update pricing numbers in `backend/config/providers.yaml` for anything
   that changed.
2. Add `status: "deprecated"` to any catalog entry a vendor has sunset (see
   the `gemini-2.5-pro` entry for the exact format) — this is what the UI
   uses to refuse selecting it (`PUT /api/config/providers/{key}/model`
   rejects it with a 400, and the dropdown greys it out). Don't delete the
   entry — keeping it with a status is the record of why it's gone.
3. If the *currently active* `model:` for a provider becomes deprecated,
   switch it to the vendor's recommended replacement so the running config
   never defaults to a dying model.
4. If a vendor is down to fewer than 5 total entries (i.e. you removed one
   instead of flagging it), add a new 5th entry to restore the buffer.
5. Update this file's tables and "Last verified" date.
