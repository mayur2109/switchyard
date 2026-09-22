"""CLI commands. JSON on stdout; operational diagnostics on stderr."""

import asyncio
import importlib.metadata
import json
import os
import platform
import signal
import subprocess
import sys
import time
from pathlib import Path

import typer
from pydantic import ValidationError

from .client import Client
from .config import initialize, load_settings
from .contracts import DecisionRequest, ItemsRequest
from .errors import DecisionError

app = typer.Typer(no_args_is_help=True, help="Local typed decisions for agents and applications.")
models_app = typer.Typer(no_args_is_help=True)
integrations_app = typer.Typer(no_args_is_help=True)
policy_app = typer.Typer(no_args_is_help=True)
app.add_typer(models_app, name="models")
app.add_typer(integrations_app, name="integrations")
app.add_typer(policy_app, name="policy")


def output(value):
    typer.echo(json.dumps(value, ensure_ascii=False, allow_nan=False))


def fail(error, code=1):
    if isinstance(error, DecisionError):
        output({"error": error.as_dict()})
    else:
        output({"error": {"code": "invalid_request", "message": "Request validation failed"}})
    raise typer.Exit(code)


def read_input():
    raw = sys.stdin.read(2_000_001)
    if len(raw.encode()) > 2_000_000:
        raise ValueError("Input exceeds transport limit")
    return json.loads(raw)


@app.command()
def init():
    """Create private per-user configuration without changing agent settings."""
    try:
        output(initialize().model_dump(mode="json"))
    except (DecisionError, ValueError) as error:
        fail(error)


@models_app.command("fetch")
def models_fetch(name: str | None = typer.Argument(None)):
    """Download and verify pinned checkpoints. Requires network access."""
    from .models import fetch_model

    try:
        settings = initialize()
        results = [fetch_model(settings, model) for model in ([name] if name else settings.models)]
        output({"models": results})
    except DecisionError as error:
        fail(error)
    except Exception:
        fail(DecisionError("download_failed", "Download failed; retry ldk models fetch"))


@models_app.command("verify")
def models_verify():
    """Check pinned model files against their installation manifest."""
    from .models import verify_model

    try:
        settings = load_settings()
        output({"models": [verify_model(settings, model) for model in settings.models]})
    except DecisionError as error:
        fail(error)


async def _serve(settings):
    from .engine import LayaEngine
    from .runtime import DecisionService, SocketServer

    engine = LayaEngine(settings)
    async with DecisionService(engine, settings.queue_size) as service:
        server = SocketServer(service, settings.socket)
        try:
            await server.start()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, server.stopped.set)
            await asyncio.to_thread(engine.load)
            typer.echo("Local Decision Kit is ready", err=True)
            await server.stopped.wait()
        finally:
            await server.close()


@app.command()
def serve(background: bool = typer.Option(False, help="Start a detached per-user runtime")):
    """Run one warm CPU runtime. Use ldk stop for a clean shutdown."""
    try:
        settings = initialize()
        if background:
            try:
                existing = Client().call("capabilities")
                output({"already_running": True, **existing})
                return
            except DecisionError as error:
                if error.code != "runtime_unavailable":
                    raise
            log = settings.home / "runtime" / "service.log"
            fd = os.open(log, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w") as stream:
                process = subprocess.Popen(
                    [sys.executable, "-m", "local_decision_kit", "serve"],
                    stdin=subprocess.DEVNULL,
                    stdout=stream,
                    stderr=stream,
                    start_new_session=True,
                )
            for _ in range(240):
                if process.poll() is not None:
                    raise DecisionError("startup_failed", f"See {log} for startup diagnostics")
                try:
                    result = Client().call("capabilities")
                    if result["ready"]:
                        output({"pid": process.pid, "socket": str(settings.socket), **result})
                        return
                except DecisionError:
                    pass
                time.sleep(0.5)
            raise DecisionError("startup_pending", "Runtime is still loading; inspect ldk status")
        asyncio.run(_serve(settings))
    except (DecisionError, ValueError) as error:
        fail(error)


@app.command()
def status():
    """Inspect the live runtime without loading models in this client."""
    try:
        output(Client().call("capabilities"))
    except DecisionError as error:
        fail(error)


@app.command()
def stop():
    """Request shutdown; an in-flight native inference finishes before exit."""
    try:
        output(Client().call("shutdown"))
    except DecisionError as error:
        fail(error)


@app.command()
def decide():
    """Read a DecisionRequest from stdin and return local typed answers."""
    try:
        request = DecisionRequest.model_validate(read_input())
        output(Client().decide(request))
    except (ValidationError, ValueError) as error:
        fail(error, 2)
    except DecisionError as error:
        fail(error)


@app.command()
def evaluate():
    """Read an ItemsRequest from stdin and execute a versioned recipe."""
    try:
        request = ItemsRequest.model_validate(read_input())
        output(Client().evaluate_items(request))
    except (ValidationError, ValueError) as error:
        fail(error, 2)
    except DecisionError as error:
        fail(error)


@app.command()
def schemas():
    """Print authoritative JSON schemas for the public request types."""
    output(
        {
            "DecisionRequest": DecisionRequest.model_json_schema(),
            "ItemsRequest": ItemsRequest.model_json_schema(),
        }
    )


@app.command()
def doctor():
    """Check configuration, dependencies, model integrity, and runtime health."""
    from .models import verify_model

    settings = load_settings()
    checks = {
        "platform": platform.system(),
        "python": platform.python_version(),
        "home": str(settings.home),
        "dependencies": {},
        "models": {},
    }
    ready = True
    for name in ("laya", "torch", "transformers", "mcp"):
        try:
            checks["dependencies"][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            checks["dependencies"][name] = "missing"
            ready = False
    for name in settings.models:
        try:
            checks["models"][name] = verify_model(settings, name)["revision"]
        except DecisionError as error:
            checks["models"][name] = error.as_dict()
            ready = False
    try:
        checks["runtime"] = Client().call("capabilities")
    except DecisionError as error:
        checks["runtime"] = error.as_dict()
    checks["ready"] = ready
    output(checks)
    if not ready:
        raise typer.Exit(1)


@app.command("mcp")
def mcp_command():
    """Serve MCP over stdio, forwarding to the shared runtime."""
    from .mcp_server import create_server

    create_server().run(transport="stdio")


@app.command(hidden=True)
def hook():
    from .hooks import process_event

    try:
        output(process_event(read_input()))
    except Exception:
        output({})


def _executable() -> str:
    return str(Path(sys.executable).parent / "ldk")


@integrations_app.command("inspect")
def integrations_inspect():
    """Print MCP configuration and hook behavior without changing anything."""
    output(
        {
            "mcpServers": {
                "local-decision-kit": {
                    "command": _executable(),
                    "args": ["mcp"],
                    "env": {"LDK_HOME": str(load_settings().home)},
                }
            },
            "hook_mode": "advisory",
            "hook_input": "decision_candidates envelope",
            "notice": "Explicit MCP tools work without hooks; hooks require matching tool output",
        }
    )


@integrations_app.command("install")
def integrations_install(
    settings: Path = typer.Option(..., help="Claude settings.json path"),  # noqa: B008
    tool: str = typer.Option(..., help="One exact tool name; no wildcards"),  # noqa: B008
    apply: bool = typer.Option(False, "--apply", help="Apply the previewed settings change"),  # noqa: B008
):
    """Preview an opt-in hook integration; pass --apply to write it."""
    from .integrations import install, preview

    try:
        output((install if apply else preview)(settings, _executable(), tool))
    except (DecisionError, ValueError) as error:
        fail(error, 2)


@integrations_app.command("remove")
def integrations_remove(settings: Path = typer.Option(...)):  # noqa: B008
    """Remove our hook, preserving unrelated settings and later edits."""
    from .integrations import remove

    try:
        output(remove(settings))
    except (DecisionError, ValueError) as error:
        fail(error)


@app.command()
def bench(dataset: Path, report: Path | None = typer.Option(None)):  # noqa: B008
    """Evaluate a labeled local dataset. Report stores IDs and metrics, not input text."""
    from .config import atomic_json
    from .evaluation import Dataset, benchmark

    try:
        result = benchmark(Client(), Dataset.model_validate_json(dataset.read_text()))
        if report:
            report.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(report, result)
        output(result)
    except (DecisionError, ValueError) as error:
        fail(error)


@app.command("report")
def show_report(path: Path):
    """Print an evaluation report's measurements and limitations."""
    try:
        report = json.loads(path.read_text())
        output(
            {
                "metrics": report["metrics"],
                "baseline": report["baseline"],
                "limitations": report["limitations"],
            }
        )
    except (ValueError, KeyError) as error:
        fail(error, 2)


@policy_app.command("enable")
def policy_enable(report: Path = typer.Option(...)):  # noqa: B008
    """Explicitly enable omission after a passing held-out evaluation."""
    from .policy import enable_policy

    try:
        output(enable_policy(initialize().home, json.loads(report.read_text())))
    except (DecisionError, ValueError) as error:
        fail(error)


@policy_app.command("disable")
def policy_disable(recipe: str):
    """Disable a recipe's omission policy, returning to advisory behavior."""
    from .config import atomic_json
    from .recipes import RECIPE_DESCRIPTIONS

    if recipe not in RECIPE_DESCRIPTIONS:
        fail(ValueError("Unknown recipe"), 2)
    path = load_settings().home / "policies" / (recipe + ".json")
    if path.exists():
        policy = json.loads(path.read_text())
        policy["enabled"] = False
        atomic_json(path, policy)
    output({"recipe": recipe, "enabled": False})
