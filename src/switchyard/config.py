"""Per-user configuration. Installation does not touch agent configuration."""

import json
import os
import tempfile
from pathlib import Path
from typing import Literal

from platformdirs import user_data_path
from pydantic import Field

from .contracts import StrictModel
from .errors import DecisionError


def default_home() -> Path:
    return Path(os.environ.get("SWITCHYARD_HOME", user_data_path("switchyard"))).expanduser()


def private_directory(path: Path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or path.stat().st_uid != os.getuid():
        raise DecisionError("unsafe_path", "Directory must be owned by the current user")
    if path.stat().st_mode & 0o077:
        raise DecisionError("unsafe_path", "Directory must have mode 0700")


def atomic_json(path: Path, value):
    fd, temporary = tempfile.mkstemp(prefix=".switchyard-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Settings(StrictModel):
    home: Path = Field(default_factory=default_home)
    model_directory: Path | None = None
    models: list[Literal["english", "multilingual", "typed-decisions"]] = Field(
        default_factory=lambda: ["english", "multilingual"], min_length=1, max_length=3
    )
    threads: int = Field(default=4, ge=1, le=64)
    queue_size: int = Field(default=16, ge=1, le=128)
    socket_timeout_seconds: int = Field(default=125, ge=1, le=180)

    @property
    def socket(self) -> Path:
        return self.home / "runtime" / "switchyard.sock"

    @property
    def model_store(self) -> Path:
        return self.model_directory or self.home / "models"


def load_settings(home: Path | None = None) -> Settings:
    root = (home or default_home()).resolve()
    path = root / "config.json"
    if path.exists():
        data = json.loads(path.read_text())
        data["home"] = root
        return Settings.model_validate(data)
    return Settings(home=root)


def initialize(home: Path | None = None) -> Settings:
    settings = load_settings(home)
    private_directory(settings.home)
    private_directory(settings.home / "runtime")
    private_directory(settings.model_store)
    if not (settings.home / "config.json").exists():
        atomic_json(
            settings.home / "config.json", settings.model_dump(mode="json", exclude={"home"})
        )
    return settings
