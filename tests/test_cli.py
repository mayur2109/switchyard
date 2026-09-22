import json

from typer.testing import CliRunner


def test_cli_init_doctor_and_schemas(tmp_path, monkeypatch):
    from switchyard.cli import app

    monkeypatch.setenv("SWITCHYARD_HOME", str(tmp_path / "kit"))
    runner = CliRunner()
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["models"] == ["english", "multilingual"]
    schema = runner.invoke(app, ["schemas"])
    assert schema.exit_code == 0
    assert "DecisionRequest" in json.loads(schema.output)
    doctor = runner.invoke(app, ["doctor"])
    assert doctor.exit_code == 1
    assert json.loads(doctor.output)["ready"] is False


def test_invalid_stdin_returns_structured_error_without_echoing_content():
    from switchyard.cli import app

    result = CliRunner().invoke(app, ["decide"], input='{"secret": "must-not-echo"}')
    assert result.exit_code == 2
    assert "must-not-echo" not in result.output
    assert json.loads(result.output)["error"]["code"] == "invalid_request"


def test_mcp_tool_contracts_do_not_expose_execution():
    import asyncio

    from switchyard.mcp_server import create_server

    server = create_server()
    tools = asyncio.run(server.list_tools())
    assert {tool.name for tool in tools} == {"decide", "evaluate_items", "capabilities"}
    for tool in tools:
        assert tool.annotations.readOnlyHint is True
