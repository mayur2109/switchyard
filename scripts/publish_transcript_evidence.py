"""Copy a private transcript report into public docs/evidence without source text.

Reads report.json from a private benchmark directory. Writes only aggregates,
hashes, usage totals, and limitations. Never copies cases.jsonl or row text.
"""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "docs" / "evidence" / "transcript-report.json"


def sanitize(report: dict) -> dict:
    runtime = report.get("runtime") or {}
    models = runtime.get("models") or {}
    revisions = sorted(
        {
            meta.get("revision")
            for meta in models.values()
            if isinstance(meta, dict) and meta.get("revision")
        }
    )
    return {
        "report_version": 1,
        "created_at": report.get("created_at"),
        "split": "transcript-replay",
        "recipe": "candidate-relevance@1",
        "budget_ratio": report.get("budget_ratio"),
        "seed": report.get("seed"),
        "corpus_sha256": report.get("corpus_sha256"),
        "sample_sha256": report.get("sample_sha256"),
        "eligible_unique_cases": report.get("eligible_unique_cases"),
        "counts": report.get("counts"),
        "usage_by_source": report.get("usage_by_source"),
        "token_measurement": report.get("token_measurement"),
        "quality_labels": False,
        "gate_eligible": False,
        "actual_provider_tokens_saved": None,
        "model_revisions": revisions,
        "runtime": {
            "ready": runtime.get("ready"),
            "state": runtime.get("state"),
            "device": runtime.get("device"),
            "models": {
                name: {"revision": meta.get("revision")}
                for name, meta in models.items()
                if isinstance(meta, dict)
            },
        },
        "replay": report.get("replay"),
        "limitations": report.get("limitations", []),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-report", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.from_report.read_text())
    if "replay" not in report:
        raise SystemExit("report.json must include a completed replay summary")
    public = sanitize(report)
    PUBLIC.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC.write_text(json.dumps(public, indent=2) + "\n")
    print(PUBLIC)


if __name__ == "__main__":
    main()
