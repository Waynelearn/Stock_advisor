"""Phase-3 structured-output schemas for LLM responses that feed code.

When an LLM answer is *consumed by code* (not just shown to the user) it must be
parseable, not prose. Pass one of these to ``complete(..., schema=...)`` — the
Anthropic provider wires it into ``output_config.format`` — then parse the JSON
result with :func:`parse`, which validates the required keys are present.

Free-text Q&A (the agent) stays prose + guardrail; these are for the actionable,
machine-read paths: a trade decision, a position assessment.
"""
from __future__ import annotations

import json
from typing import Any

# A graded directional/risk verdict on the held position.
POSITION_ASSESSMENT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "stance": {
            "type": "string",
            "enum": ["hold", "trim", "add", "roll", "close"],
            "description": "The single recommended action on the current spread.",
        },
        "conviction": {
            "type": "integer",
            "minimum": 1,
            "maximum": 5,
            "description": "Confidence in the stance, 1 (low) to 5 (high).",
        },
        "rationale": {
            "type": "string",
            "description": "One or two sentences, grounded in the supplied data.",
        },
        "key_risks": {
            "type": "array",
            "items": {"type": "string"},
            "description": "The main risks to the stance.",
        },
    },
    "required": ["stance", "conviction", "rationale"],
    "additionalProperties": False,
}

# A go/no-go on a candidate spread, with the numbers code needs to act.
TRADE_DECISION: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": ["enter", "pass", "wait"],
            "description": "Whether to take the proposed trade.",
        },
        "max_contracts": {
            "type": "integer",
            "minimum": 0,
            "description": "Position-size ceiling implied by risk budget.",
        },
        "rationale": {"type": "string"},
    },
    "required": ["decision", "rationale"],
    "additionalProperties": False,
}


def parse(text: str, schema: dict) -> dict:
    """Parse a schema-constrained completion's text into a validated dict.

    Raises ``ValueError`` if the text isn't JSON or a required key is missing —
    a loud failure at the code boundary beats silently acting on a bad shape.
    """
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as e:
        raise ValueError(f"structured output was not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"structured output was {type(data).__name__}, expected object")
    missing = [k for k in schema.get("required", []) if k not in data]
    if missing:
        raise ValueError(f"structured output missing required keys: {missing}")
    return data
