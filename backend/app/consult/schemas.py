"""The JSON schema one lens must answer in.

Structured rather than prose because the output has to be *ranked*, not just read. A paragraph
saying "I'd be cautious about the NVDA trim" cannot be counted, cross-checked against the
computed action ids, or overridden by the constraint layer. `opposed_action_ids: ["trim_nvda"]`
can be all three.

`confidence_signal` is an enum of three bands and not a number, deliberately — see the note on
`ConfidenceSignal`. A schema that accepted a float would get one, and it would be uncalibrated.
"""

from __future__ import annotations

from typing import Any

CONSULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "stance": {
            "type": "string",
            "enum": ["endorse", "oppose", "mixed", "abstain"],
            "description": (
                "endorse: the computed scenario is right. oppose: it is wrong. mixed: parts of "
                "it. abstain: this sits outside what this framework speaks to."
            ),
        },
        "preferred_candidate_id": {
            "type": ["string", "null"],
            "description": (
                "Which candidate this framework favours, by id. Null when abstaining. Naming a "
                "candidate marked NOT FEASIBLE is permitted and will be recorded and overridden."
            ),
        },
        "supported_action_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Action ids from the computed scenario this framework backs.",
        },
        "opposed_action_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Action ids this framework would not carry out, and why in rationale.",
        },
        "rationale": {
            "type": "string",
            "description": (
                "What the framework weighs and why, in plain language. Describe what the "
                "framework favours — never write in the subject's voice or first person."
            ),
        },
        "risks_or_missing_information": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "What the computation cannot see. This is where a framework earns its place: "
                "the engine does not know this person is about to change jobs."
            ),
        },
        "confidence_signal": {
            "type": "string",
            "enum": ["low", "medium", "high"],
            "description": "A band. Never a probability — stated model confidence is not calibrated.",
        },
        "declined": {
            "type": "boolean",
            "description": "True when the framework's honest boundaries say it should not opine.",
        },
        "declined_reason": {"type": "string"},
    },
    "required": [
        "stance",
        "preferred_candidate_id",
        "supported_action_ids",
        "opposed_action_ids",
        "rationale",
        "risks_or_missing_information",
        "confidence_signal",
        "declined",
        "declined_reason",
    ],
    "additionalProperties": False,
}
