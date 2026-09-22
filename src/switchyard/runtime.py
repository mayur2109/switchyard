"""Bounded serial inference and a private JSON-lines Unix socket protocol."""

import asyncio
import contextlib
import fcntl
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import ValidationError

from .config import private_directory
from .contracts import DecisionRequest, ItemsRequest, SelectionRequest
from .errors import DecisionError
from .recipes import evaluate_items

MAX_FRAME = 2_000_000


class DecisionService:
    def __init__(self, engine, queue_size=16):
        self.engine = engine
        self.queue = asyncio.Queue(maxsize=queue_size)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="switchyard-inference")
        self.worker = None
        self.count = 0
        self.errors = 0

    async def __aenter__(self):
        self.worker = asyncio.create_task(self._work())
        return self

    async def __aexit__(self, *args):
        await self.queue.put(None)
        await self.worker
        self.executor.shutdown(wait=True, cancel_futures=True)

    async def _work(self):
        loop = asyncio.get_running_loop()
        while (job := await self.queue.get()) is not None:
            function, request, future, deadline = job
            if future.cancelled():
                continue
            if time.monotonic() >= deadline:
                future.set_exception(DecisionError("deadline_exceeded", "Queue deadline exceeded"))
                continue
            try:
                # Never cancel this await on client timeout: a native kernel is still running.
                result = await loop.run_in_executor(self.executor, function, request)
                self.count += 1
                if not future.done():
                    future.set_result(result)
            except Exception as error:
                self.errors += 1
                if not future.done():
                    future.set_exception(error)

    async def dispatch(self, message):
        if not isinstance(message, dict) or set(message) - {"method", "params"}:
            raise DecisionError("invalid_request", "Expected method and params")
        method, params = message.get("method"), message.get("params", {})
        if method == "capabilities":
            return {
                **self.engine.capabilities(),
                "queued": self.queue.qsize(),
                "completed_requests": self.count,
                "failed_requests": self.errors,
            }
        if method == "decide":
            request = DecisionRequest.model_validate(params)
            function = self.engine.decide
        elif method == "evaluate_items":
            request = ItemsRequest.model_validate(params)

            def function(value):
                return evaluate_items(self.engine, value)
        elif method == "select_items":
            request = SelectionRequest.model_validate(params)

            def function(value):
                from .recipes import select_items

                return select_items(self.engine, value)
        else:
            raise DecisionError("unknown_method", "Unknown decision method")
        future = asyncio.get_running_loop().create_future()
        deadline = time.monotonic() + request.timeout_ms / 1000
        try:
            self.queue.put_nowait((function, request, future, deadline))
        except asyncio.QueueFull as error:
            raise DecisionError("queue_full", "Inference queue is full; retry later") from error
        try:
            return await asyncio.wait_for(future, request.timeout_ms / 1000)
        except TimeoutError as error:
            raise DecisionError("deadline_exceeded", "Request deadline exceeded") from error


class SocketServer:
    def __init__(self, service: DecisionService, path: Path):
        self.service, self.path = service, path
        self.server = None
        self.lock_fd = None
        self.stopped = asyncio.Event()
        self.connections = set()

    async def start(self):
        private_directory(self.path.parent)
        if len(os.fsencode(self.path)) > 100:
            raise DecisionError(
                "unsafe_path", "Socket path is too long; use a shorter SWITCHYARD_HOME"
            )
        lock_path = self.path.with_suffix(".lock")
        self.lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(self.lock_fd)
            self.lock_fd = None
            raise DecisionError("already_running", "A runtime already owns this socket") from error
        if self.path.exists() or self.path.is_symlink():
            if self.path.is_symlink() or not self.path.is_socket():
                raise DecisionError("unsafe_path", "Refusing to replace a non-socket path")
            self.path.unlink()
        self.server = await asyncio.start_unix_server(self._accept, path=self.path, limit=MAX_FRAME)
        self.path.chmod(0o600)

    async def _accept(self, reader, writer):
        task = asyncio.current_task()
        self.connections.add(task)

        def forget(done):
            self.connections.discard(done)

        task.add_done_callback(forget)
        dispatch = disconnect = None
        try:
            async with asyncio.timeout(125):
                line = await reader.readline()
                message = json.loads(line)
                if isinstance(message, dict) and message.get("method") == "shutdown":
                    result = {"stopping": True}
                    self.stopped.set()
                else:
                    dispatch = asyncio.create_task(self.service.dispatch(message))
                    disconnect = asyncio.create_task(reader.read(1))
                    done, _ = await asyncio.wait(
                        [dispatch, disconnect], return_when=asyncio.FIRST_COMPLETED
                    )
                    if dispatch not in done:
                        dispatch.cancel()
                        return
                    result = await dispatch
                response = {"result": result}
        except DecisionError as error:
            response = {"error": error.as_dict()}
        except (ValidationError, ValueError, TypeError, RecursionError):
            response = {
                "error": {"code": "invalid_request", "message": "Request validation failed"}
            }
        except TimeoutError:
            response = {
                "error": {"code": "deadline_exceeded", "message": "Socket deadline exceeded"}
            }
        except Exception:
            response = {"error": {"code": "internal_error", "message": "Runtime request failed"}}
        finally:
            for pending in (dispatch, disconnect):
                if pending and not pending.done():
                    pending.cancel()
        try:
            if "response" in locals():
                writer.write(
                    json.dumps(response, ensure_ascii=False, allow_nan=False).encode() + b"\n"
                )
                await writer.drain()
        except (ConnectionError, BrokenPipeError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()

    async def close(self):
        if self.server:
            self.server.close()
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(2):
                    await self.server.wait_closed()
            # A disconnected client must not hold shutdown. Native inference continues in the
            # service worker and the queued future is resolved or expired independently.
            for connection in list(self.connections):
                connection.cancel()
            if self.connections:
                await asyncio.gather(*self.connections, return_exceptions=True)
            self.connections.clear()
            self.path.unlink(missing_ok=True)
        if self.lock_fd is not None:
            os.close(self.lock_fd)
            self.lock_fd = None
