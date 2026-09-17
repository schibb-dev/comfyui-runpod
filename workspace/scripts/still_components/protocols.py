"""Narrow interfaces for still similarity, tagging, and embedding stores."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, Sequence, runtime_checkable


class SimilarityHit(Dict[str, Any]):
    """Ranked neighbor. Keys: content_id, score, reasons, provider, model_version, shared_tags."""


@runtime_checkable
class SimilarityProvider(Protocol):
    name: str

    def find_similar(self, query_id: str, *, limit: int = 24) -> List[Dict[str, Any]]:
        """Return ranked neighbors for ``query_id`` (sha256 content_id)."""


@runtime_checkable
class TagSource(Protocol):
    def tags_for(self, content_id: str) -> Dict[str, List[str]]:
        """Return editorial / provisional / effective tag lists for one still."""

    def iter_tagged(self) -> Sequence[Dict[str, Any]]:
        """Yield ``{content_id, editorial_tags, provisional_tags, effective_tags}``."""


@runtime_checkable
class EmbedIndex(Protocol):
    def get(self, content_id: str) -> Optional[Dict[str, Any]]:
        """Row with vector + model_id, or None."""

    def upsert(
        self,
        content_id: str,
        vector: Sequence[float],
        *,
        model_id: str,
        source_path: str = "",
    ) -> None: ...

    def nearest(self, content_id: str, *, limit: int = 24) -> List[Dict[str, Any]]:
        """Cosine neighbors excluding the query itself."""

    def stats(self) -> Dict[str, Any]: ...
