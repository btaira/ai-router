"""Cheap, non-LLM claim extraction/diffing used by the `diff_then_check` stage-2 mode.

Splits each stage-1 answer into sentence-level "claims", keeps the ones that
look factual (contain a number, date, or a capitalized proper-noun-like
phrase), and flags a claim as a disagreement candidate if no other answer
contains a sufficiently similar sentence. Only flagged claims get sent to an
LLM for adjudication — this is the "diff first, ask second" cost saver.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_HAS_NUMBER = re.compile(r"\d")
_HAS_PROPER_PHRASE = re.compile(r"\b[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*\b")

SIMILARITY_THRESHOLD = 0.6
HIGH_SIMILARITY_THRESHOLD = 0.85
_NUMBER_TOKEN = re.compile(r"\d+(?:\.\d+)?")


def _numbers(text: str) -> set[str]:
    return set(_NUMBER_TOKEN.findall(text))


def extract_claims(text: str) -> list[str]:
    if not text:
        return []
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    claims = []
    for s in sentences:
        if len(s) < 8:
            continue
        if _HAS_NUMBER.search(s) or len(_HAS_PROPER_PHRASE.findall(s)) >= 1:
            claims.append(s)
    return claims


def _has_close_match(claim: str, candidates: list[str]) -> bool:
    claim_numbers = _numbers(claim)
    for c in candidates:
        ratio = SequenceMatcher(None, claim.lower(), c.lower()).ratio()
        if ratio >= HIGH_SIMILARITY_THRESHOLD:
            return True
        if ratio >= SIMILARITY_THRESHOLD:
            # Similar phrasing alone isn't enough if the claims cite different
            # numbers/dates — that's exactly the kind of disagreement this
            # diff is meant to catch, not paper over.
            if not claim_numbers or claim_numbers & _numbers(c):
                return True
    return False


def find_disagreement_candidates(answers_by_provider: dict[str, str]) -> dict[str, list[str]]:
    """Return {provider: [flagged claim, ...]} for claims with no close match elsewhere."""
    claims_by_provider = {p: extract_claims(t) for p, t in answers_by_provider.items()}
    flagged: dict[str, list[str]] = {}
    for provider, claims in claims_by_provider.items():
        other_claims = [c for p, cs in claims_by_provider.items() if p != provider for c in cs]
        provider_flags = [c for c in claims if not _has_close_match(c, other_claims)]
        if provider_flags:
            flagged[provider] = provider_flags
    return flagged
