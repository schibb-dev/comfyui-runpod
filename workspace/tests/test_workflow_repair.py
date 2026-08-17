#!/usr/bin/env python3
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from workflow_repair import (  # noqa: E402
    RepairContext,
    DeclarativePromptErrorRules,
    FlattenLibraryOutputPrefixRule,
    FlattenLibraryOutputPrefixPromptRule,
    NodeTypeRenameRule,
    PromptStringImageMismatchRule,
    StringConcatenateLegacyTextInputsRule,
    _error_matches_spec,
    default_repair_rules,
    load_prompt_error_rules,
    load_type_mappings,
    migrate_string_concatenate_prompt_inputs,
    repair_until_stable,
)


class WorkflowRepairTest(unittest.TestCase):
    def test_node_type_rename_rule(self) -> None:
        workflow = {
            "nodes": [
                {
                    "id": 88,
                    "type": "LoadImageWithFilename|pysssss",
                    "properties": {"Node name for S&R": "LoadImageWithFilename|pysssss"},
                    "outputs": [
                        {"name": "IMAGE", "type": "IMAGE"},
                        {"name": "MASK", "type": "MASK"},
                        {"name": "FILENAME", "type": "STRING"},
                    ],
                    "widgets_values": ["foo.png"],
                }
            ]
        }
        ctx = RepairContext(workflow=workflow, object_info={"LoadImage": {}})
        rule = NodeTypeRenameRule()
        self.assertTrue(rule.matches(ctx))
        fixes = rule.apply(ctx)
        self.assertEqual(len(fixes), 1)
        self.assertEqual(ctx.workflow["nodes"][0]["type"], "LoadImage")

    def test_repair_loop_ui_then_prompt(self) -> None:
        workflow = {
            "nodes": [
                {
                    "id": 451,
                    "type": "StringConcatenate",
                    "inputs": [{"name": "string_a", "link": 1}],
                },
                {"id": 88, "type": "LoadImage", "inputs": []},
            ],
            "links": [[1, 88, 0, 451, 0, "IMAGE"]],
        }
        prompt = {"451": {"class_type": "StringConcatenate", "inputs": {"string_a": ["88", 0]}}}
        rounds = {"n": 0}

        def validate_fn(ctx: RepairContext) -> dict:
            rounds["n"] += 1
            if ctx.prompt is None:
                ctx.prompt = json.loads(json.dumps(prompt))
            if ctx.prompt.get("451", {}).get("inputs", {}).get("string_a") == "":
                report = {"ok": True, "node_errors": {}}
            else:
                report = {
                    "ok": False,
                    "node_errors": {
                        "451": {
                            "errors": [
                                {
                                    "type": "return_type_mismatch",
                                    "details": "string_a, received_type(IMAGE) mismatch input_type(STRING)",
                                }
                            ]
                        }
                    },
                }
            ctx.report = report
            return report

        ctx = RepairContext(workflow=workflow, object_info={"LoadImage": {}, "StringConcatenate": {}})
        loop = repair_until_stable(
            ctx,
            rules=[PromptStringImageMismatchRule()],
            validate_fn=validate_fn,
            max_rounds=3,
        )
        self.assertGreaterEqual(len(loop.fixes), 1)
        self.assertEqual(ctx.prompt["451"]["inputs"]["string_a"], "")

    def test_map_includes_pysssss_load_image(self) -> None:
        mappings = load_type_mappings()
        self.assertEqual(mappings.get("LoadImageWithFilename|pysssss"), "LoadImage")

    def test_yaml_prompt_rules_loaded(self) -> None:
        rules = load_prompt_error_rules()
        ids = {str(r.get("id")) for r in rules}
        self.assertIn("sanitize_image_string_inputs", ids)
        self.assertIn("return_type_mismatch_image_to_string", ids)

    def test_error_match_spec(self) -> None:
        err = {
            "type": "return_type_mismatch",
            "details": "string_a, received_type(IMAGE) mismatch input_type(STRING)",
            "extra_info": {"input_name": "string_a"},
        }
        spec = {
            "error_type": "return_type_mismatch",
            "details_contains": ["received_type(IMAGE)", "input_type(STRING)"],
        }
        self.assertTrue(_error_matches_spec(err, spec))

    def test_declarative_proactive_apply(self) -> None:
        workflow = {
            "nodes": [
                {"id": 451, "type": "StringConcatenate"},
                {"id": 88, "type": "LoadImage"},
            ]
        }
        prompt = {"451": {"class_type": "StringConcatenate", "inputs": {"string_a": ["88", 0]}}}
        rule = DeclarativePromptErrorRules()
        ctx = RepairContext(workflow=workflow, prompt=prompt)
        self.assertTrue(rule.matches(ctx))
        fixes = rule.apply(ctx)
        self.assertTrue(fixes)
        self.assertEqual(prompt["451"]["inputs"]["string_a"], "")
        self.assertEqual(fixes[0].rule_id, "sanitize_image_string_inputs")

    def test_flatten_library_output_prefix_ui_rule(self) -> None:
        workflow = {
            "nodes": [
                {
                    "id": 80,
                    "type": "VHS_VideoCombine",
                    "widgets_values": {"filename_prefix": "output/og/2026-07-09/job"},
                }
            ]
        }
        rule = FlattenLibraryOutputPrefixRule()
        ctx = RepairContext(workflow=workflow)
        self.assertTrue(rule.matches(ctx))
        fixes = rule.apply(ctx)
        self.assertTrue(fixes)
        self.assertEqual(
            workflow["nodes"][0]["widgets_values"]["filename_prefix"],
            "og/2026-07-09/job",
        )

    def test_flatten_library_output_prefix_prompt_rule(self) -> None:
        workflow = {"nodes": []}
        prompt = {
            "9": {
                "class_type": "VHS_VideoCombine",
                "inputs": {"filename_prefix": "output/output/og/2026-07-09/job"},
            }
        }
        rule = FlattenLibraryOutputPrefixPromptRule()
        ctx = RepairContext(workflow=workflow, prompt=prompt)
        self.assertTrue(rule.matches(ctx))
        fixes = rule.apply(ctx)
        self.assertTrue(fixes)
        self.assertEqual(prompt["9"]["inputs"]["filename_prefix"], "og/2026-07-09/job")

    def test_default_repair_rules_include_flatten_prefix(self) -> None:
        ids = {type(r).__name__ for r in default_repair_rules()}
        self.assertIn("FlattenLibraryOutputPrefixRule", ids)
        self.assertIn("FlattenLibraryOutputPrefixPromptRule", ids)
        self.assertIn("EasyConvertAnythingToIntBypassRule", ids)
        self.assertIn("EasyPromptReplaceWidgetPadRule", ids)
        self.assertIn("UnetGgufBackslashPathRule", ids)

    def test_easy_convert_anything_replaced_with_vfi_float_to_int(self) -> None:
        from workflow_repair import EasyConvertAnythingToIntBypassRule

        workflow = {
            "nodes": [
                {
                    "id": 425,
                    "type": "easy mathFloat",
                    "outputs": [{"name": "FLOAT", "type": "FLOAT", "links": [908]}],
                },
                {
                    "id": 428,
                    "type": "easy convertAnything",
                    "title": "toInt",
                    "widgets_values": ["int"],
                    "inputs": [{"name": "*", "type": "*", "link": 908}],
                    "outputs": [{"name": "int", "type": "INT", "links": [938]}],
                },
                {
                    "id": 446,
                    "type": "easy mathInt",
                    "inputs": [{"name": "a", "type": "INT", "link": 938}],
                },
            ],
            "links": [
                [908, 425, 0, 428, 0, "*"],
                [938, 428, 0, 446, 0, "INT"],
            ],
        }
        rule = EasyConvertAnythingToIntBypassRule()
        ctx = RepairContext(workflow=workflow)
        self.assertTrue(rule.matches(ctx))
        fixes = rule.apply(ctx)
        self.assertTrue(fixes)
        node = next(n for n in workflow["nodes"] if n["id"] == 428)
        self.assertEqual(node["type"], "VFI FloatToInt")
        self.assertEqual(node["inputs"][0]["name"], "float")
        self.assertEqual(node["outputs"][0]["name"], "INT")
        into = next(l for l in workflow["links"] if l[3] == 428)
        self.assertEqual(into[5], "FLOAT")

    def test_easy_prompt_replace_widget_pad(self) -> None:
        from workflow_repair import EasyPromptReplaceWidgetPadRule

        workflow = {
            "nodes": [
                {
                    "id": 411,
                    "type": "easy promptReplace",
                    "widgets_values": ["photo", "video", "image", "video"],
                }
            ]
        }
        rule = EasyPromptReplaceWidgetPadRule()
        ctx = RepairContext(workflow=workflow)
        self.assertTrue(rule.matches(ctx))
        rule.apply(ctx)
        self.assertEqual(
            workflow["nodes"][0]["widgets_values"],
            ["", "photo", "video", "image", "video"],
        )

    def test_unet_gguf_backslash_path(self) -> None:
        from workflow_repair import UnetGgufBackslashPathRule

        workflow = {
            "nodes": [
                {
                    "id": 458,
                    "type": "UnetLoaderGGUFDisTorchMultiGPU",
                    "widgets_values": ["WAN\\wan2.1-i2v-14b-720p-Q5_K_M.gguf", "cuda:0"],
                }
            ]
        }
        rule = UnetGgufBackslashPathRule()
        ctx = RepairContext(workflow=workflow)
        self.assertTrue(rule.matches(ctx))
        rule.apply(ctx)
        self.assertEqual(
            workflow["nodes"][0]["widgets_values"][0],
            "WAN/wan2.1-i2v-14b-720p-Q5_K_M.gguf",
        )


class MissingAssetRemapTest(unittest.TestCase):
    def test_resolve_by_hash_suffix(self) -> None:
        from workflow_repair import MissingAssetRemapRule, _resolve_missing_asset

        data_root = Path("/home/yuji/comfyui-runpod-data")
        missing = "CropFXXX1485ba2d80130dfcf11e17f84b8bdb94169a8db84e06afdc97bd28bdd3ee3db9.jpeg"
        resolved = _resolve_missing_asset(missing, data_root)
        self.assertIsNotNone(resolved)
        self.assertTrue(resolved.is_file())

    def test_apply_remaps_load_image_widget(self) -> None:
        from workflow_repair import MissingAssetRemapRule

        missing = "CropFXXX1485ba2d80130dfcf11e17f84b8bdb94169a8db84e06afdc97bd28bdd3ee3db9.jpeg"
        workflow = {"nodes": [{"id": 88, "type": "LoadImage", "widgets_values": [missing, "image"]}]}
        ctx = RepairContext(workflow=workflow, data_root=Path("/home/yuji/comfyui-runpod-data"))
        rule = MissingAssetRemapRule()
        self.assertTrue(rule.matches(ctx))
        fixes = rule.apply(ctx)
        self.assertTrue(fixes)
        self.assertNotEqual(workflow["nodes"][0]["widgets_values"][0], missing)


class StringConcatenateLegacyTextInputsTest(unittest.TestCase):
    def test_migrate_hybrid_text_b_link_wins_over_weak_string_b(self) -> None:
        inputs = {
            "string_a": ", ",
            "string_b": "true",
            "text_b": ["380", 0],
        }
        changed = migrate_string_concatenate_prompt_inputs(inputs)
        self.assertTrue(changed)
        self.assertNotIn("text_b", inputs)
        self.assertEqual(inputs["string_b"], ["380", 0])
        self.assertEqual(inputs["string_a"], ", ")

    def test_ui_rename_remaps_text_slots(self) -> None:
        workflow = {
            "nodes": [
                {
                    "id": 224,
                    "type": "Text Concatenate",
                    "properties": {"Node name for S&R": "Text Concatenate"},
                    "inputs": [
                        {"name": "text_a", "link": 1},
                        {"name": "text_b", "link": 2},
                        {"name": "text_c", "link": None},
                    ],
                }
            ]
        }
        ctx = RepairContext(workflow=workflow, object_info={"StringConcatenate": {}})
        rule = NodeTypeRenameRule()
        self.assertTrue(rule.matches(ctx))
        rule.apply(ctx)
        node = workflow["nodes"][0]
        self.assertEqual(node["type"], "StringConcatenate")
        names = [inp["name"] for inp in node["inputs"]]
        self.assertEqual(names, ["string_a", "string_b"])

    def test_prompt_rule_strips_text_kwargs(self) -> None:
        prompt = {
            "224": {
                "class_type": "StringConcatenate",
                "inputs": {
                    "string_a": ", ",
                    "string_b": "true",
                    "text_b": ["380", 0],
                },
            }
        }
        rule = StringConcatenateLegacyTextInputsRule()
        ctx = RepairContext(workflow={}, prompt=prompt)
        self.assertTrue(rule.matches(ctx))
        fixes = rule.apply(ctx)
        self.assertEqual(len(fixes), 1)
        self.assertEqual(fixes[0].rule_id, "string_concatenate_legacy_text_inputs")
        self.assertNotIn("text_b", prompt["224"]["inputs"])
        self.assertEqual(prompt["224"]["inputs"]["string_b"], ["380", 0])
        self.assertTrue(
            any(isinstance(r, StringConcatenateLegacyTextInputsRule) for r in default_repair_rules())
        )


if __name__ == "__main__":
    unittest.main()
