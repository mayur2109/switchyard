"""Local evaluation reports. Content estimates are not provider billing claims."""

import hashlib
import math
import platform
import statistics
import time
from datetime import UTC, datetime
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Item, ItemsRequest, StrictModel
from .models import REVISION


class Case(StrictModel):
    id: str = Field(min_length=1)
    task: str = Field(min_length=1)
    items: list[Item] = Field(min_length=1, max_length=128)
    relevant: dict[str, bool]

    @model_validator(mode="after")
    def labels_match(self):
        ids = [item.id for item in self.items]
        if len(set(ids)) != len(ids) or set(ids) != set(self.relevant):
            raise ValueError("Every unique item requires an explicit relevance label")
        return self


class Dataset(StrictModel):
    split: Literal["smoke", "holdout"]
    recipe: Literal["candidate-relevance@1", "log-triage@1"]
    cases: list[Case] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def distinct_cases(self):
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("Case IDs must be unique")
        pairs = [(case.task, item.text) for case in self.cases for item in case.items]
        if len(set(pairs)) != len(pairs):
            raise ValueError("Repeated task/item pairs would inflate evaluation counts")
        return self


def summarize(rows: list[dict]) -> dict:
    relevant = [row for row in rows if row["relevant"]]
    mandatory = [row for row in rows if row["mandatory"]]
    retained = sum(not row["would_omit"] for row in relevant)
    retention = retained / len(relevant) if relevant else None
    protected = all(not row["would_omit"] for row in mandatory)
    errors = sum(row["error"] for row in rows)
    latencies = sorted(row["elapsed_ms"] for row in rows)
    removed = sum(row["input_chars"] - row["selected_chars"] for row in rows)
    return {
        "items": len(rows),
        "relevant_items": len(relevant),
        "mandatory_items": len(mandatory),
        "relevant_retention": retention,
        "mandatory_preserved": protected,
        "errors": errors,
        "would_omit": sum(row["would_omit"] for row in rows),
        "p50_ms": statistics.median(latencies) if latencies else None,
        "p95_ms": latencies[max(0, math.ceil(len(latencies) * 0.95) - 1)] if latencies else None,
        "estimated_content_tokens_removed": removed / 4,
        "estimate_method": "Unicode characters / 4; excludes agent overhead, not billing",
        "actual_provider_tokens_saved": None,
        "gate_eligible": (
            len(rows) >= 200
            and len(relevant) >= 100
            and len(mandatory) >= 10
            and retention is not None
            and retention >= 0.99
            and protected
            and errors == 0
            and removed > 0
        ),
    }


def benchmark(client, dataset: Dataset) -> dict:
    from .policy import pipeline_fingerprint

    rows = []
    began = time.monotonic()
    for case in dataset.cases:
        started = time.monotonic()
        request = ItemsRequest(
            recipe=dataset.recipe, task=case.task, items=case.items, timeout_ms=120000
        )
        result = client.evaluate_items(request)
        latency = (time.monotonic() - started) * 1000
        for item in result["items"]:
            would_omit = (
                item["assessment"] == "irrelevant" and not item["mandatory"] and "error" not in item
            )
            # A transparent lexical baseline; no model involved.
            words = {word.lower().strip(".,:;?!") for word in case.task.split() if len(word) > 3}
            lexical_keep = item["mandatory"] or any(word in item["text"].lower() for word in words)
            rows.append(
                {
                    "id": f"{case.id}/{item['id']}",
                    "relevant": case.relevant[item["id"]],
                    "mandatory": item["mandatory"],
                    "would_omit": would_omit,
                    "error": "error" in item,
                    "elapsed_ms": round(latency, 2),
                    "input_chars": len(item["text"]),
                    "selected_chars": 0 if would_omit else len(item["text"]),
                    "lexical_keep": lexical_keep,
                    "models": sorted({part["model"] for part in item["parts"]}),
                }
            )
    metrics = summarize(rows)
    metrics["gate_eligible"] &= dataset.split == "holdout"
    relevant = [row for row in rows if row["relevant"]]
    return {
        "report_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "split": dataset.split,
        "recipe": dataset.recipe,
        "model_revision": REVISION,
        "pipeline_fingerprint": pipeline_fingerprint(),
        "dataset_sha256": hashlib.sha256(dataset.model_dump_json().encode()).hexdigest(),
        "platform": platform.platform(),
        "elapsed_seconds": time.monotonic() - began,
        "metrics": metrics,
        "rows": rows,
        "baseline": {
            "unfiltered_relevant_retention": 1.0,
            "lexical_relevant_retention": (
                sum(row["lexical_keep"] for row in relevant) / len(relevant) if relevant else None
            ),
        },
        "limitations": [
            "No coding-agent task completion or provider billing measured",
            "Per-item latency records its enclosing request latency",
            "Holdout provenance is supplied by the dataset author",
        ],
    }
