"""
Shared pricing helpers for model cost estimation.

BurnMap prefers native cost recorded by the source logs. When native cost is
missing, these tables provide a model-family estimate.
"""

PRICING = {
    # Anthropic / Claude
    "claude-opus-4-6":   {"input": 5.00, "output": 25.00, "cache_write": 6.25, "cache_read": 0.50},
    "claude-opus-4-5":   {"input": 5.00, "output": 25.00, "cache_write": 6.25, "cache_read": 0.50},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30},
    "claude-haiku-4-5":  {"input": 1.00, "output": 5.00,  "cache_write": 1.25, "cache_read": 0.10},
    "claude-haiku-4-6":  {"input": 1.00, "output": 5.00,  "cache_write": 1.25, "cache_read": 0.10},

    # OpenAI / GPT-5 family
    # The gpt-5.4 figures are anchored by native-cost Pi rows in the user's
    # data: e.g. 233,032 input + 40 output -> $0.58318 and 246,242 input +
    # 2,469 output -> $0.65264, which match $2.50/M input and $15.00/M output.
    # Cached reads line up at $0.25/M. Codex uses the same model-family rates
    # when native cost is absent.
    "gpt-5.4":       {"input": 2.50, "output": 15.00, "cache_write": 2.50, "cache_read": 0.25},
    "gpt-5.3-codex": {"input": 2.50, "output": 15.00, "cache_write": 2.50, "cache_read": 0.25},
    "gpt-5":         {"input": 2.50, "output": 15.00, "cache_write": 2.50, "cache_read": 0.25},
}


_FAMILY_FALLBACKS = [
    ("claude-opus", "claude-opus-4-6"),
    ("claude-sonnet", "claude-sonnet-4-6"),
    ("claude-haiku", "claude-haiku-4-5"),
    ("gpt-5", "gpt-5"),
]


def is_billable(model):
    return get_pricing(model) is not None


def get_pricing(model):
    if not model:
        return None
    if model in PRICING:
        return PRICING[model]
    for key in PRICING:
        if model.startswith(key):
            return PRICING[key]
    m = model.lower()
    for prefix, canonical in _FAMILY_FALLBACKS:
        if prefix in m:
            return PRICING[canonical]
    return None


def calc_cost(model, inp, out, cache_read, cache_creation):
    if not is_billable(model):
        return 0.0
    p = get_pricing(model)
    if not p:
        return 0.0
    return (
        inp * p["input"] / 1_000_000 +
        out * p["output"] / 1_000_000 +
        cache_read * p["cache_read"] / 1_000_000 +
        cache_creation * p["cache_write"] / 1_000_000
    )
