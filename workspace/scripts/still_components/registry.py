"""Named provider registries — swap implementations without touching callers."""

from __future__ import annotations

from typing import Dict, Generic, List, TypeVar

T = TypeVar("T")


class ProviderRegistry(Generic[T]):
    def __init__(self, kind: str) -> None:
        self.kind = str(kind or "provider").strip() or "provider"
        self._items: Dict[str, T] = {}

    def register(self, name: str, provider: T) -> T:
        key = str(name or "").strip()
        if not key:
            raise ValueError(f"empty {self.kind} provider name")
        self._items[key] = provider
        return provider

    def get(self, name: str) -> T:
        key = str(name or "").strip()
        if key not in self._items:
            known = ", ".join(sorted(self._items)) or "(none)"
            raise KeyError(f"unknown {self.kind} provider: {key!r} (have {known})")
        return self._items[key]

    def names(self) -> List[str]:
        return sorted(self._items.keys())

    def __contains__(self, name: object) -> bool:
        return str(name or "").strip() in self._items


similarity_providers: ProviderRegistry = ProviderRegistry("similarity")
