"""Optional, read-only MainFrame Vault candidate provider."""

import hashlib
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

CURATED_TIERS = ("Projects", "Stacks", "Patterns", "Guidelines", "Notes")
_RESULT = re.compile(r"^(?P<path>[^\n:]+):(?P<line>[0-9]+):(?P<text>.*)$")


class MainFrameProvider:
    """Search curated MainFrame tiers without writing to the Vault or invoking a shell."""

    def __init__(
        self,
        vault_root: Path,
        *,
        search_binary: str | None = None,
        timeout_seconds: float = 10,
        runner: Callable[[str, Path], str] | None = None,
    ):
        self.vault_root = Path(vault_root).expanduser()
        self.search_binary = search_binary or shutil.which("rg")
        self.timeout_seconds = timeout_seconds
        self.runner = runner

    def _run_search(self, query: str, root: Path) -> str:
        if not self.search_binary:
            raise RuntimeError("ripgrep is required for the MainFrame provider")
        directories = [str(root / tier) for tier in CURATED_TIERS if (root / tier).is_dir()]
        if not directories:
            return ""
        completed = subprocess.run(
            [
                self.search_binary,
                "-n",
                "--no-heading",
                "-S",
                "--glob",
                "*.md",
                "-e",
                query,
                *directories,
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        if completed.returncode not in (0, 1):
            raise RuntimeError("MainFrame search failed")
        return completed.stdout

    def search(self, query: str) -> list[dict]:
        if not query.strip():
            raise ValueError("Search query must not be blank")
        root = self.vault_root.resolve()
        if not root.is_dir():
            raise ValueError(f"Vault root does not exist: {root}")
        output = (self.runner or self._run_search)(query, root)
        candidates = []
        seen = set()
        for line in output.splitlines():
            match = _RESULT.match(line)
            if not match:
                continue
            relative = Path(match.group("path"))
            if relative.is_absolute():
                try:
                    relative = relative.resolve().relative_to(root)
                except ValueError:
                    continue
            if not relative.parts or relative.parts[0] not in CURATED_TIERS:
                continue
            citation = f"{relative.as_posix()}:{match.group('line')}"
            text = match.group("text")
            identity = hashlib.sha256(f"{citation}\n{text}".encode()).hexdigest()[:16]
            if identity in seen:
                continue
            seen.add(identity)
            candidates.append(
                {
                    "id": f"mainframe:{identity}",
                    "text": text,
                    "citation": citation,
                    "trust": "mainframe-curated",
                    "mandatory": False,
                }
            )
        return candidates
