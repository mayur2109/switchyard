"""Opt-in hook recipe for tools that emit the documented candidate envelope."""

from .client import Client
from .contracts import ItemsRequest


def process_event(event: dict, client=None) -> dict:
    """Fail open. Shadow evaluation adds no tokens and never grants permission."""
    if event.get("hook_event_name") != "PostToolUse":
        return {}
    output = event.get("tool_response")
    if not isinstance(output, dict) or not isinstance(output.get("decision_candidates"), dict):
        return {}
    try:
        request = ItemsRequest.model_validate(output["decision_candidates"])
        request.timeout_ms = min(request.timeout_ms, 2000)
        (client or Client()).evaluate_items(request)
    except Exception:
        return {}
    return {}
