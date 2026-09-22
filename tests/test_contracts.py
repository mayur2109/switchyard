import pytest
from pydantic import ValidationError


def test_valid_typed_questions():
    from local_decision_kit.contracts import DecisionRequest

    request = DecisionRequest.model_validate(
        {
            "state": {"message": "Fix the failing build"},
            "questions": {
                "route": {
                    "type": "choice",
                    "instructions": "Pick a team",
                    "criteria": {"engineering": "builds and software", "billing": "payments"},
                },
                "urgent": {"type": "noul", "instructions": "Is this urgent?"},
                "severity": {
                    "type": "score",
                    "instructions": "Rate severity",
                    "criteria": ["routine", "blocking"],
                },
            },
        }
    )
    assert request.model == "auto"
    assert request.questions["urgent"].type == "noul"


@pytest.mark.parametrize(
    "question",
    [
        {"type": "choice", "instructions": "Choose", "criteria": {"a": "one"}},
        {"type": "score", "instructions": "Rate", "criteria": ["one"]},
        {"type": "noul", "instructions": ""},
        {"type": "noul", "instructions": "Check", "criteria": {"x": "bad"}},
        {"type": "generate", "instructions": "Write code"},
    ],
)
def test_invalid_question_rejected(question):
    from local_decision_kit.contracts import DecisionRequest

    with pytest.raises(ValidationError):
        DecisionRequest(state="sample", questions={"q": question})


def test_state_rejects_non_finite_numbers_and_extra_fields():
    from local_decision_kit.contracts import DecisionRequest

    with pytest.raises(ValidationError):
        DecisionRequest(state={"value": float("nan")}, questions={})
    with pytest.raises(ValidationError):
        DecisionRequest(
            state="x",
            questions={"q": {"type": "noul", "instructions": "Check"}},
            command="rm -rf something",
        )


def test_duplicate_items_rejected():
    from local_decision_kit.contracts import ItemsRequest

    with pytest.raises(ValidationError):
        ItemsRequest(
            recipe="candidate-relevance@1",
            task="context",
            items=[{"id": "a", "text": "one"}, {"id": "a", "text": "two"}],
        )


def test_rejects_silent_head_and_state_truncation():
    from local_decision_kit.budget import check_budget
    from local_decision_kit.contracts import Question
    from local_decision_kit.errors import DecisionError

    class Tokenizer:
        mask_token = "[MASK]"

        def __call__(self, text, **kwargs):
            return {"input_ids": text.split()}

    question = Question(type="noul", instructions="Does this apply?")
    check_budget(Tokenizer(), "small input", question, max_len=100, head_max_len=50)
    with pytest.raises(DecisionError, match="input_too_large"):
        check_budget(Tokenizer(), "word " * 100, question, max_len=100, head_max_len=50)
    with pytest.raises(DecisionError, match="input_too_large"):
        check_budget(
            Tokenizer(),
            "small",
            Question(
                type="choice", instructions="Choose", criteria={"a": "word " * 100, "b": "other"}
            ),
            max_len=512,
            head_max_len=192,
        )


def test_malformed_model_answer_is_error_not_negative():
    from local_decision_kit.contracts import Question, validate_answers
    from local_decision_kit.errors import DecisionError

    questions = {"q": Question(type="noul", instructions="Check")}
    with pytest.raises(DecisionError, match="invalid_model_output"):
        validate_answers(questions, {"q": {"noul": float("nan")}})
    with pytest.raises(DecisionError, match="invalid_model_output"):
        validate_answers(questions, {})
