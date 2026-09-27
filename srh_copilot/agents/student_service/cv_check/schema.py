"""JSON schema of the tiered CV review, the same shape the prompts describe.

Providers that support structured output (OpenAI, Azure, the self-hosted vLLM
server) enforce it while generating, so the review is always parsable. Gemini
ignores it and the task falls back to parse_json() on free text.

Only types and required keys are used, no numeric ranges or string formats:
not every structured-output engine implements those keywords.
"""

CV_REVIEW_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "overall_score": {"type": "integer"},
        "tier_1": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "detail": {"type": "string"}, "fix": {"type": "string"}},
                "required": ["title", "detail", "fix"],
            },
        },
        "tier_2": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "detail": {"type": "string"}},
                "required": ["title", "detail"],
            },
        },
        "tier_3": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
        "ready": {"type": "boolean"},
    },
    "required": ["overall_score", "tier_1", "tier_2", "tier_3", "summary", "ready"],
}
