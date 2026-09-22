import pytest


def item(item_id, text, *, mandatory=False, level=None):
    value = {"id": item_id, "text": text, "mandatory": mandatory}
    if level is not None:
        value["level"] = level
    return value


def request(**overrides):
    from switchyard.contracts import SelectionRequest

    value = {
        "recipe": "candidate-relevance@1",
        "task": "billing incident",
        "items": [item("a", "billing details"), item("b", "unrelated")],
        "budget_bytes": 10_000,
    }
    value.update(overrides)
    return SelectionRequest.model_validate(value)


def test_selection_request_requires_a_positive_budget_and_unique_items():
    with pytest.raises(ValueError):
        request(budget_bytes=0)
    with pytest.raises(ValueError):
        request(items=[item("a", "one"), item("a", "two")])
    assert request(budget_tokens=10).budget_tokens == 10


def test_select_items_preserves_mandatory_items_and_fills_budget_by_score():
    from switchyard.recipes import select_items

    class Engine:
        def decide(self, decision):
            text = decision.state["text"]
            score = {"mandatory": 0.01, "high": 0.99, "medium": 0.8, "low": 0.05}[text]
            return {
                "answers": {"relevant": {"type": "noul", "noul": score, "confidence": 0.9}},
                "model": "english",
                "revision": "test",
            }

    result = select_items(
        Engine(),
        request(
            items=[
                item("m", "mandatory", mandatory=True),
                item("h", "high"),
                item("x", "medium"),
                item("l", "low"),
            ],
            budget_bytes=210,
        ),
    )

    assert result["recommended_selected_ids"] == ["m", "h"]
    assert result["selected_ids"] == ["m", "h", "x", "l"]
    assert [candidate["id"] for candidate in result["selected_items"]] == ["m", "h", "x", "l"]
    assert result["items"][0]["disposition"] == "keep"
    assert result["items"][2]["recommended_disposition"] == "omit"
    assert result["applied"] is False
    assert result["recommended_selected_bytes"] <= result["budget_bytes"]


def test_select_items_applies_only_policy_approved_omissions():
    from switchyard.recipes import select_items

    class Engine:
        settings = None

        def decide(self, decision):
            score = 0.01 if decision.state["text"] == "drop" else 0.99
            return {
                "answers": {"relevant": {"type": "noul", "noul": score, "confidence": 0.9}},
                "model": "english",
                "revision": "test",
            }

    result = select_items(Engine(), request(items=[item("keep", "keep"), item("drop", "drop")]))

    assert result["applied"] is False
    assert result["selected_ids"] == ["keep", "drop"]
