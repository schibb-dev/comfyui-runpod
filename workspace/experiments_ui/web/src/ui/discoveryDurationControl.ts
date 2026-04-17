/**
 * Discovery Quick Edits — generation duration: resolve where the graph stores length
 * (native read/write in API prompt space). No React; trim / library UI is unrelated.
 */
import type { SetPromptInputMeta } from "./usePromptDraftHistory";

export type ComfyPromptMap = Record<string, unknown>;

export const DISCOVERY_DURATION_FRAMES_MIN = 1;
export const DISCOVERY_DURATION_FRAMES_MAX = 8192;

function _sortNodeIds(ids: string[]): string[] {
  return [...ids].sort((a, b) => {
    const na = Number.parseInt(a, 10);
    const nb = Number.parseInt(b, 10);
    if (String(na) === a && String(nb) === b && !Number.isNaN(na) && !Number.isNaN(nb)) return na - nb;
    return a.localeCompare(b);
  });
}

function _nodeInputs(prompt: ComfyPromptMap, nodeId: string): Record<string, unknown> | null {
  const node = prompt[nodeId];
  if (typeof node !== "object" || node === null) return null;
  const ins = (node as { inputs?: unknown }).inputs;
  if (typeof ins !== "object" || ins === null) return null;
  return ins as Record<string, unknown>;
}

function _classType(prompt: ComfyPromptMap, nodeId: string): string {
  const node = prompt[nodeId];
  if (typeof node !== "object" || node === null) return "";
  const ct = (node as { class_type?: unknown }).class_type;
  return typeof ct === "string" ? ct : "";
}

function _isComfyEdgeRef(v: unknown): v is [string, number] {
  return Array.isArray(v) && v.length >= 2 && typeof v[0] === "string" && typeof v[1] === "number";
}

function _readLiteralNumber(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const t = v.trim();
    if (!t.length) return null;
    const n = Number(t);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function _readMxSliderScalar(ins: Record<string, unknown>): number | null {
  const floatMode = _readLiteralNumber(ins.isfloatX);
  const useFloat = floatMode != null && floatMode > 0;
  return useFloat ? _readLiteralNumber(ins.Xf) : _readLiteralNumber(ins.Xi);
}

function _mxSliderWriteKeys(ins: Record<string, unknown>): { intKey: "Xi"; floatKey: "Xf"; useFloat: boolean } {
  const floatMode = _readLiteralNumber(ins.isfloatX);
  const useFloat = floatMode != null && floatMode > 0;
  return { intKey: "Xi", floatKey: "Xf", useFloat };
}

function _clampFrames(n: number): number {
  const r = Math.round(n);
  return Math.min(DISCOVERY_DURATION_FRAMES_MAX, Math.max(DISCOVERY_DURATION_FRAMES_MIN, r));
}

/** First literal `frame_rate` on a VHS_VideoCombine (encode FPS), for seconds ↔ frames display only. */
export function discoveryLiteralFpsFromVhsCombine(prompt: ComfyPromptMap): number | null {
  for (const nodeId of _sortNodeIds(Object.keys(prompt))) {
    const ct = _classType(prompt, nodeId);
    if (!ct.includes("VHS_VideoCombine")) continue;
    const ins = _nodeInputs(prompt, nodeId);
    if (!ins) continue;
    const v = ins.frame_rate;
    if (_isComfyEdgeRef(v)) continue;
    const lit = _readLiteralNumber(v);
    if (lit == null || !Number.isFinite(lit) || lit <= 0) continue;
    return lit;
  }
  return null;
}

export type DurationControlSurface =
  | {
      kind: "mx_slider_frames";
      /** Wan node (for labels / config notes). */
      wanNodeId: string;
      sliderNodeId: string;
      /** mxSlider int mode only for this path. */
      usesFloat: boolean;
    }
  | {
      kind: "literal_wan_length";
      wanNodeId: string;
    }
  | {
      kind: "unsupported";
      wanNodeId: string;
      reason: string;
    };

export type DurationControlResolution = {
  surface: DurationControlSurface;
  /** Human-readable where native writes go. */
  nativeLabel: string;
};

/**
 * Resolve how `WanImageToVideo.length` is driven: literal on Wan, int `mxSlider` upstream, or unsupported.
 */
export function resolveDurationControlSurface(prompt: ComfyPromptMap): DurationControlResolution | null {
  for (const wanId of _sortNodeIds(Object.keys(prompt))) {
    if (_classType(prompt, wanId) !== "WanImageToVideo") continue;
    const ins = _nodeInputs(prompt, wanId);
    if (!ins) continue;
    const len = ins.length;
    if (!_isComfyEdgeRef(len)) {
      const lit = _readLiteralNumber(len);
      if (lit == null || !Number.isFinite(lit)) continue;
      return {
        surface: { kind: "literal_wan_length", wanNodeId: wanId },
        nativeLabel: `frames · node ${wanId} · WanImageToVideo · inputs.length`,
      };
    }
    const srcId = String(len[0]);
    const srcIns = _nodeInputs(prompt, srcId);
    const srcCt = _classType(prompt, srcId);
    if (!srcIns) {
      return {
        surface: { kind: "unsupported", wanNodeId: wanId, reason: `length is linked to ${srcId} but inputs are missing.` },
        nativeLabel: `node ${wanId} · WanImageToVideo · length (linked)`,
      };
    }
    if (srcCt === "mxSlider" || srcCt === "mxSlider2D") {
      const keys = _mxSliderWriteKeys(srcIns);
      if (keys.useFloat) {
        return {
          surface: {
            kind: "unsupported",
            wanNodeId: wanId,
            reason: `length is driven by float mxSlider node ${srcId}; Quick Edit supports int frame counts only.`,
          },
          nativeLabel: `node ${wanId} · WanImageToVideo · length → ${srcCt} ${srcId} (float)`,
        };
      }
      return {
        surface: { kind: "mx_slider_frames", wanNodeId: wanId, sliderNodeId: srcId, usesFloat: false },
        nativeLabel: `frames · node ${srcId} · ${srcCt} · Xi/Xf (feeds Wan ${wanId} · length)`,
      };
    }
    return {
      surface: {
        kind: "unsupported",
        wanNodeId: wanId,
        reason: `length is linked to ${srcCt} node ${srcId}; only literal Wan length or int mxSlider is supported in Quick Edits.`,
      },
      nativeLabel: `node ${wanId} · WanImageToVideo · length → ${srcCt} ${srcId}`,
    };
  }
  return null;
}

export function readDurationNativeFrames(prompt: ComfyPromptMap, surface: DurationControlSurface): number | null {
  if (surface.kind === "literal_wan_length") {
    const ins = _nodeInputs(prompt, surface.wanNodeId);
    if (!ins) return null;
    const lit = _readLiteralNumber(ins.length);
    return lit != null && Number.isFinite(lit) ? _clampFrames(lit) : null;
  }
  if (surface.kind === "mx_slider_frames") {
    const ins = _nodeInputs(prompt, surface.sliderNodeId);
    if (!ins) return null;
    const v = _readMxSliderScalar(ins);
    return v != null && Number.isFinite(v) ? _clampFrames(v) : null;
  }
  return null;
}

export function writeDurationNativeFrames(
  prompt: ComfyPromptMap,
  surface: DurationControlSurface,
  frames: number,
  setPromptInput: (nodeId: string, inputKey: string, value: unknown, meta?: SetPromptInputMeta) => void,
  meta: SetPromptInputMeta,
): void {
  const f = _clampFrames(frames);
  if (surface.kind === "literal_wan_length") {
    setPromptInput(surface.wanNodeId, "length", f, meta);
    return;
  }
  if (surface.kind === "mx_slider_frames") {
    const ins = _nodeInputs(prompt, surface.sliderNodeId);
    if (!ins) return;
    const { useFloat } = _mxSliderWriteKeys(ins);
    if (useFloat) return;
    /** One undo step: history records the snapshot before Xi; Xf is the paired write on the same logical edit. */
    setPromptInput(surface.sliderNodeId, "Xi", f, meta);
    setPromptInput(surface.sliderNodeId, "Xf", f, { ...meta, recordHistory: false });
  }
}

export function framesToSeconds(frames: number, fps: number): number {
  if (!Number.isFinite(fps) || fps <= 0) return NaN;
  return frames / fps;
}

export function secondsToFrames(seconds: number, fps: number): number {
  if (!Number.isFinite(fps) || fps <= 0) return DISCOVERY_DURATION_FRAMES_MIN;
  return _clampFrames(Math.round(seconds * fps));
}
