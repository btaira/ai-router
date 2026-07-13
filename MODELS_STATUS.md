# Model catalog status

Tracks whether each model in `backend/config/providers.yaml`'s `models:`
catalogs is still valid/callable, and what it costs. Update this whenever
you re-verify the catalog (see "How to refresh" below) — it's the record of
*why* the YAML looks the way it does, so future edits don't have to
re-derive it from scratch.

**Last verified:** 2026-07-14, via 6 parallel research passes (one per
vendor) against each provider's live docs / OpenRouter model pages.

## Summary

23 of 24 catalog entries are confirmed working with pricing matching the
config exactly. One is deprecated:

| Provider | Model | Status | Notes |
|---|---|---|---|
| Google | `gemini-2.5-pro` | ⚠️ **deprecated** | Shuts down 2026-10-16. Google's own recommended replacement is `gemini-3.1-pro-preview`, which is also already in the catalog and is now the active default. Marked `status: "deprecated"` in `providers.yaml` so the UI can't select it. |

Everything else below is `working` with unchanged pricing, unless noted.

## Anthropic

| Model | Status | Pricing (in/out per M) | Notes |
|---|---|---|---|
| `claude-opus-4-8` | working | $5.00 / $25.00 | Current flagship for complex agentic work. Tentative retirement not before 2027-05-28. |
| `claude-sonnet-5` | working | $2.00 / $10.00 | Introductory pricing confirmed through 2026-08-31; standard $3/$15 after. Tentative retirement not before 2027-02-17. |
| `claude-haiku-4-5` | working | $1.00 / $5.00 | Tentative retirement not before 2026-10-15. |
| `claude-opus-4-7` | working | $5.00 / $25.00 | Moved to Anthropic's "legacy models" table (Opus 4.8 is now recommended for new work) but not deprecated. Tentative retirement not before 2027-04-16. |

**Not yet in the catalog:** `claude-fable-5` — a newer, more capable flagship above Opus 4.8, GA since 2026-06-09. $10.00/$50.00 per M, 1M context, always-on adaptive thinking. Worth adding as a 5th "maximum capability" tier if cost isn't a concern for a given run.

Sources: platform.claude.com/docs/en/about-claude/models/overview, platform.claude.com/docs/en/about-claude/model-deprecations

## OpenAI

| Model | Status | Pricing (in/out per M) | Notes |
|---|---|---|---|
| `gpt-5.6-sol` | working | $5.00 / $30.00 | Flagship of the GPT-5.6 family (launched 2026-07-09). |
| `gpt-5.6-terra` | working | $2.50 / $15.00 | Mid-tier; "competitive with GPT-5.5 at half the cost" per OpenAI. |
| `gpt-5.6-luna` | working | $1.00 / $6.00 | Cheapest GPT-5.6 tier. |
| `gpt-5.5` | working | $5.00 / $30.00 | Still active, no sunset date. Superseded GPT-5.2 (auto-routed) in 2026-06. Doubled in price from $2.50/$15 to $5/$30 at its 2026-04-23 launch — current config value is correct, just be aware it wasn't always this price. |

**Retiring 2026-12-11 (not in our catalog, but worth knowing if anyone hand-edits a model string):** `gpt-5-2025-08-07`, `gpt-5-mini-2025-08-07`, `gpt-5-nano-2025-08-07`, `gpt-5-pro-2025-10-06`, `o3-2025-04-16`, `o3-pro-2025-06-10`.

Sources: developers.openai.com/api/docs/pricing, developers.openai.com/api/docs/deprecations, openai.com/index/gpt-5-6

## Google Gemini

| Model | Status | Pricing (in/out per M, ≤200K tokens) | Notes |
|---|---|---|---|
| `gemini-3.1-pro-preview` | working | $2.00 / $12.00 | Current flagship; still officially "Preview" despite being out since 2026-02-19 — no GA rename yet. Doubles to $4/$18 above 200K tokens (not tracked by our cost estimator). **Now the active default model.** |
| `gemini-3.5-flash` | working | $1.50 / $9.00 | Stable, released 2026-05-19. |
| `gemini-3.1-flash-lite` | working | $0.25 / $1.50 | Stable, released 2026-05-07. Has a routine ~1yr shutdown date of 2027-05-07 (not urgent). |
| `gemini-2.5-pro` | ⚠️ **deprecated** | $1.25 / $10.00 | Shuts down 2026-10-16. Replacement: `gemini-3.1-pro-preview`. |

Sources: ai.google.dev/gemini-api/docs/models, .../pricing, .../deprecations, .../changelog

## DeepSeek (via OpenRouter)

| Model | Status | Pricing (in/out per M) | Notes |
|---|---|---|---|
| `deepseek/deepseek-v4-pro` | working | $0.435 / $0.87 | Current flagship, released 2026-04-24. 1.6T/49B active MoE, 1M context. |
| `deepseek/deepseek-v4-flash` | working | $0.09 / $0.18 | Released alongside V4 Pro; efficiency-tuned MoE. |
| `deepseek/deepseek-r1` | working | $0.70 / $2.50 | Original R1 (2025-01-20), still live. A newer point release exists as a separate slug, `deepseek/deepseek-r1-0528` ($0.50/$2.15) — cheaper, not a forced migration. |
| `deepseek/deepseek-v3.2` | working | $0.2145 / $0.3218 | Sparse-attention model, released 2025-12-01. |

Source: openrouter.ai/deepseek and per-model pages

## MiniMax (via OpenRouter)

| Model | Status | Pricing (in/out per M) | Notes |
|---|---|---|---|
| `minimax/minimax-m2.7` | working | $0.24 / $0.96 | 205K context, released 2026-03-18. |
| `minimax/minimax-m2` | working | $0.255 / $1.02 | Coding/agentic-focused, released 2025-10-23. |
| `minimax/minimax-m3` | working | $0.30 / $1.20 | Current flagship — multimodal, 1M context, released 2026-05-31. |
| `minimax/minimax-m1` | working | $0.40 / $2.20 | Oldest of the four (2025-06-17), superseded in capability but not deprecated. |

Source: openrouter.ai/minimax and per-model pages

## Moonshot AI / Kimi (via OpenRouter)

| Model | Status | Pricing (in/out per M) | Notes |
|---|---|---|---|
| `moonshotai/kimi-k2-thinking` | working | $0.60 / $2.50 | Released 2025-11-06. |
| `moonshotai/kimi-k2.6` | working | $0.66 / $3.41 | Released 2026-04-20. |
| `moonshotai/kimi-k2.7-code` | working | $0.719 / $3.49 | Current flagship (coding-focused), released 2026-06-12. |
| `moonshotai/kimi-k2` | working | $0.57 / $2.30 | Original K2 (2025-07-11, aka "K2 0711"). |

Kimi K3 is rumored for Q3 2026 but not released as of this check.

Source: openrouter.ai/moonshotai and per-model pages

## How to refresh this file

Have 6 agents research in parallel (one per vendor), each given the current
catalog entries for that vendor and asked to verify status/pricing and flag
a newer flagship. Then:

1. Update pricing numbers in `backend/config/providers.yaml` for anything
   that changed.
2. Add `status: "deprecated"` to any catalog entry a vendor has sunset (see
   the `gemini-2.5-pro` entry for the exact format) — this is what the UI
   uses to refuse selecting it (`PUT /api/config/providers/{key}/model`
   rejects it with a 400, and the dropdown should grey it out).
3. If the *currently active* `model:` for a provider becomes deprecated,
   switch it to the vendor's recommended replacement so the running config
   never defaults to a dying model.
4. Update this file's tables and "Last verified" date.
