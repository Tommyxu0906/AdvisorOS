"""JSON schemas for structured committee output.

Constraints are kept simple on purpose: the API's structured-output support rejects numeric and
string length constraints, so validation of ranges happens on our side after parsing.
"""

from __future__ import annotations

from typing import Any

ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "thesis": {"type": "string", "description": "One or two sentences stating your position."},
        "reasoning": {"type": "string", "description": "Why you hold it, in your own style."},
        "recommendations": {"type": "array", "items": {"type": "string"}},
        "risks_flagged": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number", "description": "0 to 1."},
        "declined": {
            "type": "boolean",
            "description": "True if this falls outside your boundaries.",
        },
        "declined_reason": {"type": "string"},
    },
    "required": [
        "thesis",
        "reasoning",
        "recommendations",
        "risks_flagged",
        "confidence",
        "declined",
        "declined_reason",
    ],
    "additionalProperties": False,
}

CRITIQUE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "agreement": {"type": "string"},
        "disagreement": {"type": "string"},
        "strength": {"type": "number", "description": "0 to 1: how strongly you disagree."},
    },
    "required": ["agreement", "disagreement", "strength"],
    "additionalProperties": False,
}

RISK_CHALLENGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "scenarios": {"type": "array", "items": {"type": "string"}},
        "unaddressed_risks": {"type": "array", "items": {"type": "string"}},
        "worst_case": {"type": "string"},
    },
    "required": ["scenarios", "unaddressed_risks", "worst_case"],
    "additionalProperties": False,
}

SYNTHESIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "consensus": {"type": "array", "items": {"type": "string"}},
        "disagreements": {"type": "array", "items": {"type": "string"}},
        "recommended_actions": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "summary",
        "consensus",
        "disagreements",
        "recommended_actions",
        "open_questions",
    ],
    "additionalProperties": False,
}
