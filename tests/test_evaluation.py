import copy

import pytest


def test_measurements_separate_predictions_from_verified_facts():
    from local_decision_kit.evaluation import summarize

    rows = [
        {
            "id": "a",
            "relevant": True,
            "mandatory": True,
            "would_omit": False,
            "error": False,
            "elapsed_ms": 10,
            "input_chars": 100,
            "selected_chars": 100,
        },
        {
            "id": "b",
            "relevant": True,
            "mandatory": False,
            "would_omit": True,
            "error": False,
            "elapsed_ms": 20,
            "input_chars": 100,
            "selected_chars": 0,
        },
        {
            "id": "c",
            "relevant": False,
            "mandatory": False,
            "would_omit": True,
            "error": False,
            "elapsed_ms": 30,
            "input_chars": 100,
            "selected_chars": 0,
        },
    ]
    result = summarize(rows)
    assert result["relevant_retention"] == 0.5
    assert result["gate_eligible"] is False
    assert result["actual_provider_tokens_saved"] is None
    assert result["estimated_content_tokens_removed"] == 50


def test_small_fixture_set_cannot_enable_omission():
    from local_decision_kit.evaluation import summarize

    row = {
        "id": "a",
        "relevant": True,
        "mandatory": True,
        "would_omit": False,
        "error": False,
        "elapsed_ms": 10,
        "input_chars": 100,
        "selected_chars": 100,
    }
    assert summarize([row])["gate_eligible"] is False


def test_gate_requires_heldout_evidence_and_current_recipe(tmp_path):
    from local_decision_kit.errors import DecisionError
    from local_decision_kit.policy import enable_policy

    with pytest.raises(DecisionError, match="evaluation_required"):
        enable_policy(tmp_path, {"metrics": {"gate_eligible": True}})


def test_active_policy_never_drops_uncertain_or_mandatory_items():
    from local_decision_kit.policy import apply_policy

    items = [
        {"id": "a", "assessment": "irrelevant", "mandatory": True},
        {"id": "b", "assessment": "uncertain", "mandatory": False},
        {"id": "c", "assessment": "irrelevant", "mandatory": False},
        {"id": "d", "assessment": "irrelevant", "mandatory": False, "error": {}},
    ]
    original = copy.deepcopy(items)
    result = apply_policy(items, enabled=True)
    assert [item["disposition"] for item in result] == ["keep", "keep", "omit", "keep"]
    assert items == original


def test_report_rejects_duplicate_case_ids_and_missing_labels():
    from local_decision_kit.evaluation import Dataset

    with pytest.raises(ValueError):
        Dataset.model_validate(
            {
                "split": "holdout",
                "recipe": "candidate-relevance@1",
                "cases": [
                    {
                        "id": "case",
                        "task": "test",
                        "items": [{"id": "x", "text": "hello"}],
                        "relevant": {},
                    }
                ],
            }
        )
