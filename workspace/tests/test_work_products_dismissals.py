#!/usr/bin/env python3
"""Workbench history-stub dismissals prefer a writable shape_factory path."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

_TMP_ROOT = Path("/dev/shm") if Path("/dev/shm").is_dir() else None


def _tmpdir() -> tempfile.TemporaryDirectory:
    if _TMP_ROOT is not None:
        return tempfile.TemporaryDirectory(dir=str(_TMP_ROOT))
    return tempfile.TemporaryDirectory()


class TestWorkProductsDismissals(unittest.TestCase):
    def test_canonical_path_when_shape_factory_writable(self) -> None:
        from shape_factory_work_products import (
            dismiss_history_work_product,
            is_work_product_dismissed,
            load_work_products_dismissals,
            work_products_dismissals_path,
        )

        with _tmpdir() as td:
            root = Path(td)
            sf = root / "shape_factory"
            jobs = sf / "jobs"
            out = root / "output"
            jobs.mkdir(parents=True)
            (out / "_status").mkdir(parents=True)

            path = work_products_dismissals_path(root, output_root=out)
            self.assertEqual(path, sf / "work_products_dismissed.json")

            res = dismiss_history_work_product(
                data_root=root,
                prompt_id="pid-1",
                job_key="job-1",
                output_root=out,
            )
            self.assertTrue(res.get("ok"))
            self.assertEqual(Path(res["dismissals_path"]), path)
            doc = load_work_products_dismissals(root, output_root=out)
            self.assertTrue(is_work_product_dismissed(doc, prompt_id="pid-1"))
            self.assertTrue(is_work_product_dismissed(doc, job_key="job-1"))

    def test_falls_back_when_shape_factory_not_writable(self) -> None:
        from shape_factory_work_products import work_products_dismissals_path

        with _tmpdir() as td:
            root = Path(td)
            sf = root / "shape_factory"
            jobs = sf / "jobs"
            out = root / "output" / "_status"
            jobs.mkdir(parents=True)
            out.mkdir(parents=True)
            # Simulate RO shape_factory by removing write bit on the dir.
            sf.chmod(0o555)
            try:
                path = work_products_dismissals_path(root, output_root=out.parent)
                self.assertIn(
                    path,
                    {
                        jobs / "work_products_dismissed.json",
                        out / "work_products_dismissed.json",
                    },
                )
            finally:
                sf.chmod(0o755)


if __name__ == "__main__":
    unittest.main()
