"""Citation verification — the one part of this pipeline that must never be
an LLM call. A citation only counts as "verified" if:

1. It appears verbatim in the synthesis output, AND
2. A live HTTP HEAD (falling back to GET) returns a 2xx/3xx status, AND
3. It resolves to a public address (private/loopback/link-local targets are
   refused so the pipeline can't be used as an SSRF vector via a model
   hallucinating an internal URL).

Citations that also appeared verbatim in one of the stage-1 source answers
are additionally flagged `found_in_sources` for extra confidence, but that
alone never marks something "verified" — the live check is mandatory.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

import httpx

from .. import db
from ..config import AppConfig
from .stage3_synthesis import extract_urls


async def _is_public_host(hostname: str) -> bool:
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, hostname, None)
    except socket.gaierror:
        return False
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


_HEAD_UNSUPPORTED_STATUSES = {403, 405, 501}


async def _check_url(client: httpx.AsyncClient, url: str) -> tuple[int | None, str | None, str | None]:
    try:
        resp = await client.head(url)
        if resp.status_code in _HEAD_UNSUPPORTED_STATUSES:
            # Server doesn't support HEAD (or blocks it) — fall back to GET.
            # Any other status (including 404/5xx) is a real answer, not a cue to retry.
            resp = await client.get(url)
            return resp.status_code, "GET", None
        return resp.status_code, "HEAD", None
    except httpx.HTTPError:
        try:
            resp = await client.get(url)
            return resp.status_code, "GET", None
        except httpx.HTTPError as exc:
            return None, None, f"{type(exc).__name__}: {exc}"


async def _verify_one(client: httpx.AsyncClient, url: str, retries: int) -> tuple[int | None, bool, str | None, str | None]:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None, False, None, "unsupported or missing URL scheme"

    if not await _is_public_host(parsed.hostname):
        return None, False, None, "blocked: hostname resolves to a private/internal/reserved address"

    last_error = None
    for attempt in range(retries + 1):
        status_code, method, error = await _check_url(client, url)
        if status_code is not None:
            verified = 200 <= status_code < 400
            return status_code, verified, method, None
        last_error = error
    return None, False, None, last_error or "request failed"


async def verify_citations(run_id: str, cfg: AppConfig, force: bool = False) -> list[dict]:
    synthesis = db.get_synthesis_result(run_id)
    if not synthesis or synthesis["status"] != "ok" or not synthesis.get("synthesis_text"):
        return []

    urls = extract_urls(synthesis["synthesis_text"])
    if not urls:
        return []

    existing = {} if force else {r["url"]: r for r in db.get_citation_verifications(run_id)}
    to_check = [u for u in urls if u not in existing]

    if to_check:
        stage1_rows = db.get_stage1_responses(run_id)
        source_text = "\n".join((r.get("response_text") or "") for r in stage1_rows)

        async with httpx.AsyncClient(
            timeout=cfg.stages.citation_timeout,
            follow_redirects=True,
            headers={"User-Agent": cfg.stages.citation_user_agent},
        ) as client:
            outcomes = await asyncio.gather(
                *(_verify_one(client, url, cfg.stages.citation_retries) for url in to_check)
            )

        for url, (status_code, verified, method, error) in zip(to_check, outcomes):
            found_in_sources = url in source_text
            db.upsert_citation_verification(
                run_id=run_id, url=url, found_in_sources=found_in_sources,
                http_status=status_code, verified=verified, method=method, error=error,
            )

    return db.get_citation_verifications(run_id)
