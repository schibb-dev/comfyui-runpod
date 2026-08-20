import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from datetime import datetime

from output_path_lib import (  # noqa: E402
    apply_queue_date_to_prefix,
    apply_queue_date_to_prompt,
    expand_date_tokens,
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


def test_expand_date_tokens_uses_given_now():
    assert (
        expand_date_tokens("og/%date:yyyy-MM-dd%/hourly/job", now=datetime(2026, 8, 18, 9, 0, 0))
        == "og/2026-08-18/hourly/job"
    )


def test_apply_queue_date_rewrites_baked_og_folder():
    assert (
        apply_queue_date_to_prefix(
            "og/2026-08-16/hourly/hourly__still-001302_202608162207",
            now=datetime(2026, 8, 18, 9, 0, 0),
        )
        == "og/2026-08-18/hourly/hourly__still-001302_202608162207"
    )


def test_apply_queue_date_expands_leftover_date_token():
    assert (
        apply_queue_date_to_prefix(
            "og/%date:yyyy-MM-dd%/hourly/job",
            now=datetime(2026, 8, 18, 9, 0, 0),
        )
        == "og/2026-08-18/hourly/job"
    )


def test_normalize_prompt_stamps_queue_day_on_restore():
    prompt = {
        "398": {
            "class_type": "VHS_VideoCombine",
            "inputs": {
                "filename_prefix": "og/2026-08-16/hourly/hourly__still-001302_202608162207",
            },
        }
    }
    apply_queue_date_to_prompt(prompt, now=datetime(2026, 8, 18, 6, 30, 0))
    assert (
        prompt["398"]["inputs"]["filename_prefix"]
        == "og/2026-08-18/hourly/hourly__still-001302_202608162207"
    )


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
