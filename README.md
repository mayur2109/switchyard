# Switchyard

Switchyard is a local decision service for AI agents and automation systems.

It uses the open-weight [Laya](https://github.com/convaiinnovations/laya) engine to answer small,
typed questions on your machine. That lets a larger model spend its context and reasoning budget on
the work that needs it.

For example, an agent can ask Switchyard:

- Which team owns this incident?
- Is this log event relevant to the task?
- Which retrieved notes fit inside a 12,000-byte context budget?
- Is this input safe to pass to the next stage?

Switchyard returns a structured answer with probabilities, model metadata, and a clear fallback
path. It does not generate prose, edit files, run commands, retrieve documents on its own, or
replace a coding agent.

## Why use it?

Most agent workflows contain many decisions that do not need a large general-purpose model. A tool
may return 100 log lines when 10 matter. A retrieval system may return notes from several unrelated
projects. A routing step may need a yes/no answer before an expensive model call starts.

Sending all of that material to a hosted model costs tokens, adds latency, and makes it harder to
see why a piece of context was kept or dropped.

Switchyard handles the narrow decision locally:

```text
retrieval or tool output
        |
        v
Switchyard: classify, rank, and apply a budget
        |
        v
smaller cited payload for the main agent
```

The local process keeps model weights warm, accepts typed requests, and communicates over a
user-owned Unix socket. The first model download needs network access. Inference does not.

## What Switchyard is and is not

Switchyard is:

- a local CPU decision runtime;
- a typed API for `choice`, `score`, and `noul` questions;
- a budgeted context-selection service;
- an MCP server for Claude Code and other MCP clients;
- a Python client and middleware layer for custom agent loops;
- an optional read-only provider for curated MainFrame Vault content.

Switchyard is not:

- a coding agent or general language model;
- a document store, search engine, or vector database;
- an authorization system;
- a command executor or file editor;
- a replacement for MainFrame;
- a hosted Jev service or a claim of Jev-compatible accuracy.

Jev is TypeSafe's hosted System One model. Laya is an independent open-weight decision engine.
Switchyard runs Laya locally and keeps those products separate.

## Install from this repository

Switchyard currently installs from source. Linux and Apple Silicon macOS are the supported targets.
Python 3.12 is the tested version.

Install the runtime and local inference dependencies with [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/mayur2109/switchyard.git
cd switchyard
uv sync --extra inference
uv run switchyard init
uv run switchyard models fetch
uv run switchyard serve --background
```

`models fetch` downloads a pinned Laya revision and records SHA-256 checksums. The English and
multilingual checkpoints need roughly 1.5 GB of disk space plus runtime memory. Choose a different
private data directory with `SWITCHYARD_HOME`:

```bash
SWITCHYARD_HOME=/data/switchyard uv run switchyard init
```

Check the installation:

```bash
uv run switchyard doctor
uv run switchyard status
```

The runtime reports `starting`, `loading`, `ready`, or `degraded`. A socket existing does not mean
that model loading has finished.

## Make a typed decision

The CLI reads one JSON request from standard input and writes one JSON response to standard output.

### Route a task

```bash
printf '%s\n' '{
  "state": "The payment reconciliation job is failing",
  "questions": {
    "owner": {
      "type": "choice",
      "instructions": "Which team owns this issue?",
      "criteria": {
        "engineering": "software and infrastructure",
        "billing": "payments and reconciliation"
      }
    }
  }
}' | uv run switchyard decide
```

The response contains the selected choice, probabilities, confidence, selected model, revision,
routing explanation, and timing. The answer is a prediction, not permission or verified fact.

### Ask a binary question

```bash
printf '%s\n' '{
  "state": "The log contains a database timeout",
  "questions": {
    "relevant": {
      "type": "noul",
      "instructions": "Is this relevant to diagnosing the billing outage?"
    }
  }
}' | uv run switchyard decide
```

`noul` is Laya's binary decision type. `choice` selects among named options. `score` selects an
ordered score description.

## Select context before an expensive model call

`switchyard select` evaluates caller-supplied candidates and builds an auditable budgeted plan.

The selector:

- keeps mandatory candidates;
- ranks other candidates by relevance;
- preserves caller order for selected output;
- keeps uncertain and failed predictions as fallback;
- records IDs, citations, trust labels, scores, and reasons;
- returns estimated bytes and tokens;
- never enables omission automatically.

```bash
printf '%s\n' '{
  "recipe": "candidate-relevance@1",
  "task": "investigate a billing incident",
  "budget_bytes": 12000,
  "items": [
    {
      "id": "billing-policy",
      "text": "Payment failures follow the reconciliation workflow.",
      "citation": "vault://billing-policy",
      "trust": "curated",
      "mandatory": true
    },
    {
      "id": "unrelated-note",
      "text": "The office garden checklist was updated.",
      "citation": "vault://unrelated-note",
      "trust": "unverified"
    }
  ]
}' | uv run switchyard select
```

The response includes two payloads:

- `recommended_selected_items` is the budgeted recommendation;
- `selected_items` is the payload actually applied by policy.

Before a reviewed policy is enabled, `selected_items` remains the original input. This makes the
default behavior advisory and reversible.

## Use it from Python

The client works in synchronous and asynchronous applications:

```python
from switchyard.client import Client
from switchyard.contracts import DecisionRequest

client = Client()
result = client.decide(
    DecisionRequest(
        state="The payment job is failing",
        questions={
            "owner": {
                "type": "choice",
                "instructions": "Which team owns this?",
                "criteria": {
                    "engineering": "software",
                    "billing": "payments",
                },
            }
        },
    )
)
print(result["answers"])
```

For a custom agent loop, use the middleware helpers:

```python
from switchyard.middleware import forward_candidates, select_context

plan = select_context(
    client,
    "investigate a billing incident",
    candidates,
    budget_bytes=12000,
)

# Copies the envelope and replaces only decision_candidates.items.
# If Switchyard is unavailable, the helper returns the original candidates.
forwarded_tool_result = forward_candidates(tool_result, plan)
```

The asynchronous versions are `aselect_context` and the async client methods. The middleware fails
open on runtime errors. An unavailable local decision service must not block the calling agent.

## Use it through MCP

Switchyard exposes four read-only MCP tools:

- `decide`
- `evaluate_items`
- `select_items`
- `capabilities`

Register the local server with Claude Code:

```bash
claude mcp add --scope user switchyard -- \
  "$HOME/.local/bin/switchyard" mcp
```

The MCP server forwards requests to the warm local runtime. It does not retrieve external context,
edit files, run commands, or grant permissions.

## MainFrame integration

MainFrame remains a local-first knowledge store. Switchyard does not own it.

The optional `MainFrameProvider` searches only these curated tiers:

```text
Projects/  Stacks/  Patterns/  Guidelines/  Notes/
```

It uses argument-safe `ripgrep`, returns stable candidate IDs and Vault citations, and does not
write retrieval logs or modify the Vault:

```python
from pathlib import Path
from switchyard.mainframe import MainFrameProvider
from switchyard.middleware import select_context

provider = MainFrameProvider(Path.home() / "MainFrame")
candidates = provider.search("billing workflow")
plan = select_context(client, "billing workflow", candidates, budget_bytes=12000)
```

The core package defines a generic read-only `CandidateProvider` protocol, so other users can plug
in a filesystem index, database, search service, or enterprise retrieval system without adding that
system to Switchyard's core dependencies.

Switchyard never writes MainFrame notes, syncs remotes, or manages Vault credentials. Remote backup
and collaboration remain explicit MainFrame operations.

## Recipes and safety

The built-in recipes are:

- `candidate-relevance@1`, which asks whether supplied passages matter to a task;
- `log-triage@1`, which asks whether supplied log events help investigate a task;
- `route-selection@1`, which chooses among caller-defined routes without executing one.

All recipes preserve stable IDs, citations, trust labels, mandatory flags, and original text.
Runtime failures and uncertain predictions keep the original item.

Automatic omission is disabled until a held-out report for the exact recipe, model revision, and
pipeline fingerprint passes the retention gate. The gate requires, among other checks, 99% relevant
retention, complete mandatory preservation, enough labeled cases, zero errors, and measurable
content reduction.

The report estimates content reduction. It does not claim provider billing savings unless the
calling harness supplies before/after provider token telemetry.

Run the included smoke corpus:

```bash
uv run switchyard bench examples/evaluation/smoke.json \
  --report /tmp/switchyard-smoke.json
uv run switchyard report /tmp/switchyard-smoke.json
```

The smoke corpus demonstrates the workflow but cannot enable omission. Use a reviewed anonymized
holdout corpus for that decision.

## Operational model

Switchyard runs one warm local process per user. The process:

1. verifies pinned model files;
2. loads the requested English and multilingual checkpoints;
3. listens on a private Unix socket;
4. serializes native inference through a bounded queue;
5. returns structured JSON responses;
6. removes the socket during clean shutdown.

The default runtime is CPU-only. Model loading can take time on the first start. Requests have
bounded input size, queue capacity, and deadlines. The current design favors predictable behavior
and safe fallback over maximum throughput.

## Development

```bash
uv sync --group dev
uv run ruff check src tests
uv run pytest -q
uv build
```

Every push and pull request runs linting, tests, and package builds on Linux and Apple Silicon
macOS.

## Current limits

Switchyard is useful today as a local decision and selection service. The following work is still
deliberately separate:

- automatic integration into a specific agent's next model call;
- a production-sized labeled holdout corpus;
- provider billing measurements;
- hosted multi-user deployment;
- Windows transport and service support;
- generated context summaries;
- MainFrame write access.

## License

Switchyard runtime code is Apache-2.0. Laya is an external dependency. The installer records the
upstream model revision and checksums. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
