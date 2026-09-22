"""Preflight Laya 0.3.5 sequence budgets without silently clipping input."""

import json

from .contracts import Question
from .errors import DecisionError


def check_budget(tokenizer, state, question: Question, *, max_len: int, head_max_len: int):
    criteria = question.criteria
    if question.type == "choice":
        options = [
            key if value in (None, "") else f"{key}: {value}" for key, value in criteria.items()
        ]
    elif question.type == "score":
        options = [f"level {i}: {value}" for i, value in enumerate(criteria)]
    else:
        criteria = criteria or {}
        options = [
            "false: " + (criteria.get("false") or "no, the statement does not hold"),
            "true: " + (criteria.get("true") or "yes, the statement holds"),
        ]

    def count(text):
        clean = text.replace(tokenizer.mask_token, " ")
        return len(tokenizer(clean, add_special_tokens=False)["input_ids"])

    head = count(f"{question.type} question: {question.instructions}")
    option_lengths = [count(" " + option) for option in options]
    option_tokens = sum(length + 1 for length in option_lengths)
    text = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
    state_tokens = count(text)
    # Upstream caps each option at 48 tokens and redistributes the head budget.
    if (
        any(length > 48 for length in option_lengths)
        or head_max_len - option_tokens < 16
        or head > max(8, head_max_len - option_tokens)
        or 4 + head + option_tokens + state_tokens > max_len
    ):
        raise DecisionError("input_too_large", "Input would be truncated; shorten or split it")
    return {
        "state_tokens": state_tokens,
        "sequence_tokens": 4 + head + option_tokens + state_tokens,
    }
