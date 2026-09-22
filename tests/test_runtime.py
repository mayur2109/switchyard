import asyncio
import json
import threading

import pytest


class Engine:
    def decide(self, request):
        return {"answers": {"q": {"type": "noul", "noul": 0.8, "confidence": 0.8}}}

    def capabilities(self):
        return {"ready": True, "models": ["test"]}


def payload(timeout=1000):
    return {
        "state": "x",
        "questions": {"q": {"type": "noul", "instructions": "Check"}},
        "timeout_ms": timeout,
    }


async def test_service_dispatch_and_validation():
    from switchyard.runtime import DecisionService

    async with DecisionService(Engine(), queue_size=2) as service:
        result = await service.dispatch({"method": "decide", "params": payload()})
        assert result["answers"]["q"]["noul"] == 0.8
        with pytest.raises(ValueError):
            await service.dispatch({"method": "decide", "params": {"state": "bad"}})


async def test_service_dispatches_budgeted_selection():
    from switchyard.runtime import DecisionService

    class SelectionEngine(Engine):
        def decide(self, request):
            return {
                "answers": {
                    "relevant": {"type": "noul", "noul": 0.9, "confidence": 0.9}
                },
                "model": "english",
                "revision": "test",
            }

    params = {
        "recipe": "candidate-relevance@1",
        "task": "keep useful context",
        "items": [{"id": "a", "text": "useful"}],
        "budget_bytes": 1000,
    }
    async with DecisionService(SelectionEngine()) as service:
        result = await service.dispatch({"method": "select_items", "params": params})
    assert result["selected_ids"] == ["a"]
    assert result["recommended_selected_ids"] == ["a"]


async def test_deadline_does_not_release_worker_while_inference_is_running():
    from switchyard.errors import DecisionError
    from switchyard.runtime import DecisionService

    release = threading.Event()
    started = threading.Event()
    calls = []

    class SlowEngine(Engine):
        def decide(self, request):
            calls.append(request.state)
            started.set()
            release.wait(2)
            return super().decide(request)

    async with DecisionService(SlowEngine(), queue_size=1) as service:
        first = asyncio.create_task(service.dispatch({"method": "decide", "params": payload(100)}))
        await asyncio.to_thread(started.wait, 1)
        second = asyncio.create_task(service.dispatch({"method": "decide", "params": payload(100)}))
        await asyncio.sleep(0.01)
        with pytest.raises(DecisionError, match="queue_full"):
            await service.dispatch({"method": "decide", "params": payload()})
        for task in (first, second):
            with pytest.raises(DecisionError, match="deadline_exceeded"):
                await task
        release.set()
        await asyncio.sleep(0.05)
        assert len(calls) == 1


async def test_socket_permissions_client_and_shutdown(tmp_path):
    from switchyard.client import AsyncClient
    from switchyard.runtime import DecisionService, SocketServer

    socket = tmp_path / "runtime" / "kit.sock"
    async with DecisionService(Engine()) as service:
        server = SocketServer(service, socket)
        await server.start()
        try:
            assert socket.stat().st_mode & 0o777 == 0o600
            assert socket.parent.stat().st_mode & 0o777 == 0o700
            client = AsyncClient(socket)
            assert (await client.call("capabilities"))["ready"]
            result = await client.call("decide", payload())
            assert result["answers"]["q"]["noul"] == 0.8
            reader, writer = await asyncio.open_unix_connection(socket)
            writer.write(b'{"method":"execute","params":{"command":"echo BAD"}}\n')
            await writer.drain()
            response = json.loads(await reader.readline())
            assert response["error"]["code"] == "unknown_method"
            writer.close()
            await writer.wait_closed()
        finally:
            await server.close()
        assert not socket.exists()


def test_private_config_and_unchanged_existing_file(tmp_path):
    from switchyard.config import Settings, initialize, load_settings

    settings = initialize(tmp_path / "switchyard")
    assert settings.models == ["english", "multilingual"]
    config = settings.home / "config.json"
    assert config.stat().st_mode & 0o777 == 0o600
    original = config.read_bytes()
    initialize(settings.home)
    assert config.read_bytes() == original
    assert load_settings(settings.home) == settings
    with pytest.raises(ValueError):
        Settings(home=settings.home, queue_size=0)


def test_missing_models_never_trigger_network(tmp_path):
    from switchyard.config import Settings
    from switchyard.engine import LayaEngine
    from switchyard.errors import DecisionError

    engine = LayaEngine(Settings(home=tmp_path))
    with pytest.raises(DecisionError, match="model_unavailable"):
        engine.load()
    assert engine.capabilities()["state"] == "degraded"
    assert engine.capabilities()["ready"] is False
