"""
Test harness helpers.

These tests are intended to run in two common layouts:
- Host/dev: repo contains `workspace/scripts/*` and tests run from `workspace/`
- Docker/compose: `workspace/scripts` is mounted at `/workspace/ws_scripts` while
  `/workspace/scripts` may contain other bootstrap utilities.

We dynamically pick the script directory that contains the workflow tooling and
add it to sys.path so imports like `import clean_comfy_workflow` work in both.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _pick_scripts_dir(root: Path) -> Path:
    candidates = [
        root / "scripts",     # host/dev: workspace/scripts
        root / "ws_scripts",  # docker: workspace/scripts mounted here
    ]
    # Prefer the candidate that actually contains our workflow tool modules.
    required_any = {
        "clean_comfy_workflow.py",
        "tune_experiment.py",
        "apply_comfy_preset.py",
        "comfy_meta_lib.py",
    }
    for d in candidates:
        try:
            if d.is_dir() and any((d / name).exists() for name in required_any):
                return d
        except Exception:
            continue
    # Fallback (keeps error messages predictable if nothing matches).
    return candidates[0]


SCRIPTS_DIR = _pick_scripts_dir(ROOT)

# Ensure our script modules are importable by name.
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

