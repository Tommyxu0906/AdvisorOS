"""JSON schemas for the Nuwa distillation pipeline."""

from __future__ import annotations

from typing import Any

from app.domain.needs import NEED_DIMENSIONS

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "questions": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
    },
    "required": ["questions", "rationale"],
    "additionalProperties": False,
}

RESEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "findings": {"type": "array", "items": {"type": "string"}},
        "decision_rules": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Transferable rules, not past positions.",
        },
        "limitations": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Where this subject is weak, wrong, or unwilling to opine.",
        },
        "sources": {"type": "array", "items": {"type": "string"}},
        "uncertainty": {
            "type": "string",
            "description": "What you are not confident the subject actually held.",
        },
    },
    "required": ["findings", "decision_rules", "limitations", "sources", "uncertainty"],
    "additionalProperties": False,
}

_EXPERTISE_PROPS: dict[str, Any] = {
    d.value: {"type": "number", "description": "0 to 1."} for d in NEED_DIMENSIONS
}

SYNTHESIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "display_name": {"type": "string"},
        "one_line": {"type": "string", "description": "The subject's edge, in one sentence."},
        "expertise": {
            "type": "object",
            "properties": _EXPERTISE_PROPS,
            "required": [d.value for d in NEED_DIMENSIONS],
            "additionalProperties": False,
        },
        "topic_affinity": {"type": "array", "items": {"type": "string"}},
        "mental_models": {"type": "array", "items": {"type": "string"}},
        "heuristics": {"type": "array", "items": {"type": "string"}},
        "reasoning_rules": {"type": "array", "items": {"type": "string"}},
        "blind_spots": {"type": "array", "items": {"type": "string"}},
        "honest_boundaries": {"type": "array", "items": {"type": "string"}},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "source": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["label", "source", "note"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "display_name",
        "one_line",
        "expertise",
        "topic_affinity",
        "mental_models",
        "heuristics",
        "reasoning_rules",
        "blind_spots",
        "honest_boundaries",
        "evidence",
    ],
    "additionalProperties": False,
}
