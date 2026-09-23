import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "session_benchmark", Path(__file__).parents[1] / "scripts" / "benchmark_sessions.py"
)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def test_inventory_deduplicates_stream_usage_and_private_output(tmp_path):
    records = [
        {"message": {"role": "user", "content": "Fix billing"}},
        {"message": {"id": "response-1", "usage": {"input_tokens": 10, "output_tokens": 2}}},
        {"message": {"id": "response-1", "usage": {"input_tokens": 10, "output_tokens": 8}}},
        {
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "content": "private evidence " * 10}],
            }
        },
    ]
    text = "\n".join(json.dumps(r) for r in records)
    (tmp_path / "one.jsonl").write_text(text)
    (tmp_path / "duplicate.jsonl").write_text(text)
    cases, report = benchmark.inventory([("claude", tmp_path)])
    assert len(cases) == 1
    assert report["usage_by_source"]["claude"]["input_tokens"] == 10
    assert report["usage_by_source"]["claude"]["output_tokens"] == 8
    assert "private evidence" not in json.dumps(report)


def test_sample_is_deterministic_and_balances_sessions():
    cases = [{"id": str(i), "session": str(i % 5), "source": "codex"} for i in range(30)]
    first = benchmark.sample_cases(cases, 5, 42)
    assert first == benchmark.sample_cases(list(reversed(cases)), 5, 42)
    assert len({c["session"] for c in first}) == 5


def test_codex_usage_ignores_cumulative_totals_and_rejects_oversized_cases(tmp_path):
    records = [
        {"type": "session_meta", "payload": {"id": "session"}},
        {
            "type": "token_usage_record",
            "payload": {
                "response_id": "one",
                "usage": {"input_tokens": 20, "cached_input_tokens": 10},
                "thread_token_usage": {"input_tokens": 1000000},
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Investigate failure"}],
            },
        },
        {
            "type": "response_item",
            "payload": {"type": "function_call_output", "output": "result " * 30},
        },
        {
            "type": "response_item",
            "payload": {"type": "function_call_output", "output": "x" * 12001},
        },
    ]
    (tmp_path / "session.jsonl").write_text("\n".join(json.dumps(r) for r in records))
    cases, report = benchmark.inventory([("codex", tmp_path)])
    assert len(cases) == 1
    assert cases[0]["task"] == "Investigate failure"
    assert report["usage_by_source"]["codex"]["input_tokens"] == 20
    assert report["counts"]["excluded_output_length"] == 1


def test_replay_failure_preserves_every_character():
    class Unavailable:
        def select_items(self, request):
            raise RuntimeError("sensitive detail")

    row = benchmark.replay(
        {
            "id": "hash",
            "session": "session",
            "source": "claude",
            "task": "task",
            "text": "λ" * 1200,
        },
        Unavailable(),
        0.5,
    )
    assert row["input_chars"] == row["selected_chars"] == 1200
    assert row["input_bytes"] == row["selected_bytes"] == 2400
    assert row["error"] == "RuntimeError"
    assert "sensitive" not in json.dumps(row)


def test_replay_records_uncertain_omissions_without_exporting_text():
    class Client:
        def select_items(self, request):
            return {
                "recommended_selected_ids": [],
                "items": [
                    {"id": item.id, "assessment": "uncertain", "parts": []}
                    for item in request.items
                ],
            }

    row = benchmark.replay(
        {
            "id": "opaque",
            "session": "session",
            "source": "codex",
            "task": "private task",
            "text": "secret output" * 20,
        },
        Client(),
        0.5,
    )
    assert row["uncertain_omitted"] == 1
    assert row["relevant_retention"] is None
    assert "secret" not in json.dumps(row)


def test_write_and_load_cases_round_trip(tmp_path):
    cases = [
        {
            "id": "abc",
            "session": "sess",
            "source": "claude",
            "task": "private task",
            "text": "secret tool output " * 5,
        }
    ]
    path = tmp_path / "cases.jsonl"
    benchmark.write_cases(path, cases)
    loaded = benchmark.load_cases(path)
    assert loaded == cases
    assert path.stat().st_mode & 0o777 == 0o600


def test_summarize_replay_aggregates_per_source():
    rows = [
        {
            "source": "claude",
            "session": "a",
            "input_chars": 10,
            "selected_chars": 4,
            "input_bytes": 10,
            "selected_bytes": 4,
            "chunks": 2,
            "selected_chunks": 1,
            "uncertain_omitted": 0,
            "item_errors": 0,
            "input_tokens": 3,
            "selected_tokens": 1,
            "error": None,
            "elapsed_ms": 10.0,
        },
        {
            "source": "claude",
            "session": "b",
            "input_chars": 20,
            "selected_chars": 8,
            "input_bytes": 20,
            "selected_bytes": 8,
            "chunks": 3,
            "selected_chunks": 1,
            "uncertain_omitted": 1,
            "item_errors": 0,
            "input_tokens": 6,
            "selected_tokens": 2,
            "error": "DecisionError",
            "elapsed_ms": 30.0,
        },
    ]
    summary = benchmark.summarize_replay(rows)
    assert summary["claude"]["cases"] == 2
    assert summary["claude"]["input_tokens"] == 9
    assert summary["claude"]["request_errors"] == 1
    assert summary["claude"]["p50_ms"] == 20.0
