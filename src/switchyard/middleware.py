"""Small adapters for embedding Switchyard in custom agent loops."""

import copy
from collections.abc import Sequence
from typing import Any

from .contracts import SelectionRequest
from .errors import DecisionError
from .providers import normalize_candidates


def _request(
    task: str,
    items: Sequence[dict[str, Any]],
    budget_bytes: int,
    *,
    recipe: str = "candidate-relevance@1",
    budget_tokens: int | None = None,
    model: str = "auto",
    timeout_ms: int = 30000,
) -> SelectionRequest:
    return SelectionRequest(
        recipe=recipe,
        task=task,
        items=normalize_candidates(list(items)),
        budget_bytes=budget_bytes,
        budget_tokens=budget_tokens,
        model=model,
        timeout_ms=timeout_ms,
    )


def select_context(
    client,
    task: str,
    items: Sequence[dict[str, Any]],
    budget_bytes: int,
    *,
    recipe: str = "candidate-relevance@1",
    budget_tokens: int | None = None,
    model: str = "auto",
    timeout_ms: int = 30000,
) -> dict:
    """Apply the typed selection contract in a synchronous harness."""
    request = _request(
        task,
        items,
        budget_bytes,
        recipe=recipe,
        budget_tokens=budget_tokens,
        model=model,
        timeout_ms=timeout_ms,
    )
    try:
        return client.select_items(request)
    except DecisionError as error:
        payload = [item.model_dump() for item in request.items]
        return {
            "mode": "fallback",
            "applied": False,
            "selected_ids": [item.id for item in request.items],
            "selected_items": payload,
            "recommended_selected_ids": [item.id for item in request.items],
            "recommended_selected_items": payload,
            "error": error.as_dict(),
        }


async def aselect_context(
    client,
    task: str,
    items: Sequence[dict[str, Any]],
    budget_bytes: int,
    *,
    recipe: str = "candidate-relevance@1",
    budget_tokens: int | None = None,
    model: str = "auto",
    timeout_ms: int = 30000,
) -> dict:
    """Apply the typed selection contract in an asynchronous harness."""
    request = _request(
        task,
        items,
        budget_bytes,
        recipe=recipe,
        budget_tokens=budget_tokens,
        model=model,
        timeout_ms=timeout_ms,
    )
    try:
        return await client.select_items(request)
    except DecisionError as error:
        payload = [item.model_dump() for item in request.items]
        return {
            "mode": "fallback",
            "applied": False,
            "selected_ids": [item.id for item in request.items],
            "selected_items": payload,
            "recommended_selected_ids": [item.id for item in request.items],
            "recommended_selected_items": payload,
            "error": error.as_dict(),
        }


def forward_candidates(envelope: dict, plan: dict | None) -> dict:
    """Return a copied harness envelope with the plan's candidate payload applied."""
    forwarded = copy.deepcopy(envelope)
    if not isinstance(plan, dict):
        return forwarded
    candidates = plan.get("selected_items" if plan.get("applied") else "recommended_selected_items")
    if not isinstance(candidates, list):
        return forwarded
    decision_candidates = forwarded.get("decision_candidates")
    if not isinstance(decision_candidates, dict) or not isinstance(
        decision_candidates.get("items"), list
    ):
        return forwarded
    decision_candidates["items"] = copy.deepcopy(candidates)
    return forwarded
