# Switchyard

Local typed decisions for agents and applications, powered by the open-weight Laya decision engine.

This project is a decision runtime. It does not replace a coding agent or general language model.
It does not retrieve documents, edit files, execute commands, or synchronize Git repositories.
Callers supply state and typed questions. The runtime returns `choice`, `score`, or `noul` answers
with probabilities and model metadata.

Jev is TypeSafe's hosted System One model. Laya is an independent open-weight implementation of
the same decision-oriented interface. This project runs Laya locally; it does not provide Jev,
route requests to Jev, or claim Jev-compatible accuracy.

## Install

Python 3.12 is supported on Linux and macOS.

```bash
uv sync --extra inference
uv run switchyard init
uv run switchyard models fetch
uv run switchyard serve --background
```

`models fetch` is the only command that needs network access. It downloads the pinned Laya
revision and records SHA-256 checksums. Inference uses the local files and sets the Hugging Face
and Transformers offline flags. The first English and multilingual checkpoints need about 1.5 GB
plus runtime memory. Use `SWITCHYARD_HOME` to choose the per-user data directory.

## Use it

```bash
printf '%s' '{"state":"The build fails in billing","questions":{"route":{"type":"choice","instructions":"Which team owns this?","criteria":{"engineering":"software","billing":"payments"}},"urgent":{"type":"noul","instructions":"Is this urgent?"}}}' \
  | uv run switchyard decide
```

The same contracts are available through Python and MCP. `uv run switchyard mcp` exposes only `decide`,
`evaluate_items`, `select_items`, and `capabilities`. These are read-only tools. MCP forwards to one warm local
process over a user-owned Unix socket so model weights are not loaded for every call.

## Select context

`switchyard select` evaluates caller-supplied candidates and returns an auditable budgeted plan.
Mandatory candidates are preserved, remaining candidates are ranked by relevance, and uncertain or
failed predictions remain available as fallback. Selection is advisory until a reviewed holdout
report enables a recipe policy; advisory responses include both the recommended IDs and the original
forwardable payload.

```bash
printf '%s' '{"recipe":"candidate-relevance@1","task":"billing incident","budget_bytes":12000,"items":[{"id":"note-1","text":"billing decision","citation":"vault://note-1","trust":"curated","mandatory":true}]}' \
  | uv run switchyard select
```

Retrieval systems can implement the read-only `CandidateProvider` protocol and normalize results
into the shared candidate contract. Switchyard does not retrieve, write, execute, or synchronize
external systems; integrations such as MainFrame belong outside the core runtime.

Custom synchronous or asynchronous harnesses can use the thin middleware helpers:

```python
from switchyard.middleware import select_context

plan = select_context(client, "billing incident", candidates, budget_bytes=12000)
forward = plan["selected_items"] if plan["applied"] else plan["recommended_selected_items"]
```

## Recipes and safety

`candidate-relevance@1`, `log-triage@1`, and `route-selection@1` classify caller-supplied items.
They preserve stable IDs, citations, trust labels, mandatory items, and the original text. They
start in advisory mode. Runtime failures and uncertain predictions keep the original item.

Automatic omission is disabled until a held-out report for the exact recipe, model revision, and
pipeline fingerprint meets the retention gate. The report estimates characters removed. It does
not claim provider token savings. Use `switchyard bench`, `switchyard report`, and `switchyard policy enable` only after
reviewing the labeled dataset and report.

The Claude hook is opt-in, reversible, and advisory. It only observes a caller-supplied
`decision_candidates` envelope. It does not grant permissions or replace tool output.

## Development

```bash
uv sync --group dev
uv run pytest -q
uv run ruff check src tests
```

The test suite covers contracts, token-budget rejection, queue deadlines, private sockets,
integration rollback, MCP tool boundaries, recipes, evaluation gates, and model-output validation.

## License

The runtime code is Apache-2.0. Laya is used as an external dependency and its upstream Apache-2.0
license and model revision are recorded by the installer. See `LICENSE` and `NOTICE`.
