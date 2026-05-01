import os
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import warnings
from pathlib import Path

from support import ROOT, SCRIPTS_DIR


FIXTURES = ROOT / "tests" / "fixtures" / "media"
MANIFEST = FIXTURES / "manifest.json"


def _have_ffprobe() -> bool:
    return shutil.which("ffprobe") is not None


def _python() -> str:
    return sys.executable


def _discover_fixture_pairs() -> list[tuple[Path, Path]]:
    """
    Find *.mp4 with same-stem *.png in tests/fixtures/media (recursive).
    """
    mp4s = sorted(FIXTURES.rglob("*.mp4"))
    pairs: list[tuple[Path, Path]] = []
    for mp4 in mp4s:
        png = mp4.with_suffix(".png")
        if png.exists():
            pairs.append((mp4, png))
    return pairs


def _discover_manifest_pairs() -> list[tuple[Path, Path]]:
    """
    Load pairs from tests/fixtures/media/manifest.json.
    Paths are treated as workspace-relative (repo root).
    Missing files are ignored (so this works on machines without those samples).
    """
    if not MANIFEST.exists():
        return []
    obj = json.loads(MANIFEST.read_text(encoding="utf-8"))
    items = obj.get("pairs") if isinstance(obj, dict) else None
    if not isinstance(items, list):
        return []
    out: list[tuple[Path, Path]] = []
    missing: list[str] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        mp4 = it.get("mp4")
        png = it.get("png")
        if not isinstance(mp4, str) or not isinstance(png, str):
            continue
        mp4p = (ROOT / mp4).resolve()
        pngp = (ROOT / png).resolve()
        if mp4p.exists() and pngp.exists():
            out.append((mp4p, pngp))
        else:
            missing.append(f"mp4={mp4} (exists={mp4p.exists()}), png={png} (exists={pngp.exists()})")
    if missing:
        msg = (
            "media fixture manifest references missing files; "
            "those entries will be skipped:\n- " + "\n- ".join(missing)
        )
        # Make this visible even when the test otherwise passes.
        warnings.warn(msg, category=UserWarning)
        sys.stderr.write(msg + "\n")
    return out


class TestIntegrationMediaRoundtrip(unittest.TestCase):
    def test_roundtrip_on_fixture_media(self):
        if not FIXTURES.exists():
            self.skipTest(f"fixtures dir missing: {FIXTURES}")
        if not _have_ffprobe():
            self.skipTest("ffprobe not found in PATH (required for MP4 metadata extraction)")

        # Prefer explicit manifest examples (lets us reference local wip samples without committing large binaries).
        manifest_pairs = _discover_manifest_pairs()
        pairs = manifest_pairs + _discover_fixture_pairs()
        if not pairs:
            if MANIFEST.exists():
                self.skipTest(
                    f"No runnable fixture pairs found. "
                    f"Either add *.mp4+*.png under {FIXTURES} or ensure paths in {MANIFEST} exist locally."
                )
            self.skipTest(f"No fixture pairs found under {FIXTURES} (add *.mp4 + matching *.png)")

        # Copy fixtures into a temp folder so the integration run can write sidecars.
        with tempfile.TemporaryDirectory(prefix="comfy_roundtrip_") as td:
            tmp = Path(td)

            # Flatten into tmp (keep unique names).
            for mp4, png in pairs:
                shutil.copy2(mp4, tmp / mp4.name)
                shutil.copy2(png, tmp / png.name)

            # 1) Generate sidecars
            proc_wip = (SCRIPTS_DIR / "process_wip_dir.py").resolve()
            if not proc_wip.exists():
                self.skipTest(f"process_wip_dir.py not found under scripts dir: {SCRIPTS_DIR}")
            proc = subprocess.run(
                [_python(), str(proc_wip), str(tmp)],
                cwd=str(ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(
                proc.returncode,
                0,
                msg=f"process_wip_dir.py failed\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}",
            )

            # 2) Verify roundtrip correctness
            proc_check = (SCRIPTS_DIR / "check_roundtrip_dir.py").resolve()
            if not proc_check.exists():
                self.skipTest(f"check_roundtrip_dir.py not found under scripts dir: {SCRIPTS_DIR}")
            proc2 = subprocess.run(
                [_python(), str(proc_check), str(tmp)],
                cwd=str(ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(
                proc2.returncode,
                0,
                msg=f"check_roundtrip_dir.py failed\nstdout:\n{proc2.stdout}\nstderr:\n{proc2.stderr}",
            )


if __name__ == "__main__":
    unittest.main()

