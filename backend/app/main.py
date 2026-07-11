from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

# Load ../.env (repo root) before anything reads provider API keys from the
# environment. Explicit path so this works regardless of the CWD uvicorn was
# launched from (e.g. `backend/` vs repo root) — matters most on Windows,
# where there's no `source .env` equivalent. override=True so a value set in
# .env always wins over a stray same-named var already in the shell (e.g. a
# local tool like LM Studio exporting its own OPENAI_API_KEY) — this app's
# config should be the single source of truth for its own provider keys.
load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent.parent / ".env", override=True)

from . import db  # noqa: E402
from .routes.runs import router as runs_router  # noqa: E402

app = FastAPI(title="AI Router — Multi-LLM Consensus & Fact-Check Engine")


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


app.include_router(runs_router)

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
