"""Version 1 request contracts shared by Python, CLI, and MCP."""

import json
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from .errors import DecisionError

ModelName = Literal["auto", "english", "multilingual", "typed-decisions"]
RecipeName = Literal["candidate-relevance@1", "log-triage@1", "route-selection@1"]
SelectionRecipeName = Literal["candidate-relevance@1", "log-triage@1"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Question(StrictModel):
    type: Literal["choice", "score", "noul"]
    instructions: str = Field(min_length=1, max_length=8192)
    criteria: dict[str, str | None] | list[str] | None = None

    @model_validator(mode="after")
    def validate_criteria(self):
        if not self.instructions.strip():
            raise ValueError("Instructions must not be blank")
        criteria = self.criteria
        if self.type == "choice":
            if not isinstance(criteria, dict) or not 2 <= len(criteria) <= 20:
                raise ValueError("choice requires 2–20 named options")
            if any(not key.strip() or len(key) > 128 for key in criteria):
                raise ValueError("Option names must contain 1–128 characters")
        elif self.type == "score":
            if not isinstance(criteria, list) or not 2 <= len(criteria) <= 10:
                raise ValueError("score requires 2–10 ordered descriptions")
            if any(not value.strip() for value in criteria):
                raise ValueError("Score descriptions must not be blank")
        elif criteria is not None:
            if not isinstance(criteria, dict) or set(criteria) != {"false", "true"}:
                raise ValueError("noul criteria must contain false and true")
        return self


class DecisionRequest(StrictModel):
    state: str | dict[str, JsonValue] | list[JsonValue]
    questions: dict[str, Question] = Field(min_length=1, max_length=32)
    model: ModelName = "auto"
    timeout_ms: int = Field(default=30000, ge=100, le=120000)

    @field_validator("state")
    @classmethod
    def finite_json(cls, value):
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode()) > 512_000:
            raise ValueError("State exceeds 512 KB")
        return value

    @field_validator("questions")
    @classmethod
    def question_ids(cls, value):
        if any(not key.strip() or len(key) > 128 for key in value):
            raise ValueError("Question IDs must contain 1–128 characters")
        return value


class Item(StrictModel):
    id: str = Field(min_length=1, max_length=256)
    text: str = Field(max_length=128000)
    citation: str | None = Field(default=None, max_length=2048)
    trust: str | None = Field(default=None, max_length=128)
    mandatory: bool = False
    exit_code: int | None = None
    level: str | None = Field(default=None, max_length=32)


class ItemsRequest(StrictModel):
    recipe: RecipeName
    task: str = Field(min_length=1, max_length=8192)
    items: list[Item] = Field(min_length=1, max_length=128)
    routes: dict[str, str | None] | None = None
    model: ModelName = "auto"
    timeout_ms: int = Field(default=30000, ge=100, le=120000)

    @model_validator(mode="after")
    def unique_items(self):
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("Item IDs must be unique")
        if sum(len(item.text.encode()) for item in self.items) > 512_000:
            raise ValueError("Items exceed 512 KB")
        if self.recipe == "route-selection@1":
            Question(type="choice", instructions=self.task, criteria=self.routes)
        elif self.routes is not None:
            raise ValueError("routes is only valid for route-selection@1")
        return self


class SelectionRequest(StrictModel):
    recipe: SelectionRecipeName
    task: str = Field(min_length=1, max_length=8192)
    items: list[Item] = Field(min_length=1, max_length=128)
    budget_bytes: int = Field(ge=1, le=2_000_000)
    budget_tokens: int | None = Field(default=None, ge=1, le=500_000)
    model: ModelName = "auto"
    timeout_ms: int = Field(default=30000, ge=100, le=120000)

    @model_validator(mode="after")
    def validate_items(self):
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("Item IDs must be unique")
        if sum(len(item.text.encode()) for item in self.items) > 512_000:
            raise ValueError("Items exceed 512 KB")
        return self


def validate_answers(questions: dict[str, Question], answers: dict) -> dict:
    """Reject invalid predictions rather than inventing a default label."""

    def probability(value):
        return isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value <= 1

    try:
        if set(answers) != set(questions):
            raise ValueError("answer keys do not match questions")
        for key, question in questions.items():
            answer = answers[key]
            if answer["type"] != question.type or not probability(answer["confidence"]):
                raise ValueError("invalid answer type or confidence")
            if question.type == "noul":
                if not probability(answer["noul"]):
                    raise ValueError("invalid noul probability")
            else:
                expected = (
                    set(question.criteria)
                    if question.type == "choice"
                    else {str(i) for i in range(len(question.criteria))}
                )
                probs = answer["probabilities"]
                if (
                    set(probs) != expected
                    or not all(probability(value) for value in probs.values())
                    or abs(sum(probs.values()) - 1) >= 0.005
                ):
                    raise ValueError("invalid probability distribution")
                if question.type == "choice":
                    if (
                        answer["choice"] not in expected
                        or answer["choice"] != max(probs, key=probs.get)
                    ):
                        raise ValueError("invalid choice")
                else:
                    if (
                        not math.isfinite(answer["score"])
                        or not 0 <= answer["score"] <= len(expected) - 1
                    ):
                        raise ValueError("invalid score")
        return answers
    except (AssertionError, KeyError, TypeError, ValueError) as error:
        raise DecisionError("invalid_model_output", "Model returned an invalid answer") from error
