"""Renders a run's full results (stage 1 answers, fact-check flags,
synthesis, citation verification, cost breakdown) as a single self-contained
Markdown document, so a run can be saved/shared outside the app.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def _fmt_ts(epoch: float | None) -> str:
    if not epoch:
        return "?"
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _fmt_cost(n: float | None) -> str:
    return f"${(n or 0):.4f}"


def _fmt_tokens_m(n: int | None) -> str:
    if n is None:
        return "?"
    return f"{n / 1_000_000:.6f}M"


def build_run_markdown(
    run: dict[str, Any],
    stage1_responses: list[dict[str, Any]],
    fact_check_results: list[dict[str, Any]],
    synthesis: dict[str, Any] | None,
    citation_verifications: list[dict[str, Any]],
    cost_summary: dict[str, float],
    cost_by_provider: dict[str, dict[str, Any]],
    followup_messages: list[dict[str, Any]] | None = None,
) -> str:
    lines: list[str] = []
    w = lines.append

    w(f"# AI Router Run Report")
    w("")
    w(f"**Prompt:** {run['prompt']}")
    w("")
    w(f"- **Run ID:** `{run['run_id']}`")
    w(f"- **Status:** {run['status']}")
    w(f"- **Created:** {_fmt_ts(run.get('created_at'))}")
    w(f"- **Fact-check mode:** {run.get('stage2_mode')}" + (" (skipped)" if run.get("skip_stage2") else ""))
    w(f"- **Synthesis provider:** {run.get('synthesis_provider')}")
    w(
        f"- **Total cost:** {_fmt_cost(cost_summary.get('total_usd'))} "
        f"(stage1 {_fmt_cost(cost_summary.get('stage1_usd'))} · "
        f"stage2 {_fmt_cost(cost_summary.get('stage2_usd'))} · "
        f"stage3 {_fmt_cost(cost_summary.get('stage3_usd'))} · "
        f"follow-up {_fmt_cost(cost_summary.get('followup_usd'))})"
    )
    w("")
    w("---")
    w("")

    if synthesis:
        w("## Synthesized Answer")
        w("")
        if synthesis.get("status") == "ok":
            w(f"_Synthesized by **{synthesis.get('provider')}**_")
            w("")
            w(synthesis.get("synthesis_text") or "(empty)")
        else:
            w(f"Synthesis {synthesis.get('status')}: {synthesis.get('error')}")
        w("")

        if citation_verifications:
            w("### Citations")
            w("")
            for c in citation_verifications:
                icon = "✅" if c.get("verified") else "❌"
                status = f"HTTP {c['http_status']}" if c.get("http_status") else (c.get("error") or "unreachable")
                found = " · found in stage-1 sources" if c.get("found_in_sources") else ""
                w(f"- {icon} <{c['url']}> — {status}{found}")
            w("")
        else:
            w("_No citations were output by the synthesis step._")
            w("")

    if followup_messages:
        w("## Follow-up Dialog")
        w("")
        for m in followup_messages:
            if m["role"] == "user":
                w(f"**You:** {m['content']}")
                w("")
            elif m.get("status") == "ok":
                w(f"**{run.get('synthesis_provider')}:** {m['content']}")
                w("")
            else:
                w(f"**{run.get('synthesis_provider')}:** _{m.get('status')}: {m.get('error')}_")
                w("")

    w("---")
    w("")
    w("## Fact-check Flags")
    w("")
    flagged = [fc for fc in fact_check_results if fc.get("status") == "ok" and fc.get("claims_json")]
    if not flagged:
        w("_Fact-check stage skipped, not yet run, or found nothing to flag._")
        w("")
    for fc in flagged:
        claims = json.loads(fc["claims_json"])
        if not claims:
            continue
        w(f"### {fc['checker_provider']} reviewing {fc['subject_provider']}")
        w("")
        for c in claims:
            w(f"- **{c.get('verdict', '?').upper()}** (confidence {c.get('confidence', '?')}): {c.get('claim')}")
            if c.get("correction"):
                w(f"  - Suggested correction: {c['correction']}")
        w("")

    w("---")
    w("")
    w("## Stage 1 — Independent Answers")
    w("")
    for r in stage1_responses:
        costs = cost_by_provider.get(r["provider"], {})
        total = costs.get("total", {})
        w(f"### {r['provider']} — `{r.get('model', '?')}`")
        w("")
        if r["status"] != "ok":
            w(f"Status: **{r['status']}** — {r.get('error')}")
            w("")
            continue
        w(
            f"Status: ok · Tokens: {_fmt_tokens_m(r.get('input_tokens'))} in / "
            f"{_fmt_tokens_m(r.get('output_tokens'))} out · "
            f"Cost (this call): {_fmt_cost(r.get('cost_usd'))} · "
            f"Total across all stages: {_fmt_cost(total.get('cost_usd'))} · "
            f"Latency: {r.get('latency_ms') and round(r['latency_ms'])}ms"
        )
        w("")
        if r.get("thinking_text"):
            w("<details><summary>Reasoning / thinking trace</summary>")
            w("")
            w(r["thinking_text"])
            w("")
            w("</details>")
            w("")
        w("**Final answer:**")
        w("")
        w(r.get("response_text") or "(empty)")
        w("")

    w("---")
    w("")
    w(f"_Generated by AI Router on {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}._")
    w("")

    return "\n".join(lines)
