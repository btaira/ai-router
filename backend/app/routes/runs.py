from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException

from .. import db
from ..config import get_config
from ..pipeline.orchestrator import run_pipeline
from ..schemas import CreateRunRequest, ResumeRunRequest

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
            {"key": key, "display_name": p.display_name, "model": p.model, "enabled": p.enabled}
            for key, p in cfg.providers.items()
        ],
        "stage2": {
            "default_mode": cfg.stages.stage2_mode,
            "fact_checkers": cfg.stages.fact_checkers,
            "modes": ["designated_fact_checkers", "full_mesh", "diff_then_check"],
        },
        "stage3": {"default_synthesis_provider": cfg.stages.synthesis_provider},
    }


@router.post("/runs")
def create_run(req: CreateRunRequest):
    cfg = get_config()
    if req.stage2_mode and req.stage2_mode not in ("designated_fact_checkers", "full_mesh", "diff_then_check"):
        raise HTTPException(400, f"invalid stage2_mode: {req.stage2_mode}")
    if req.synthesis_provider and req.synthesis_provider not in cfg.providers:
        raise HTTPException(400, f"unknown synthesis_provider: {req.synthesis_provider}")

    run_id = db.create_run(
        prompt=req.prompt,
        skip_stage2=req.skip_stage2,
        stage2_mode=req.stage2_mode or cfg.stages.stage2_mode,
        synthesis_provider=req.synthesis_provider or cfg.stages.synthesis_provider,
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

    return {
        "run": run,
        "stage1_responses": stage1,
        "fact_check_results": fact_checks,
        "synthesis": synthesis,
        "citation_verifications": citations_,
        "cost_summary": cost,
    }


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    return _serialize_run(run_id)


@router.post("/runs/{run_id}/resume")
def resume_run(run_id: str, req: ResumeRunRequest):
    run = db.get_run(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    if req.force_stage and req.force_stage not in ("stage1", "stage2", "stage3"):
        raise HTTPException(400, f"invalid force_stage: {req.force_stage}")
    _launch(run_id, force_stage=req.force_stage)
    return {"run_id": run_id, "status": "resumed"}
