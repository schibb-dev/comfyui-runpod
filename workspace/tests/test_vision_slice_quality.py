#!/usr/bin/env python3
"""Tests for classical vision-slice quality metrics."""

from __future__ import annotations

import math
import unittest

import support  # noqa: F401

from vision_slice_quality_metrics import (
    Gray,
    artifacting_score,
    convergence_score,
    rollup_quality_rows,
    score_frame,
    sharpness_score,
)


def _flat(v: float, h: int = 64, w: int = 64) -> Gray:
    return [[float(v) for _ in range(w)] for _ in range(h)]


def _checker(h: int = 64, w: int = 64, period: int = 2) -> Gray:
    out: Gray = []
    for y in range(h):
        row = []
        for x in range(w):
            row.append(1.0 if ((x // period) + (y // period)) % 2 == 0 else 0.0)
        out.append(row)
    return out


def _blocky(h: int = 64, w: int = 64) -> Gray:
    """Large constant 8×8 blocks — strong block boundaries."""
    out: Gray = []
    for y in range(h):
        row = []
        for x in range(w):
            bx, by = x // 8, y // 8
            row.append(0.9 if (bx + by) % 2 == 0 else 0.1)
        out.append(row)
    return out


def _blur(src: Gray, passes: int = 4) -> Gray:
    cur = src
    for _ in range(passes):
        h, w = len(cur), len(cur[0])
        nxt: Gray = [[0.0] * w for _ in range(h)]
        for y in range(h):
            for x in range(w):
                s = 0.0
                n = 0
                for dy in (-1, 0, 1):
                    yy = min(h - 1, max(0, y + dy))
                    for dx in (-1, 0, 1):
                        xx = min(w - 1, max(0, x + dx))
                        s += cur[yy][xx]
                        n += 1
                nxt[y][x] = s / n
        cur = nxt
    return cur


class VisionSliceQualityMetricsTests(unittest.TestCase):
    def test_sharp_beats_blurry(self) -> None:
        sharp = _checker(period=1)
        blurry = _blur(sharp, passes=6)
        self.assertGreater(sharpness_score(sharp), sharpness_score(blurry))

    def test_flat_low_sharpness(self) -> None:
        self.assertLess(sharpness_score(_flat(0.5)), 0.15)

    def test_convergence_identical_high(self) -> None:
        a = _checker()
        b = [row[:] for row in a]
        c = convergence_score(a, b)
        assert c is not None
        self.assertGreater(c, 0.99)

    def test_convergence_first_none(self) -> None:
        self.assertIsNone(convergence_score(None, _flat(0.5)))

    def test_convergence_different_lower(self) -> None:
        a = _flat(0.2)
        b = _flat(0.8)
        c = convergence_score(a, b)
        assert c is not None
        self.assertLess(c, 0.5)

    def test_blocky_artifacting_high(self) -> None:
        smooth = _blur(_checker(period=4), passes=3)
        blocky = _blocky()
        self.assertGreater(artifacting_score(blocky), artifacting_score(smooth))

    def test_score_frame_keys(self) -> None:
        q = score_frame(_checker(), prev=_flat(0.5))
        for k in ("sharpness", "convergence", "artifacting", "exposure", "contrast"):
            self.assertIn(k, q)
        self.assertIsInstance(q["sharpness"], float)
        self.assertTrue(0.0 <= q["sharpness"] <= 1.0)

    def test_rollup(self) -> None:
        rows = [
            {"quality": score_frame(_checker())},
            {"quality": score_frame(_flat(0.5), prev=_checker())},
        ]
        rolled = rollup_quality_rows(rows)
        self.assertEqual(rolled["frame_count"], 2)
        self.assertIn("sharpness", rolled)
        self.assertTrue(math.isfinite(rolled["sharpness"]["mean"]))


if __name__ == "__main__":
    unittest.main()
