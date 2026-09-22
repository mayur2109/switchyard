"""Reversible, explicitly requested Claude hook configuration edits."""

import base64
import copy
import hashlib
import json
import re
import shlex
from pathlib import Path

from .config import atomic_json
from .errors import DecisionError


def _entry(executable: str, tool: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_:-]+", tool):
        raise ValueError("Provide one exact tool name; patterns and wildcards are not allowed")
    if not Path(executable).is_absolute():
        raise ValueError("Use an absolute executable path")
    return {
        "matcher": "^" + re.escape(tool) + "$",
        "hooks": [{"type": "command", "command": shlex.quote(executable) + " hook", "timeout": 5}],
    }


def _metadata(path: Path) -> Path:
    return path.with_name(path.name + ".switchyard-backup.json")


def preview(path: Path, executable: str, tool: str) -> dict:
    before = json.loads(path.read_text()) if path.exists() else {}
    after = copy.deepcopy(before)
    entry = _entry(executable, tool)
    entries = after.setdefault("hooks", {}).setdefault("PostToolUse", [])
    if entry not in entries:
        entries.append(entry)
    return {"path": str(path), "before": before, "after": after}


def install(path: Path, executable: str, tool: str) -> dict:
    proposal = preview(path, executable, tool)
    entry = _entry(executable, tool)
    backup = _metadata(path)
    if backup.exists():
        metadata = json.loads(backup.read_text())
        if metadata["entry"] == entry and entry in proposal["before"].get("hooks", {}).get(
            "PostToolUse", []
        ):
            return {"installed": True, "changed": False}
        raise DecisionError(
            "configuration_conflict", "Remove the existing managed integration first"
        )
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    original = path.read_bytes() if path.exists() else None
    metadata = {
        "entry": entry,
        "original": base64.b64encode(original).decode() if original is not None else None,
        "original_hooks": proposal["before"].get("hooks"),
    }
    atomic_json(backup, metadata)
    atomic_json(path, proposal["after"])
    metadata["installed_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    atomic_json(backup, metadata)
    return {"installed": True, "changed": True, "backup": str(backup)}


def remove(path: Path) -> dict:
    backup = _metadata(path)
    if not backup.exists():
        return {"removed": False}
    metadata = json.loads(backup.read_text())
    if not path.exists():
        raise DecisionError("configuration_conflict", "Settings were removed; backup retained")
    current_bytes = path.read_bytes()
    if hashlib.sha256(current_bytes).hexdigest() == metadata.get("installed_sha256"):
        if metadata["original"] is None:
            path.unlink()
        else:
            import os
            import tempfile

            fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".switchyard-restore-")
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(base64.b64decode(metadata["original"]))
                os.replace(temp, path)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
    else:
        current = json.loads(current_bytes)
        hooks = current.get("hooks", {})
        entries = hooks.get("PostToolUse", [])
        if metadata["entry"] not in entries:
            raise DecisionError(
                "configuration_conflict", "Managed hook was edited; backup retained"
            )
        entries.remove(metadata["entry"])
        original_hooks = metadata.get("original_hooks") or {}
        if not entries and "PostToolUse" not in original_hooks:
            hooks.pop("PostToolUse", None)
        if not hooks and metadata["original_hooks"] is None:
            current.pop("hooks", None)
        atomic_json(path, current)
    backup.unlink()
    return {"removed": True, "recoverable": "Original settings restored or user changes preserved"}
