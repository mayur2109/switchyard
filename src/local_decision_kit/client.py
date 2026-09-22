"""Client library shared by command-line and MCP entrypoints."""

import asyncio
import contextlib
import json
from pathlib import Path

from .config import load_settings
from .contracts import DecisionRequest, ItemsRequest
from .errors import DecisionError
from .runtime import MAX_FRAME


class AsyncClient:
    def __init__(self, socket: Path | None = None):
        self.socket = socket or load_settings().socket

    async def call(self, method: str, params: dict | None = None) -> dict:
        params = params or {}
        timeout = min(params.get("timeout_ms", 30000) / 1000 + 5, 125)
        writer = None
        try:
            async with asyncio.timeout(timeout):
                reader, writer = await asyncio.open_unix_connection(self.socket, limit=MAX_FRAME)
                frame = json.dumps({"method": method, "params": params}, allow_nan=False).encode()
                if len(frame) >= MAX_FRAME:
                    raise DecisionError("input_too_large", "Request exceeds transport limit")
                writer.write(frame + b"\n")
                await writer.drain()
                reply = json.loads(await reader.readline())
                if "error" in reply:
                    raise DecisionError(**reply["error"])
                return reply["result"]
        except (FileNotFoundError, ConnectionRefusedError) as error:
            raise DecisionError(
                "runtime_unavailable", "Start the runtime with ldk serve"
            ) from error
        except TimeoutError as error:
            raise DecisionError(
                "deadline_exceeded", "Runtime response deadline exceeded"
            ) from error
        except (ConnectionError, ValueError, KeyError) as error:
            raise DecisionError("transport_error", "Runtime connection failed") from error
        finally:
            if writer:
                writer.close()
                with contextlib.suppress(ConnectionError):
                    await writer.wait_closed()

    async def decide(self, request: DecisionRequest) -> dict:
        return await self.call("decide", request.model_dump(mode="json"))

    async def evaluate_items(self, request: ItemsRequest) -> dict:
        return await self.call("evaluate_items", request.model_dump(mode="json"))


class Client:
    """Synchronous API. Use AsyncClient inside an existing asyncio event loop."""

    def __init__(self, socket: Path | None = None):
        self.async_client = AsyncClient(socket)

    def call(self, method: str, params: dict | None = None) -> dict:
        return asyncio.run(self.async_client.call(method, params))

    def decide(self, request: DecisionRequest) -> dict:
        return self.call("decide", request.model_dump(mode="json"))

    def evaluate_items(self, request: ItemsRequest) -> dict:
        return self.call("evaluate_items", request.model_dump(mode="json"))
