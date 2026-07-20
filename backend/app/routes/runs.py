from __future__ import annotations

import asyncio
import json

import httpx
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response

from .. import db
from ..config import (
    ModelUpdateError,
    get_config,
    set_provider_api_key,
    update_provider_enabled,
    update_provider_model,
    update_provider_params,
)
from ..pipeline.document_extract import ExtractionError, UnsupportedFileType, extract_text
from ..pipeline.export_markdown import build_run_markdown
from ..pipeline.followup import run_followup
from ..pipeline.orchestrator import run_pipeline
from ..schemas import (
    CreateRunRequest,
    FollowupRequest,
    ResumeRunRequest,
    UpdateProviderApiKeyRequest,
    UpdateProviderEnabledRequest,
    UpdateProviderModelRequest,
    UpdateProviderParamsRequest,
)

MAX_UPLOAD_BYTES = 15 * 1024 * 1024  # 15MB

router = APIRouter(prefix="/api")

_background_tasks: set[asyncio.Task] = set()
# run_id -> in-flight pipeline task, so a Stop click can find and cancel it.
# Entries are removed once the task finishes (whether by completing, failing,
# or being cancelled) so this never grows for old runs.
_run_tasks: dict[str, asyncio.Task] = {}


def _launch(run_id: str, force_stage: str | None = None) -> None:
    cfg = get_config()
    task = asyncio.create_task(run_pipeline(run_id, cfg, force_stage=force_stage))
    _background_tasks.add(task)
    _run_tasks[run_id] = task

    def _cleanup(_: asyncio.Task) -> None:
        _background_tasks.discard(task)
        if _run_tasks.get(run_id) is task:
            del _run_tasks[run_id]

    task.add_done_callback(_cleanup)


@router.get("/config")
def get_provider_config():
    cfg = get_config()
    return {
        "providers": [
            {
                "key": key, "display_name": p.display_name, "model": p.model,
                "enabled": p.enabled, "has_api_key": p.local or bool(p.api_key),
                "pricing": p.pricing, "available_models": p.available_models,
                "temperature": p.extra.get("temperature"), "top_p": p.extra.get("top_p"),
                "default_temperature": p.default_temperature, "default_top_p": p.default_top_p,
                "sampling_locked": p.sampling_locked, "local": p.local,
            }
            for key, p in cfg.providers.items()
        ],
        "stage2": {
            "default_mode": cfg.stages.stage2_mode,
            "fact_checkers": cfg.stages.fact_checkers,
            "modes": ["designated_fact_checkers", "full_mesh", "diff_then_check"],
        },
        "stage3": {"default_synthesis_provider": cfg.stages.synthesis_provider},
    }


@router.put("/config/providers/{provider_key}/model")
def set_provider_model(provider_key: str, req: UpdateProviderModelRequest):
    cfg = get_config()
    if provider_key not in cfg.providers:
        raise HTTPException(404, f"unknown provider: {provider_key}")
    pcfg = cfg.providers[provider_key]
    catalog_entry = next((m for m in pcfg.available_models if m.get("id") == req.model), None)
    if catalog_entry and catalog_entry.get("status") not in (None, "working"):
        raise HTTPException(
            400,
            f"{req.model!r} is marked {catalog_entry.get('status')!r} in the model catalog and can't be "
            "selected — see backend/config/providers.yaml or MODELS_STATUS.md for the current replacement.",
        )
    try:
        update_provider_model(provider_key, req.model)
    except ModelUpdateError as exc:
        raise HTTPException(500, str(exc)) from exc
    cfg = get_config(refresh=True)
    p = cfg.providers[provider_key]
    return {"key": provider_key, "model": p.model, "pricing": p.pricing}


@router.put("/config/providers/{provider_key}/enabled")
def set_provider_enabled(provider_key: str, req: UpdateProviderEnabledRequest):
    cfg = get_config()
    if provider_key not in cfg.providers:
        raise HTTPException(404, f"unknown provider: {provider_key}")
    try:
        update_provider_enabled(provider_key, req.enabled)
    except ModelUpdateError as exc:
        raise HTTPException(500, str(exc)) from exc
    cfg = get_config(refresh=True)
    return {"key": provider_key, "enabled": cfg.providers[provider_key].enabled}


@router.put("/config/providers/{provider_key}/params")
def set_provider_params(provider_key: str, req: UpdateProviderParamsRequest):
    cfg = get_config()
    if provider_key not in cfg.providers:
        raise HTTPException(404, f"unknown provider: {provider_key}")
    pcfg = cfg.providers[provider_key]
    if pcfg.sampling_locked and (req.temperature is not None or req.top_p is not None):
        raise HTTPException(
            400,
            f"{pcfg.display_name} locks temperature/top_p while its reasoning mode is enabled "
            "and rejects any custom value — leave both fields blank to use its default.",
        )
    try:
        update_provider_params(provider_key, req.temperature, req.top_p)
    except ModelUpdateError as exc:
        raise HTTPException(500, str(exc)) from exc
    cfg = get_config(refresh=True)
    p = cfg.providers[provider_key]
    return {"key": provider_key, "temperature": p.extra.get("temperature"), "top_p": p.extra.get("top_p")}


@router.put("/config/providers/{provider_key}/api-key")
def set_provider_api_key_route(provider_key: str, req: UpdateProviderApiKeyRequest):
    cfg = get_config()
    if provider_key not in cfg.providers:
        raise HTTPException(404, f"unknown provider: {provider_key}")
    set_provider_api_key(provider_key, req.api_key.strip())
    return {"key": provider_key, "has_api_key": True}


@router.delete("/config/providers/{provider_key}/api-key")
def clear_provider_api_key_route(provider_key: str):
    cfg = get_config()
    if provider_key not in cfg.providers:
        raise HTTPException(404, f"unknown provider: {provider_key}")
    set_provider_api_key(provider_key, None)
    return {"key": provider_key, "has_api_key": bool(cfg.providers[provider_key].api_key)}


@router.get("/config/providers/{provider_key}/local-models")
async def list_local_models(provider_key: str):
    """Live-queries a local provider's own OpenAI-compatible `/models`
    endpoint (e.g. LM Studio) — deliberately not a static catalog like the
    other providers, since what's available locally changes as models are
    loaded/unloaded, often more than once per session.
    """
    cfg = get_config()
    if provider_key not in cfg.providers:
        raise HTTPException(404, f"unknown provider: {provider_key}")
    pcfg = cfg.providers[provider_key]
    if not pcfg.local:
        raise HTTPException(400, f"{provider_key} isn't a local provider")

    models_url = pcfg.base_url.rsplit("/chat/completions", 1)[0] + "/models"
    headers = {"Authorization": f"Bearer {pcfg.api_key}"} if pcfg.api_key else {}
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(models_url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"couldn't reach {models_url}: {exc}") from exc

    ids = sorted(m["id"] for m in data.get("data", []) if m.get("id"))
    return {"models": ids}


@router.post("/documents/extract")
async def extract_document(file: UploadFile = File(...)):
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, f"file too large — max {MAX_UPLOAD_BYTES // (1024 * 1024)}MB")
    try:
        text, truncated = extract_text(file.filename or "upload", content)
    except UnsupportedFileType as exc:
        raise HTTPException(400, str(exc)) from exc
    except ExtractionError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"filename": file.filename, "text": text, "truncated": truncated, "char_count": len(text)}


@router.post("/runs")
async def create_run(req: CreateRunRequest):
    cfg = get_config()
    if req.stage2_mode and req.stage2_mode not in ("designated_fact_checkers", "full_mesh", "diff_then_check"):
        raise HTTPException(400, f"invalid stage2_mode: {req.stage2_mode}")
    if req.synthesis_provider and req.synthesis_provider not in cfg.providers:
        raise HTTPException(400, f"unknown synthesis_provider: {req.synthesis_provider}")
    synthesis_provider = req.synthesis_provider or cfg.stages.synthesis_provider
    if not cfg.providers[synthesis_provider].enabled:
        raise HTTPException(400, f"synthesis_provider {synthesis_provider!r} is disabled")
    if req.fact_checkers is not None:
        unknown = [p for p in req.fact_checkers if p not in cfg.providers]
        if unknown:
            raise HTTPException(400, f"unknown fact_checkers: {unknown}")

    run_id = db.create_run(
        prompt=req.prompt,
        skip_stage2=req.skip_stage2,
        stage2_mode=req.stage2_mode or cfg.stages.stage2_mode,
        synthesis_provider=synthesis_provider,
        fact_checkers=req.fact_checkers,
    )
    _launch(run_id)
    return {"run_id": run_id}


@router.get("/runs")
def list_runs(limit: int = 50):
    return db.list_runs(limit=limit)


def _serialize_run(run_id: str) -> dict:
    run = db.get_run(run_id)
    if run is None:
        raise HTTPException(404, "run not found")

    stage1 = db.get_stage1_responses(run_id)
    fact_checks = db.get_fact_check_results(run_id)
    for fc in fact_checks:
        if fc.get("claims_json"):
            fc["claims"] = json.loads(fc["claims_json"])
    synthesis = db.get_synthesis_result(run_id)
    citations_ = db.get_citation_verifications(run_id)
    cost = db.run_cost_summary(run_id)
    cost_by_provider = db.run_cost_by_provider(run_id)
    followup_messages = db.get_followup_messages(run_id)

    return {
        "run": run,
        "stage1_responses": stage1,
        "fact_check_results": fact_checks,
        "synthesis": synthesis,
        "citation_verifications": citations_,
        "cost_summary": cost,
        "cost_by_provider": cost_by_provider,
        "followup_messages": followup_messages,
    }


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    return _serialize_run(run_id)


@router.post("/runs/{run_id}/resume")
async def resume_run(run_id: str, req: ResumeRunRequest):
    run = db.get_run(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    if req.force_stage and req.force_stage not in ("stage1", "stage2", "stage3"):
        raise HTTPException(400, f"invalid force_stage: {req.force_stage}")
    _launch(run_id, force_stage=req.force_stage)
    return {"run_id": run_id, "status": "resumed"}


@router.post("/runs/{run_id}/followup")
async def post_followup(run_id: str, req: FollowupRequest):
    try:
        result = await run_followup(run_id, req.message, get_config())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return result


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str):
    run = db.get_run(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    task = _run_tasks.get(run_id)
    if task is None or task.done():
        raise HTTPException(400, "run is not currently in progress")
    task.cancel()
    return {"run_id": run_id, "status": "cancelling"}


@router.delete("/runs/{run_id}")
def delete_run(run_id: str):
    run = db.get_run(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    task = _run_tasks.get(run_id)
    if task is not None and not task.done():
        # avoid an orphaned background task burning through provider calls
        # for a run that no longer exists to show the results of
        task.cancel()
    db.delete_run(run_id)
    return {"run_id": run_id, "status": "deleted"}


@router.get("/runs/{run_id}/export")
def export_run(run_id: str):
    data = _serialize_run(run_id)
    markdown = build_run_markdown(**data)
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="ai-router-run-{run_id}.md"'},
    )
