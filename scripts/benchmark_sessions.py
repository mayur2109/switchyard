"""Private transcript inventory and deterministic, unlabeled Switchyard replay.

Run with the project interpreter. Outputs contain aggregate counts and opaque hashes,
never source text. Does not invoke paid APIs or enable filtering policies.
"""

import argparse
import hashlib
import io
import json
import math
import os
import random
import statistics
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from switchyard.client import Client
from switchyard.contracts import SelectionRequest


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def content_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(v.get("text", "") for v in value if isinstance(v, dict))
    return ""


def read_transcripts(root):
    """Bounded prefetch hides per-file filesystem latency without changing ordering."""
    paths = sorted(root.rglob("*.jsonl"))
    with ThreadPoolExecutor(max_workers=8) as pool:
        for start in range(0, len(paths), 8):
            batch = paths[start : start + 8]
            yield from zip(batch, pool.map(Path.read_bytes, batch), strict=True)


def inventory(roots):
    counts = Counter()
    usage = {}
    cases = {}
    manifest = []
    for source, root in roots:
        for path, data in read_transcripts(root):
            counts[source + "_files"] += 1
            task = ""
            session = path.stem
            fingerprint = hashlib.sha256()
            with io.BytesIO(data) as stream:
                for raw in stream:
                    fingerprint.update(raw)
                    try:
                        record = json.loads(raw)
                    except (ValueError, UnicodeError):
                        counts["malformed_lines"] += 1
                        continue
                    payload = record.get("payload", {})
                    if record.get("type") == "session_meta":
                        session = payload.get("id", session)
                    message = record.get("message", {}) if source == "claude" else payload
                    if not isinstance(message, dict):
                        continue
                    # Deduplicate response usage, including streamed Claude message fragments.
                    u = message.get("usage") if source == "claude" else None
                    uid = message.get("id")
                    if record.get("type") == "token_usage_record":
                        u, uid = payload.get("usage"), payload.get("response_id")
                    if isinstance(u, dict) and uid:
                        key = source + ":" + uid
                        previous = usage.setdefault(key, {"source": source})
                        for field, value in u.items():
                            if isinstance(value, int) and not isinstance(value, bool):
                                previous[field] = max(previous.get(field, 0), value)
                    outputs = []
                    if source == "claude":
                        content = message.get("content", [])
                        if message.get("role") == "user":
                            text = content_text(content)
                            if text.strip():
                                task = text
                            if isinstance(content, list):
                                outputs = [
                                    content_text(v.get("content"))
                                    for v in content
                                    if isinstance(v, dict) and v.get("type") == "tool_result"
                                ]
                    elif record.get("type") == "response_item":
                        if message.get("type") == "message" and message.get("role") == "user":
                            text = content_text(message.get("content"))
                            if text.strip():
                                task = text
                        if message.get("type") in (
                            "function_call_output",
                            "custom_tool_call_output",
                        ):
                            outputs = [content_text(message.get("output"))]
                    for output in outputs:
                        counts["tool_outputs"] += 1
                        if not 1 <= len(task) <= 1500:
                            counts["excluded_task_length"] += 1
                            continue
                        if not 80 <= len(output) <= 12000:
                            counts["excluded_output_length"] += 1
                            continue
                        key = digest(task + "\0" + output)
                        if key in cases:
                            counts["duplicate_cases"] += 1
                            continue
                        cases[key] = {
                            "id": key,
                            "session": digest(source + session),
                            "source": source,
                            "task": task,
                            "text": output,
                        }
            manifest.append(fingerprint.hexdigest())
    totals = {}
    for row in usage.values():
        target = totals.setdefault(row["source"], Counter())
        target["response_records"] += 1
        for key, value in row.items():
            if key != "source":
                target[key] += value
    return list(cases.values()), {
        "counts": dict(counts),
        "usage_by_source": totals,
        "eligible_unique_cases": len(cases),
        "corpus_sha256": digest("\n".join(sorted(manifest))),
    }


def sample_cases(cases, per_source, seed):
    # Shuffle sessions first and round-robin them to prevent a long chat dominating.
    rng = random.Random(seed)
    selected = []
    for source in sorted({case["source"] for case in cases}):
        groups = {}
        for case in sorted(cases, key=lambda c: c["id"]):
            if case["source"] == source:
                groups.setdefault(case["session"], []).append(case)
        queues = list(groups.values())
        rng.shuffle(queues)
        for queue in queues:
            rng.shuffle(queue)
        chosen = []
        while queues and len(chosen) < per_source:
            remaining = []
            for queue in queues:
                if len(chosen) < per_source:
                    chosen.append(queue.pop())
                if queue:
                    remaining.append(queue)
            queues = remaining
        selected.extend(chosen)
    rng.shuffle(selected)
    return selected


def write_cases(path, cases):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as stream:
        for case in cases:
            stream.write(json.dumps(case, ensure_ascii=False) + "\n")
    path.chmod(0o600)


def load_cases(path):
    cases = []
    with path.open() as stream:
        for line in stream:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def summarize_replay(rows):
    report = {}
    for source in ("claude", "codex"):
        subset = [r for r in rows if r["source"] == source]
        if not subset:
            continue
        latency = sorted(r["elapsed_ms"] for r in subset)
        report[source] = {
            "cases": len(subset),
            "sessions": len({r["session"] for r in subset}),
            **{
                key: sum(r[key] for r in subset)
                for key in (
                    "input_chars",
                    "selected_chars",
                    "input_bytes",
                    "selected_bytes",
                    "chunks",
                    "selected_chunks",
                    "uncertain_omitted",
                    "item_errors",
                    "input_tokens",
                    "selected_tokens",
                )
            },
            "request_errors": sum(bool(r["error"]) for r in subset),
            "p50_ms": statistics.median(latency),
            "p95_ms": latency[math.ceil(len(latency) * 0.95) - 1],
        }
    return report


def replay(case, client, ratio, tokenizer=None):
    # Fixed character chunks preserve all text; report this preprocessing explicitly.
    chunks = [case["text"][i : i + 800] for i in range(0, len(case["text"]), 800)]
    items = [{"id": str(i), "text": chunk} for i, chunk in enumerate(chunks)]
    request = SelectionRequest(
        recipe="candidate-relevance@1",
        task=case["task"],
        items=items,
        budget_bytes=max(1, int(len(case["text"].encode()) * ratio)),
        timeout_ms=120000,
    )
    started = time.monotonic()
    error = None
    try:
        response = client.select_items(request)
        ids = set(response["recommended_selected_ids"])
        assessments = response["items"]
    except Exception as exc:
        ids = {item["id"] for item in items}
        assessments = []
        error = type(exc).__name__  # Exception messages may contain private text.
    selected = "".join(item["text"] for item in items if item["id"] in ids)
    return {
        "id": case["id"],
        "session": case["session"],
        "source": case["source"],
        "input_chars": len(case["text"]),
        "selected_chars": len(selected),
        "input_bytes": len(case["text"].encode()),
        "selected_bytes": len(selected.encode()),
        "chunks": len(items),
        "selected_chunks": len(ids),
        "uncertain_omitted": sum(
            item["id"] not in ids and item["assessment"] == "uncertain" for item in assessments
        ),
        "item_errors": sum("error" in item for item in assessments),
        "error": error,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
        "models": sorted({p["model"] for item in assessments for p in item["parts"]}),
        "revisions": sorted({p["revision"] for item in assessments for p in item["parts"]}),
        "relevant_retention": None,
        "input_tokens": len(tokenizer.encode(case["text"], disallowed_special=()))
        if tokenizer
        else None,
        "selected_tokens": len(tokenizer.encode(selected, disallowed_special=()))
        if tokenizer
        else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--per-source", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--ratio", type=float, default=0.5)
    parser.add_argument("--inventory-only", action="store_true")
    parser.add_argument(
        "--from-run",
        type=Path,
        help="Reuse inventory.json and cases.jsonl from a prior private run directory",
    )
    parser.add_argument(
        "--cases",
        type=Path,
        help="Load cases.jsonl directly (skips transcript scan; use with --inventory)",
    )
    parser.add_argument(
        "--inventory",
        type=Path,
        help="Load inventory.json metadata when replaying with --cases",
    )
    args = parser.parse_args()
    if args.per_source < 1 or not 0 < args.ratio <= 1:
        parser.error("positive sample size and ratio in (0, 1] required")
    if args.from_run and (args.cases or args.inventory):
        parser.error("--from-run cannot be combined with --cases/--inventory")
    if bool(args.cases) != bool(args.inventory) and not args.from_run:
        if args.cases and not args.inventory:
            parser.error("--cases requires --inventory")
        if args.inventory and not args.cases:
            parser.error("--inventory requires --cases")
    if args.output.exists():
        parser.error("output must be a new directory to preserve previous evidence")
    args.output.mkdir(parents=True, mode=0o700)
    os.umask(0o077)

    if args.from_run:
        cases = load_cases(args.from_run / "cases.jsonl")
        report = json.loads((args.from_run / "inventory.json").read_text())
    elif args.cases:
        cases = load_cases(args.cases)
        report = json.loads(args.inventory.read_text())
    else:
        roots = [
            ("claude", Path.home() / ".claude/projects"),
            ("codex", Path.home() / ".codex/sessions"),
            ("codex", Path.home() / ".codex-personal/sessions"),
        ]
        cases, report = inventory(roots)
        report.update(
            created_at=datetime.now(UTC).isoformat(),
            seed=args.seed,
            budget_ratio=args.ratio,
            quality_labels=False,
            gate_eligible=False,
            actual_provider_tokens_saved=None,
            token_measurement="characters only",
            limitations=[
                "Offline replay, no paid provider calls or billing savings.",
                "Length-filtered sample; inherited fork histories may overlap.",
                "Usage deduplicated by response ID; missing IDs excluded.",
                "No relevance labels: reduction is not evidence of safe omission.",
                "800-character chunking; no source text exported.",
            ],
        )
    report["seed"] = args.seed
    report["budget_ratio"] = args.ratio
    report["created_at"] = datetime.now(UTC).isoformat()
    (args.output / "inventory.json").write_text(json.dumps(report, indent=2))
    write_cases(args.output / "cases.jsonl", cases)
    print(json.dumps({k: report[k] for k in report if k != "runtime"}), flush=True)
    if args.inventory_only:
        return
    import tiktoken

    tokenizer = tiktoken.get_encoding("cl100k_base")
    report["token_measurement"] = (
        "tiktoken 0.12.0 cl100k_base; reference encoding, not Claude billing"
    )
    client = Client()
    capabilities = client.call("capabilities")
    if not capabilities.get("ready"):
        raise RuntimeError("Runtime must be ready before replay")
    report["runtime"] = capabilities
    selected = sample_cases(cases, args.per_source, args.seed)
    report["sample_sha256"] = digest("\n".join(case["id"] for case in selected))
    rows = []
    with (args.output / "rows.jsonl").open("w") as stream:
        for case in selected:
            row = replay(case, client, args.ratio, tokenizer)
            rows.append(row)
            stream.write(json.dumps(row) + "\n")
            stream.flush()
            if len(rows) % 10 == 0:
                print(f"Replayed {len(rows)}/{len(selected)}", flush=True)
    report["replay"] = summarize_replay(rows)
    (args.output / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report["replay"]), flush=True)


if __name__ == "__main__":
    main()
