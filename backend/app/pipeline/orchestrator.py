"""Drives a run through stage 1 -> stage 2 -> stage 3 -> citation verification.

Every stage function is independently resumable (checks the DB before
spending money re-calling a provider), so `run_pipeline` can be called again
on an existing run_id to pick up wherever it left off — handy while
iterating on the stage 3 synthesis prompt without re-paying for stage 1.
"""
from __future__ import annotations

import asyncio
import logging

from .. import db
from ..config import AppConfig
from . import citations, stage1_dispatch, stage2_factcheck, stage3_synthesis

logger = logging.getLogger(__name__)


async def run_pipeline(run_id: str, cfg: AppConfig, force_stage: str | None = None) -> None:
    run = db.get_run(run_id)
    if run is None:
        raise ValueError(f"unknown run_id: {run_id}")
    prompt = run["prompt"]

    try:
        db.update_run_status(run_id, "running_stage1")
        await stage1_dispatch.run_stage1(run_id, prompt, cfg, force=force_stage == "stage1")

        db.update_run_status(run_id, "running_stage2")
        await stage2_factcheck.run_stage2(run_id, prompt, cfg, force=force_stage in ("stage1", "stage2"))

        db.update_run_status(run_id, "running_stage3")
        synthesis = await stage3_synthesis.run_stage3(run_id, prompt, cfg, force=force_stage in ("stage1", "stage2", "stage3"))

        db.update_run_status(run_id, "verifying_citations")
        if synthesis["status"] == "ok":
            await citations.verify_citations(run_id, cfg, force=force_stage is not None)

        db.update_run_status(run_id, "complete")
    except asyncio.CancelledError:
        # User hit Stop: whatever stage was in flight gets cancelled (asyncio.gather
        # propagates the cancellation into every in-progress provider call), and
        # whatever's already in SQLite from completed calls is kept as-is — a
        # cancelled run can still be resumed later like any other partial run.
        # Any provider call that was still mid-flight never got to write its own
        # final status, so it'd be stuck showing "running" forever without this.
        logger.info("pipeline run %s cancelled", run_id)
        db.mark_stray_running_rows_cancelled(run_id)
        db.update_run_status(run_id, "cancelled")
        raise
    except Exception:
        logger.exception("pipeline run %s failed", run_id)
        db.update_run_status(run_id, "failed")
        raise
