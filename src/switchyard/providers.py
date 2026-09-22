"""Read-only candidate-provider boundary for retrieval integrations."""

from typing import Protocol, runtime_checkable

from .contracts import Item


@runtime_checkable
class CandidateProvider(Protocol):
    """A retrieval adapter that returns caller-owned candidate records."""

    def search(self, query: str) -> list[dict]:
        ...


def normalize_candidates(values: list[dict]) -> list[Item]:
    """Validate provider output without adding retrieval or write capabilities."""
    return [Item.model_validate(value) for value in values]
