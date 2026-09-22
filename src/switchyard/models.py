"""Explicit, revision-pinned model installation and integrity checks."""

import hashlib
import json
import os
from pathlib import Path

from .config import Settings, atomic_json, private_directory
from .errors import DecisionError

REPOSITORY = "convaiinnovations/laya"
REVISION = "1c5edc17a7acd8701df6fc341c0d179f1c62c982"
SUBFOLDERS = {"english": "", "multilingual": "multilingual", "typed-decisions": "typed-decisions"}
FILES = [
    "rl_agent_config.json",
    "model.safetensors",
    "tokenizer/tokenizer.json",
    "tokenizer/tokenizer_config.json",
    "encoder/config.json",
]


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def model_root(settings: Settings, name: str) -> Path:
    if name not in SUBFOLDERS:
        raise DecisionError("unknown_model", "Unknown checkpoint")
    return settings.model_store / REVISION / name


def model_path(settings: Settings, name: str) -> Path:
    return model_root(settings, name) / SUBFOLDERS[name]


def fetch_model(settings: Settings, name: str) -> dict:
    """Only this operation accesses the model hub. No inference-time downloads."""
    import fcntl

    root = model_root(settings, name)
    private_directory(root)
    with (root / "fetch.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        manifest_path = root / "manifest.json"
        if manifest_path.exists():
            return verify_model(settings, name)
        from huggingface_hub import snapshot_download

        prefix = f"{SUBFOLDERS[name]}/" if SUBFOLDERS[name] else ""
        snapshot_download(
            REPOSITORY,
            revision=REVISION,
            local_dir=root,
            allow_patterns=[prefix + file for file in FILES],
            max_workers=2,
        )
        path = model_path(settings, name)
        original = {file: digest(path / file) for file in FILES}
        # Laya performs these compatibility edits on load. Do them once before hashing.
        tokenizer_path = path / "tokenizer/tokenizer_config.json"
        tokenizer = json.loads(tokenizer_path.read_text())
        if tokenizer.get("tokenizer_class") in (None, "TokenizersBackend"):
            tokenizer["tokenizer_class"] = "PreTrainedTokenizerFast"
            tokenizer.pop("backend", None)
            tokenizer.pop("is_local", None)
        if isinstance(tokenizer.get("extra_special_tokens"), list):
            tokenizer["extra_special_tokens"] = {
                f"extra_{i}": value for i, value in enumerate(tokenizer["extra_special_tokens"])
            }
        atomic_json(tokenizer_path, tokenizer)
        manifest = {
            "repository": REPOSITORY,
            "revision": REVISION,
            "model": name,
            "license": "Apache-2.0",
            "downloaded_sha256": original,
            "sha256": {file: digest(path / file) for file in FILES},
            "normalization": "Tokenizer configuration compatibility for transformers 4.x",
        }
        atomic_json(manifest_path, manifest)
        return manifest


def verify_model(settings: Settings, name: str) -> dict:
    root = model_root(settings, name)
    try:
        manifest = json.loads((root / "manifest.json").read_text())
        if manifest["revision"] != REVISION or manifest["model"] != name:
            raise ValueError("Model identity mismatch")
        if set(manifest["sha256"]) != set(FILES):
            raise ValueError("Incomplete manifest")
        for file in FILES:
            path = model_path(settings, name) / file
            if not path.is_file() or path.is_symlink() or digest(path) != manifest["sha256"][file]:
                raise ValueError("Artifact checksum mismatch")
        return manifest
    except (OSError, KeyError, ValueError) as error:
        raise DecisionError(
            "model_unavailable", f"Fetch or repair the {name} model with switchyard models fetch"
        ) from error


def offline_environment():
    for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY"):
        os.environ[key] = "1"
    os.environ["USE_TF"] = "0"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
