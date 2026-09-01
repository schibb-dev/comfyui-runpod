# Family discovery — operator review

Generated `2026-09-01T11:51:59Z`.

- Covered clusters (already enrolled): **15**
- Uncovered clusters with proposals: **40** (of 123 uncovered)
- Sample videos: up to **20** per prop via fingerprint exemplar index (`family_discovery_exemplars.json`), not output naming.

## How to review

**UI (preferred):** open Experiments UI → Workflow Explorer → **Family review** tab.

**Manual / CLI:**

1. Open each `prop_NNN.md` and watch listed sample videos.
2. Decide: **new family** / **merge** into an enrolled slug / **skip**.
3. Edit the matching `prop_NNN.json`: set `status`, `proposed_family_slug` (or `nearest_enrolled` for merge), and `operator_notes`.
4. For approved new families, run enroll.

## Proposal index

| id | IO | members | samples | representative | status |
|----|----|---------|---------|----------------|--------|
| prop_001 | I2V | 1 | 20 | `004359_OG_00001-readable.json` | pending_review |
| prop_002 | I2V | 1 | 0 | `FB8VA5-laying-down-readable.json` | pending_review |
| prop_003 | I2V | 1 | 20 | `103520_OG_00001-readable.json` | pending_review |
| prop_004 | VI2V | 1 | 20 | `FB8VB2_2026-01-06_234750_EXT_00001-readable.json` | pending_review |
| prop_005 | I2V | 1 | 20 | `141756_OG_00001-readable.json` | pending_review |
| prop_006 | I2V | 1 | 20 | `2025-12-06-215827_filename_RAW_00001-readable.json` | pending_review |
| prop_007 | I2V | 65 | 20 | `FB8VA-mainline-readable.json` | pending_review |
| prop_008 | V2V | 17 | 20 | `ASTONISH_FB9_GEX_2026-03-03_00023 (2).json` | pending_review |
| prop_009 | I2V | 17 | 20 | `FB8VA5-laying-down.cleaned.json` | pending_review |
| prop_010 | I2V | 11 | 8 | `FB8VA5-laying-down.json` | pending_review |
| prop_011 | I2V | 7 | 1 | `FB8VA-mainline-dance.json` | pending_review |
| prop_012 | V2V | 5 | 9 | `EXPT-EXTENSION.json` | pending_review |
| prop_013 | I2V | 4 | 0 | `EXPT-PromptScheduleA.json` | pending_review |
| prop_014 | I2V | 4 | 12 | `FB9-Gen-PromptSchedule.json` | pending_review |
| prop_015 | I2V | 2 | 0 | `I2V-FizzNodes_FIXED.json` | pending_review |
| prop_016 | — | 2 | 0 | `LoRA.json` | pending_review |
| prop_017 | I2V | 2 | 0 | `EXPT-PromptScheduleB.json` | pending_review |
| prop_018 | — | 2 | 0 | `TXT to IMG 1.json` | pending_review |
| prop_019 | I2V | 2 | 0 | `INPAINT.json` | pending_review |
| prop_020 | I2V | 2 | 0 | `Undress_pulldown_FB8VA5L-2026-02-16-103025_OG_00001.json` | pending_review |
| prop_021 | VI2V | 2 | 0 | `PS-EXPT.json` | pending_review |
| prop_022 | I2V | 1 | 0 | `DualSampling (Simplified) v2.1.json` | pending_review |
| prop_023 | I2V | 1 | 0 | `PuLID_Redux.json` | pending_review |
| prop_024 | I2V | 1 | 0 | `EXPT-PromptSchedule.json` | pending_review |
| prop_025 | I2V | 1 | 0 | `FB8VA5L-1.prompt_schedule.json` | pending_review |
| prop_026 | I2V | 1 | 0 | `LTXV - IMG to VIDEO (gguf).json` | pending_review |
| prop_027 | V2V | 1 | 8 | `SB_extender_reconciled.json` | pending_review |
| prop_028 | I2V | 1 | 0 | `FLUX - INPAINT v7.0 (gguf).json` | pending_review |
| prop_029 | I2V | 1 | 0 | `CtrlNet HED.json` | pending_review |
| prop_030 | — | 1 | 0 | `FB9_GEX_DESPERATE_FACIAL.json` | pending_review |
| prop_031 | I2V | 1 | 0 | `Background changer.json` | pending_review |
| prop_032 | I2V | 1 | 0 | `WAN_2.1_IMG_to_VIDEO.json` | pending_review |
| prop_033 | I2V | 1 | 0 | `IMG to VIDEO.json` | pending_review |
| prop_034 | I2V | 1 | 0 | `2_pass_pose_worship.json` | pending_review |
| prop_035 | I2V | 1 | 0 | `FaceBlast8K_modified.json` | pending_review |
| prop_036 | I2V | 1 | 0 | `IMG to IMG.json` | pending_review |
| prop_037 | I2V | 1 | 0 | `SingleSampling (Simplified).json` | pending_review |
| prop_038 | V2V | 1 | 12 | `FB9-Gen-Extension.json` | pending_review |
| prop_039 | I2V | 1 | 0 | `HiDream - INPAINT (base).json` | pending_review |
| prop_040 | I2V | 1 | 0 | `Native-I2V-60FPS.json` | pending_review |

No families are auto-enrolled. Naming is the human gate.
