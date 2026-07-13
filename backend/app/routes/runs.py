from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException

from .. import db
from ..config import (
    ModelUpdateError,
    get_config,
    update_provider_enabled,
    update_provider_model,
    update_provider_params,
)
from ..pipeline.orchestrator import run_pipeline
from ..schemas import (
    CreateRunRequest,
    ResumeRunRequest,
    UpdateProviderEnabledRequest,
    UpdateProviderModelRequest,
    UpdateProviderParamsRequest,
)

router = APIRouter(prefix="/api")

_background_tasks: set[asyncio.Task] = set()


def _launch(run_id: str, force_stage: str | None = None) -> None:
    cfg = get_config()
    task = asyncio.create_task(run_pipeline(run_id, cfg, force_stage=force_stage))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


@router.get("/config")
def get_provider_config():
    cfg = get_config()
    return {
        "providers": [
            {
                "key": key, "display_name": p.display_name, "model": p.model,
                "enabled": p.enabled, "has_api_key": bool(p.api_key),
                "pricing": p.pricing, "available_models": p.available_models,
                "temperature": p.extra.get("temperature"), "top_p": p.extra.get("top_p"),
                "default_temperature": p.default_temperature, "default_top_p": p.default_top_p,
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
    try:
        update_provider_params(provider_key, req.temperature, req.top_p)
    except ModelUpdateError as exc:
        raise HTTPException(500, str(exc)) from exc
    cfg = get_config(refresh=True)
    p = cfg.providers[provider_key]
    return {"key": provider_key, "temperature": p.extra.get("temperature"), "top_p": p.extra.get("top_p")}


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

    run_id = db.create_run(
        prompt=req.prompt,
        skip_stage2=req.skip_stage2,
        stage2_mode=req.stage2_mode or cfg.stages.stage2_mode,
        synthesis_provider=synthesis_provider,
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

    return {
        "run": run,
        "stage1_responses": stage1,
        "fact_check_results": fact_checks,
        "synthesis": synthesis,
        "citation_verifications": citations_,
        "cost_summary": cost,
        "cost_by_provider": cost_by_provider,
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
