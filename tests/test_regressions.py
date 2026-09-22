import asyncio
import subprocess
import sys

import pytest


def test_model_output_validation_cannot_be_disabled_by_python_optimization():
    result = subprocess.run(
        [
            sys.executable,
            "-O",
            "-c",
            """
from local_decision_kit.contracts import Question, validate_answers
from local_decision_kit.errors import DecisionError
try:
    validate_answers({'q': Question(type='noul', instructions='Check')},
                     {'q': {'type': 'noul', 'noul': float('nan'), 'confidence': 0.9}})
except DecisionError:
    raise SystemExit(0)
raise SystemExit(1)
""",
        ],
        capture_output=True,
    )
    assert result.returncode == 0


async def test_disconnected_client_is_cleaned_up(tmp_path):
    import time

    from local_decision_kit.runtime import DecisionService, SocketServer

    class Engine:
        def capabilities(self):
            return {"ready": True}

        def decide(self, request):
            time.sleep(0.2)
            return {}

    async with DecisionService(Engine()) as service:
        server = SocketServer(service, tmp_path / "s")
        await server.start()
        try:
            reader, writer = await asyncio.open_unix_connection(server.path)
            writer.write(
                b'{"method":"decide","params":{"state":"x","questions":'
                b'{"q":{"type":"noul","instructions":"Check"}}}}\n'
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(0.5)
            assert not server.connections
        finally:
            for task in list(server.connections):
                task.cancel()
            await server.close()


def test_choice_winner_must_agree_with_probabilities():
    from local_decision_kit.contracts import Question, validate_answers
    from local_decision_kit.errors import DecisionError

    with pytest.raises(DecisionError):
        validate_answers(
            {
                "q": Question(
                    type="choice", instructions="Choose", criteria={"a": "one", "b": "two"}
                )
            },
            {
                "q": {
                    "type": "choice",
                    "choice": "a",
                    "confidence": 0.9,
                    "probabilities": {"a": 0.1, "b": 0.9},
                }
            },
        )


def test_new_settings_parent_is_private(tmp_path):
    from local_decision_kit.integrations import install

    path = tmp_path / ".claude" / "settings.json"
    install(path, "/opt/kit/ldk", "mcp__docs__search")
    assert path.parent.stat().st_mode & 0o777 == 0o700
