import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from output_path_lib import (  # noqa: E402
    flatten_output_prefix,
    normalize_prompt_output_prefixes,
)


def test_flatten_output_prefix_strips_single_output():
    assert flatten_output_prefix("output/og/2026-07-09/foo") == "og/2026-07-09/foo"


def test_flatten_output_prefix_collapses_double_nest():
    assert (
        flatten_output_prefix("output/output/og/2026-07-09/FB9_GEX2_shape/job")
        == "og/2026-07-09/FB9_GEX2_shape/job"
    )


def test_flatten_output_prefix_leaves_unrelated_paths():
    assert flatten_output_prefix("WAN") == "WAN"
    assert flatten_output_prefix("output/custom/foo") == "output/custom/foo"


def test_flatten_output_prefix_skips_absolute():
    assert flatten_output_prefix("/workspace/output/og/x") == "/workspace/output/og/x"


def test_normalize_prompt_output_prefixes_mutates_prompt():
    prompt = {
        "9": {
            "class_type": "VHS_VideoCombine",
            "inputs": {"filename_prefix": "output/output/og/2026-07-09/job"},
        }
    }
    changes = normalize_prompt_output_prefixes(prompt)
    assert len(changes) == 1
    assert prompt["9"]["inputs"]["filename_prefix"] == "og/2026-07-09/job"


def test_normalize_ui_workflow_output_prefixes_dict_widgets():
    from output_path_lib import normalize_ui_workflow_output_prefixes

    workflow = {
        "nodes": [
            {
                "id": 80,
                "type": "VHS_VideoCombine",
                "widgets_values": {
                    "filename_prefix": "output/og/2026-07-09/FB9_GEX2_OVERHEAD",
                },
            }
        ]
    }
    changes = normalize_ui_workflow_output_prefixes(workflow)
    assert changes
    assert workflow["nodes"][0]["widgets_values"]["filename_prefix"] == "og/2026-07-09/FB9_GEX2_OVERHEAD"
