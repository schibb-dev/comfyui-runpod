"""Deposit must not re-index missing outputs; pool prune-missing drops ghosts."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


class PoolPruneMissingTests(unittest.TestCase):
    def test_prune_drops_missing_and_raw_members(self) -> None:
        from shape_factory import prune_missing_pool_index_members

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            live = root / "live.mp4"
            live.write_bytes(b"ok")
            doc = {
                "pools": {
                    "X_og": {
                        "members": [
                            {"path": str(live), "source": "seed"},
                            {"path": str(root / "gone.mp4"), "source": "shape_factory"},
                            {
                                "path": str(root / "234650_FaceBlast8J_RAW_00001.mp4"),
                                "source": "seed",
                            },
                        ]
                    }
                }
            }
            removed = prune_missing_pool_index_members(doc, also_raw=True)
            reasons = {r["reason"] for r in removed}
            self.assertEqual(reasons, {"missing_file", "raw_or_preview_name"})
            kept = doc["pools"]["X_og"]["members"]
            self.assertEqual(len(kept), 1)
            self.assertEqual(kept[0]["path"], str(live))

    def test_deposit_skips_missing_outputs(self) -> None:
        import shape_factory as sf

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            jobs = root / "jobs"
            jobs.mkdir()
            pools = root / "pools" / "DEMO"
            pools.mkdir(parents=True)
            (pools / "pools.yaml").write_text(
                "schema_version: comfyui-runpod.pools.v0\n"
                "deposit_pools:\n  DEMO_X_og:\n    slot: final_video\n",
                encoding="utf-8",
            )
            index_path = pools / "index.json"
            index_path.write_text(
                json.dumps({"pools": {"DEMO_X_og": {"members": []}}}),
                encoding="utf-8",
            )
            ghost = root / "missing.mp4"
            job_path = jobs / "demo.job.json"
            job = {
                "job_key": "demo",
                "pools_path": str(pools / "pools.yaml"),
                "deposits": {"final_video": {"to_pool": "pool:DEMO_X_og"}},
                "submit": {
                    "status": "complete",
                    "outputs": [str(ghost)],
                    "output_discovery": "filesystem",
                },
            }
            job_path.write_text(json.dumps(job), encoding="utf-8")

            args = SimpleNamespace(
                job=str(job_path),
                jobs_dir=None,
                family=None,
                job_dir=str(jobs),
                limit=None,
                data_root=str(root),
                pools=str(pools / "pools.yaml"),
                index=str(index_path),
                quiet=False,
            )
            rc = sf.cmd_deposit(args)
            self.assertEqual(rc, 0)
            healed = json.loads(job_path.read_text(encoding="utf-8"))
            self.assertEqual(healed["submit"].get("outputs"), [])
            idx = json.loads(index_path.read_text(encoding="utf-8"))
            self.assertEqual(idx["pools"]["DEMO_X_og"]["members"], [])


if __name__ == "__main__":
    unittest.main()
