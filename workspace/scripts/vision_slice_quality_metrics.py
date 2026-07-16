#!/usr/bin/env python3
"""
Classical (non-learned) frame quality metrics for Vision V1 slices.

Pure functions over grayscale float matrices in [0, 1]. No OpenCV / no VQA models.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

Gray = List[List[float]]


def _clamp01(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return float(x)


def _mean(xs: Sequence[float]) -> float:
    if not xs:
        return 0.0
    return float(sum(xs)) / float(len(xs))


def _percentile(xs: Sequence[float], p: float) -> Optional[float]:
    if not xs:
        return None
    ys = sorted(float(v) for v in xs)
    if len(ys) == 1:
        return ys[0]
    p = max(0.0, min(100.0, float(p)))
    k = (len(ys) - 1) * (p / 100.0)
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return ys[lo]
    t = k - lo
    return ys[lo] * (1.0 - t) + ys[hi] * t


def shape(gray: Gray) -> Tuple[int, int]:
    h = len(gray)
    w = len(gray[0]) if h else 0
    return h, w


def laplacian_variance(gray: Gray) -> float:
    """Variance of a discrete Laplacian (higher = sharper)."""
    h, w = shape(gray)
    if h < 3 or w < 3:
        return 0.0
    vals: List[float] = []
    for y in range(1, h - 1):
        row = gray[y]
        yp = gray[y - 1]
        yn = gray[y + 1]
        for x in range(1, w - 1):
            v = row[x + 1] + row[x - 1] + yn[x] + yp[x] - 4.0 * row[x]
            vals.append(v)
    if not vals:
        return 0.0
    m = _mean(vals)
    return _mean([(v - m) * (v - m) for v in vals])


def sharpness_score(gray: Gray) -> float:
    """
    Log-scaled Laplacian variance mapped roughly into [0, 1].

    Empirically: soft/blurry frames ~0.01–0.05 var; crisp gen frames often 0.1–2+.
    """
    var = laplacian_variance(gray)
    # log1p soft-compress; 4.0 chosen so var≈e^4-1≈53 → ~1.0
    return _clamp01(math.log1p(max(0.0, var) * 50.0) / 4.0)


def exposure_score(gray: Gray) -> float:
    h, w = shape(gray)
    if h == 0 or w == 0:
        return 0.0
    total = 0.0
    n = 0
    for row in gray:
        for v in row:
            total += v
            n += 1
    return _clamp01(total / n) if n else 0.0


def contrast_score(gray: Gray) -> float:
    h, w = shape(gray)
    if h == 0 or w == 0:
        return 0.0
    vals: List[float] = []
    for row in gray:
        vals.extend(row)
    m = _mean(vals)
    var = _mean([(v - m) * (v - m) for v in vals])
    # Std of [0,1] luminance; scale so typical photos sit mid-range.
    return _clamp01(math.sqrt(var) * 2.5)


def mean_abs_diff(a: Gray, b: Gray) -> float:
    """Mean absolute pixel difference in [0, 1]."""
    ha, wa = shape(a)
    hb, wb = shape(b)
    h, w = min(ha, hb), min(wa, wb)
    if h == 0 or w == 0:
        return 1.0
    total = 0.0
    n = 0
    for y in range(h):
        ra, rb = a[y], b[y]
        for x in range(w):
            total += abs(ra[x] - rb[x])
            n += 1
    return total / n if n else 1.0


def convergence_score(prev: Optional[Gray], cur: Gray) -> Optional[float]:
    """
    Temporal stability vs previous sample: 1 - mean_abs_diff.

    Higher = more stable / “held together”. First frame returns None.
    """
    if prev is None:
        return None
    return _clamp01(1.0 - mean_abs_diff(prev, cur))


def artifacting_score(gray: Gray) -> float:
    """
    Blockiness + high-frequency residual proxy. Higher = more artifacted.

    - 8×8 block-boundary energy (MPEG/JPEG blocking)
    - Residual after crude 3×3 box blur (ringing / mosquito noise)
    """
    h, w = shape(gray)
    if h < 16 or w < 16:
        return 0.0

    # Block boundary energy (every 8th row/col interior difference).
    bound = 0.0
    n_b = 0
    for y in range(h):
        row = gray[y]
        for x in range(8, w, 8):
            bound += abs(row[x] - row[x - 1])
            n_b += 1
    for y in range(8, h, 8):
        ra, rb = gray[y], gray[y - 1]
        for x in range(w):
            bound += abs(ra[x] - rb[x])
            n_b += 1
    block = (bound / n_b) if n_b else 0.0

    # High-frequency residual vs 3×3 box.
    hf = 0.0
    n_hf = 0
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            s = 0.0
            for dy in (-1, 0, 1):
                row = gray[y + dy]
                for dx in (-1, 0, 1):
                    s += row[x + dx]
            blur = s / 9.0
            hf += abs(gray[y][x] - blur)
            n_hf += 1
    residual = (hf / n_hf) if n_hf else 0.0

    # Mix; scale into ~[0,1] for typical gen/compress ranges.
    return _clamp01(block * 4.0 + residual * 6.0)


def score_frame(gray: Gray, *, prev: Optional[Gray] = None) -> Dict[str, Any]:
    """Return the v1 quality dict for one grayscale frame."""
    conv = convergence_score(prev, gray)
    out: Dict[str, Any] = {
        "sharpness": round(sharpness_score(gray), 4),
        "artifacting": round(artifacting_score(gray), 4),
        "exposure": round(exposure_score(gray), 4),
        "contrast": round(contrast_score(gray), 4),
    }
    if conv is None:
        out["convergence"] = None
    else:
        out["convergence"] = round(conv, 4)
    return out


def rollup_metric(values: Sequence[Optional[float]]) -> Optional[Dict[str, float]]:
    xs = [float(v) for v in values if isinstance(v, (int, float))]
    if not xs:
        return None
    p10 = _percentile(xs, 10)
    p90 = _percentile(xs, 90)
    return {
        "mean": round(_mean(xs), 4),
        "p10": round(p10, 4) if p10 is not None else round(_mean(xs), 4),
        "p90": round(p90, 4) if p90 is not None else round(_mean(xs), 4),
        "n": float(len(xs)),
    }


def rollup_quality_rows(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Asset-level mean + p10/p90 for each metric key.

    Each row may be a flat quality dict or ``{"quality": {...}}``.
    """
    keys = ("sharpness", "convergence", "artifacting", "exposure", "contrast")
    out: Dict[str, Any] = {"frame_count": len(rows)}
    for k in keys:
        vals: List[Optional[float]] = []
        for r in rows:
            q = r.get("quality") if isinstance(r.get("quality"), dict) else r
            if isinstance(q, dict):
                v = q.get(k)
                vals.append(float(v) if isinstance(v, (int, float)) else None)
            else:
                vals.append(None)
        rolled = rollup_metric(vals)
        if rolled is not None:
            out[k] = rolled
    return out
