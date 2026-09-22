import json

import pytest


def test_install_preview_preserves_existing_settings_and_remove_restores(tmp_path):
    from switchyard.integrations import install, preview, remove

    path = tmp_path / "settings.json"
    original = {
        "permissions": {"deny": ["Bash(rm:*)"]},
        "hooks": {"PostToolUse": [{"matcher": "Edit", "hooks": []}]},
    }
    path.write_text(json.dumps(original))
    before = path.read_bytes()
    proposal = preview(path, "/opt/switchyard/bin/switchyard", "mcp__search__find")
    assert path.read_bytes() == before
    assert proposal["after"]["permissions"] == original["permissions"]
    install(path, "/opt/switchyard/bin/switchyard", "mcp__search__find")
    assert len(json.loads(path.read_text())["hooks"]["PostToolUse"]) == 2
    remove(path)
    assert path.read_bytes() == before


def test_removal_preserves_user_edits_after_install(tmp_path):
    from switchyard.integrations import install, remove

    path = tmp_path / "settings.json"
    path.write_text('{"other": true}')
    install(path, "/opt/switchyard/bin/switchyard", "mcp__search__find")
    settings = json.loads(path.read_text())
    settings["new_setting"] = "keep"
    path.write_text(json.dumps(settings))
    remove(path)
    assert json.loads(path.read_text()) == {"other": True, "new_setting": "keep"}


def test_install_is_idempotent_and_rejects_wildcard_matchers(tmp_path):
    from switchyard.integrations import install

    path = tmp_path / "settings.json"
    install(path, "/opt/switchyard/bin/switchyard", "mcp__search__find")
    first = path.read_bytes()
    install(path, "/opt/switchyard/bin/switchyard", "mcp__search__find")
    assert path.read_bytes() == first
    with pytest.raises(ValueError):
        install(path, "/opt/switchyard/bin/switchyard", ".*")


def test_hook_failure_and_unrecognized_output_are_passthrough():
    from switchyard.hooks import process_event

    assert process_event({"hook_event_name": "PostToolUse", "tool_response": "arbitrary"}) == {}
    assert process_event({"hook_event_name": "PreToolUse", "tool_response": {}}) == {}
