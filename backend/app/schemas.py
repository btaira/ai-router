from __future__ import annotations

from pydantic import BaseModel, Field


class CreateRunRequest(BaseModel):
    prompt: str = Field(min_length=1)
    skip_stage2: bool = False
    stage2_mode: str | None = None  # designated_fact_checkers | full_mesh | diff_then_check
    synthesis_provider: str | None = None
    # Which providers act as designated fact-checkers for this run (used by
    # designated_fact_checkers and diff_then_check modes; full_mesh ignores
    # it and always uses every enabled provider). None = use the
    # deployment's configured default (pipeline.stage2.fact_checkers).
    fact_checkers: list[str] | None = None


class ResumeRunRequest(BaseModel):
    force_stage: str | None = None  # stage1 | stage2 | stage3 | None (resume only what's missing)


class UpdateProviderModelRequest(BaseModel):
    model: str = Field(min_length=1)


class UpdateProviderEnabledRequest(BaseModel):
    enabled: bool


class UpdateProviderParamsRequest(BaseModel):
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)


class UpdateProviderApiKeyRequest(BaseModel):
    api_key: str = Field(min_length=1)


class FollowupRequest(BaseModel):
    message: str = Field(min_length=1)
