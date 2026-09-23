# Benchmark methodology

Switchyard has two distinct evaluation tracks:

* `switchyard bench` evaluates explicitly labeled fixtures. The included four-item fixture
  is synthetic and cannot support a production quality claim.
* `scripts/benchmark_sessions.py` inventories local Claude and Codex transcripts, then replays
  a deterministic sample of historical tool outputs through the local selection service.
  These examples have no human relevance labels. Reports deliberately leave retention unknown.

## References and structure

[Laya's benchmark documentation](https://github.com/NandhaKishorM/laya/blob/main/BENCHMARKS.md)
separates application, multilingual, and latency runs and links saved results. It explicitly
identifies published Jev numbers as external comparisons rather than its own measurements.
[jev-measured](https://github.com/WallerChen/jev-measured) is a community measurement repository,
not the official Jev implementation. Its per-request evidence and reproducibility approach
inform this suite. Switchyard does not call Jev or claim a head-to-head comparison.

Here, runners live in `scripts/`, fixtures in `examples/evaluation/`, and public evidence in
`docs/evidence/`. Private transcript replay outputs stay outside the repository.

## Run against local sessions

```bash
uv sync --extra inference --group dev
uv pip install 'tiktoken==0.12.0'
.venv/bin/python scripts/benchmark_sessions.py \
  --output "$HOME/.local/share/switchyard/benchmarks/my-run" \
  --per-source 100 --seed 42 --ratio 0.5
```

The output directory must not already exist. The daemon must be ready. First-time installation
and the reference tokenizer download need network access; transcripts are never sent to a
provider. The runner reads `~/.claude/projects`, `~/.codex/sessions`, and
`~/.codex-personal/sessions`. It does not modify those files.

Each run writes `inventory.json`, a private `cases.jsonl` (task/tool text for local replay
only), individual replay rows, and a summary. Directories are private and new output files use
restrictive permissions. Rows contain hashes, counts, timing, and model identifiers, not
transcript text or paths. Reports are not automatically published.

To avoid rescanning archives after a successful inventory:

```bash
.venv/bin/python scripts/benchmark_sessions.py \
  --from-run "$HOME/.local/share/switchyard/benchmarks/my-run" \
  --output "$HOME/.local/share/switchyard/benchmarks/my-replay" \
  --per-source 100 --seed 42 --ratio 0.5
```

`--inventory-only` stops after writing inventory and cases. `--cases` plus `--inventory` loads
those files directly when you do not want `--from-run`.

## Accounting rules

Recorded usage is deduplicated by provider response ID. Repeated streamed fragments use the maximum
value for each usage counter. These are observed counters, not invoice totals. Missing response IDs
are excluded. Claude cache reads and writes are separate from uncached input. Codex cached input
is a subset of input, and reasoning output is a subset of output; do not add subsets twice.

Replay candidates pair a tool response with the preceding user message. Exact task/output pairs
are deduplicated across files. Eligible tasks have 1–1,500 characters and outputs have 80–12,000
characters. Exclusion counts are reported. Sampling shuffles sessions with a fixed seed and visits
them round-robin, separately for Claude and Codex. Forked histories may still overlap in ways that
exact content deduplication cannot identify. Files are read sequentially; active sessions can grow
while an inventory runs, so record and compare corpus and sample hashes.

Tool outputs are split into consecutive 800-character chunks without truncation. The byte budget
is a configured fraction of original UTF-8 text size. Original and selected text are counted with
`tiktoken==0.12.0`, using `cl100k_base` as an explicitly named reference encoding. These are exact
counts for that encoding, not Claude token counts or complete provider request sizes. Metadata,
system instructions, conversation history, and output tokens are not included.

The comparison baseline is the original tool output. Each case runs once against the warm daemon,
serially. Latency includes IPC and any queue delay; p50 and p95 are calculated per request.
Request failures preserve all text in replay accounting. Model-level errors and uncertain chunks
omitted by the current selector are reported separately. This suite does not activate policies.

## Interpreting results

A smaller payload is not evidence of safe selection. Report reduction alongside errors and
uncertain omissions. Actual provider savings and relevant retention remain null for unlabeled
replays. A budget alone can force large reductions, even if useful information is lost.

Before enabling filtering, independently label a held-out, session-disjoint sample and evaluate
both context retention and downstream task completion. Also measure the exact outgoing request
with the destination provider's tokenizer, compare cached and uncached costs, and account for
local inference latency. Synthetic fixtures and unlabeled transcript runs cannot replace this step.
