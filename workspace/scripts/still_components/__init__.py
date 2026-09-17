"""Composable still-pipeline components: protocols + registries.

Callers (UI, factory, batch jobs) should talk to orchestrators, not providers.
"""

from still_components.protocols import (  # noqa: F401
    SimilarityHit,
    SimilarityProvider,
    TagSource,
    EmbedIndex,
)
from still_components.registry import ProviderRegistry, similarity_providers  # noqa: F401
