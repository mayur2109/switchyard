import pytest


class FakeEngine:
    def __init__(self, answer=0.01, fail=False):
        self.answer = answer
        self.fail = fail

    def decide(self, request):
        if self.fail:
            from local_decision_kit.errors import DecisionError

            raise DecisionError("model_unavailable", "Test model unavailable")
        return {
            "answers": {"relevant": {"type": "noul", "noul": self.answer}},
            "model": "english",
            "revision": "test",
            "elapsed_ms": 1,
        }


def test_relevance_preserves_ids_trust_and_mandatory_items():
    from local_decision_kit.contracts import ItemsRequest
    from local_decision_kit.recipes import evaluate_items

    result = evaluate_items(
        FakeEngine(),
        ItemsRequest(
            recipe="candidate-relevance@1",
            task="offline support",
            items=[
                {"id": "one", "text": "Context", "citation": "doc.md:5", "trust": "unverified"},
                {"id": "two", "text": "Policy", "mandatory": True},
            ],
        ),
    )
    assert [item["id"] for item in result["items"]] == ["one", "two"]
    assert result["items"][0]["citation"] == "doc.md:5"
    assert result["items"][0]["trust"] == "unverified"
    assert all(item["disposition"] == "keep" for item in result["items"])
    assert result["mode"] == "advisory"


def test_failed_inference_keeps_input_and_reports_failure():
    from local_decision_kit.contracts import ItemsRequest
    from local_decision_kit.recipes import evaluate_items

    result = evaluate_items(
        FakeEngine(fail=True),
        ItemsRequest(
            recipe="candidate-relevance@1", task="test", items=[{"id": "x", "text": "Keep me"}]
        ),
    )
    assert result["items"][0]["disposition"] == "keep"
    assert result["items"][0]["text"] == "Keep me"
    assert result["items"][0]["error"]["code"] == "model_unavailable"


def test_log_exit_status_is_not_overridden_by_model():
    from local_decision_kit.contracts import ItemsRequest
    from local_decision_kit.recipes import evaluate_items

    request = ItemsRequest(
        recipe="log-triage@1",
        task="test results",
        items=[
            {"id": "exit", "text": "exit code: 1", "exit_code": 1},
            {"id": "error", "text": "ERROR unable to run", "level": "error"},
        ],
    )
    result = evaluate_items(FakeEngine(), request)
    assert all(item["mandatory"] for item in result["items"])
    assert result["items"][0]["exit_code"] == 1


def test_unknown_recipe_rejected():
    from local_decision_kit.contracts import ItemsRequest

    with pytest.raises(ValueError):
        ItemsRequest(recipe="execute-shell@1", task="x", items=[])
