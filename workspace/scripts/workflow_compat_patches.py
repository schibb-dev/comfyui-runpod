"""Backward-compatible re-exports; prefer workflow_repair.py."""

from workflow_repair import (  # noqa: F401
    apply_workflow_compat_patches,
    load_type_mappings,
    patchable_missing_types,
)
