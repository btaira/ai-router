from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import db
from .routes.runs import router as runs_router

app = FastAPI(title="AI Router — Multi-LLM Consensus & Fact-Check Engine")


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


app.include_router(runs_router)

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
