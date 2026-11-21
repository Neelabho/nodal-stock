"""Typed schemas for the AI review layer. The model fills these; deterministic code validates them."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field

OUTCOMES = ("CONFIRM", "FLAG", "ALTERNATIVE", "INSUFFICIENT_EVIDENCE")


class AIReview(BaseModel):
    outcome: Literal["CONFIRM", "FLAG", "ALTERNATIVE", "INSUFFICIENT_EVIDENCE"]
    plain_explanation: str = Field(max_length=420, description="1-2 sentences a store manager understands; only facts from the input")
    reasoning: str = Field(max_length=700)
    evidence_refs: list[str] = Field(default_factory=list, max_length=8, description="keys from `facts` that support the outcome")
    risks: list[str] = Field(default_factory=list, max_length=4)
    missing_information: list[str] = Field(default_factory=list, max_length=4)
    confidence: Literal["low", "medium", "high"]
    preferred_candidate: str | None = Field(default=None, description="id of one provided candidate (e.g. ALT-1), or null")


REVIEW_SCHEMA = AIReview.model_json_schema()
