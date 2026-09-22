"""Explicit local opt-in after evaluation. Predictions never grant permissions."""

import hashlib
import json
from pathlib import Path

from .config import atomic_json, private_directory
from .errors import DecisionError
from .models import REVISION


def pipeline_fingerprint() -> str:
    root = Path(__file__).parent
    source = b"".join(
        (root / name).read_bytes()
        for name in (
            "recipes.py",
            "engine.py",
            "budget.py",
            "contracts.py",
            "policy.py",
            "evaluation.py",
        )
    )
    return hashlib.sha256(source + REVISION.encode()).hexdigest()


def enable_policy(home: Path, report: dict) -> dict:
    from .evaluation import summarize

    try:
        metrics = summarize(report["rows"])
        valid = (
            report["report_version"] == 1
            and report["split"] == "holdout"
            and report["model_revision"] == REVISION
            and report["pipeline_fingerprint"] == pipeline_fingerprint()
            and report["recipe"] in {"candidate-relevance@1", "log-triage@1"}
            and metrics["gate_eligible"]
            and len(report["dataset_sha256"]) == 64
        )
        if not valid:
            raise ValueError("Evaluation gate not met")
    except (KeyError, TypeError, ValueError) as error:
        raise DecisionError(
            "evaluation_required", "A current, passing held-out report is required"
        ) from error
    directory = home / "policies"
    private_directory(directory)
    approval = {
        "recipe": report["recipe"],
        "pipeline_fingerprint": pipeline_fingerprint(),
        "model_revision": REVISION,
        "dataset_sha256": report["dataset_sha256"],
        "metrics": metrics,
        "enabled": True,
        "models": sorted({model for row in report["rows"] for model in row.get("models", [])}),
    }
    atomic_json(directory / (report["recipe"] + ".json"), approval)
    return approval


def active_policy(home: Path, recipe: str) -> dict | None:
    if recipe not in {"candidate-relevance@1", "log-triage@1"}:
        return None
    try:
        policy = json.loads((home / "policies" / (recipe + ".json")).read_text())
        if (
            policy["enabled"]
            and policy["pipeline_fingerprint"] == pipeline_fingerprint()
            and policy["model_revision"] == REVISION
        ):
            return policy
    except (OSError, ValueError, KeyError):
        pass
    return None


def apply_policy(items: list[dict], enabled: bool, models: list[str] | None = None) -> list[dict]:
    return [
        {
            **item,
            "disposition": "omit"
            if (
                enabled
                and item["assessment"] == "irrelevant"
                and not item["mandatory"]
                and "error" not in item
                and (
                    models is None or all(part["model"] in models for part in item.get("parts", []))
                )
            )
            else "keep",
        }
        for item in items
    ]
