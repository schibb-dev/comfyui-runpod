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

function _readLiteralString(v: unknown): string | null {
  if (typeof v === "string") return v.trim().toLowerCase();
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

/** Ops apply in order: start from mxSlider native scalar, each op maps x -> x'. */
export type DurationChainOp =
  | { t: "float_mul"; v: number }
  | { t: "float_add"; v: number }
  | { t: "int_cast_trunc" }
  | { t: "int_add"; v: number }
  | { t: "int_mul"; v: number }
  | { t: "int_div"; v: number };

function _forwardChain(x: number, ops: readonly DurationChainOp[]): number {
  let v = x;
  for (const op of ops) {
    switch (op.t) {
      case "float_mul":
        v *= op.v;
        break;
      case "float_add":
        v += op.v;
        break;
      case "int_cast_trunc":
        v = Math.trunc(v);
        break;
      case "int_add":
        v += op.v;
        break;
      case "int_mul":
        v *= op.v;
        break;
      case "int_div":
        v /= op.v;
        break;
      default:
        break;
    }
  }
  return v;
}

function _normOp(s: string | null): string {
  return (s ?? "").trim().toLowerCase();
}

/** easy mathFloat / easy mathInt: one linked side, other literal number, small op set. */
function _parseEasyMathBin(
  ins: Record<string, unknown>,
  floatMode: boolean,
): { op: string; linkedKey: "a" | "b"; edge: [string, number]; lit: number } | null {
  const op = _normOp(_readLiteralString(ins.operation) ?? _readLiteralString((ins as { op?: unknown }).op));
  if (!op) return null;
  const a = ins.a;
  const b = ins.b;
  const aEdge = _isComfyEdgeRef(a);
  const bEdge = _isComfyEdgeRef(b);
  if (aEdge === bEdge) return null;
  const linkedKey: "a" | "b" = aEdge ? "a" : "b";
  const edge = (aEdge ? a : b) as [string, number];
  const litRaw = aEdge ? b : a;
  const lit = _readLiteralNumber(litRaw);
  if (lit == null || !Number.isFinite(lit)) return null;
  const allowed = floatMode
    ? ["add", "subtract", "multiply", "divide"]
    : ["add", "subtract", "multiply", "divide"];
  if (!allowed.includes(op)) return null;
  if (op === "divide" && linkedKey !== "a") return null;
  return { op, linkedKey, edge, lit };
}

function _convertAnythingLinkedRef(ins: Record<string, unknown>): [string, number] | null {
  for (const k of ["*", "anything", "input"]) {
    if (!(k in ins)) continue;
    const v = ins[k];
    if (_isComfyEdgeRef(v)) return v;
  }
  for (const v of Object.values(ins)) {
    if (_isComfyEdgeRef(v)) return v;
  }
  return null;
}

function _convertAnythingOutputType(ins: Record<string, unknown>): string | null {
  const v = ins.output_type ?? ins.outputType;
  return _readLiteralString(v);
}

export type PeeledLinearChainSurface = {
  kind: "peeled_linear_chain";
  wanNodeId: string;
  sliderNodeId: string;
  mxSliderUseFloat: boolean;
  opsFromSlider: DurationChainOp[];
};

export type DurationControlSurface =
  | {
      kind: "mx_slider_frames";
      wanNodeId: string;
      sliderNodeId: string;
      usesFloat: boolean;
    }
  | {
      kind: "literal_wan_length";
      wanNodeId: string;
    }
  | PeeledLinearChainSurface
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

type PeelOk = { sliderNodeId: string; mxSliderUseFloat: boolean; opsFromSlider: DurationChainOp[] };

function _tryPeelLinearChain(prompt: ComfyPromptMap, startNodeId: string, visited: Set<string>): PeelOk | null {
  if (visited.has(startNodeId)) return null;
  visited.add(startNodeId);
  const ct = _classType(prompt, startNodeId);
  const ins = _nodeInputs(prompt, startNodeId);
  if (!ins) return null;

  if (ct === "mxSlider" || ct === "mxSlider2D") {
    const { useFloat } = _mxSliderWriteKeys(ins);
    return { sliderNodeId: startNodeId, mxSliderUseFloat: useFloat, opsFromSlider: [] };
  }

  if (ct === "easy convertAnything") {
    const outType = _convertAnythingOutputType(ins);
    if (outType !== "int") return null;
    const ref = _convertAnythingLinkedRef(ins);
    if (!ref) return null;
    const inner = _tryPeelLinearChain(prompt, String(ref[0]), visited);
    if (!inner) return null;
    return {
      sliderNodeId: inner.sliderNodeId,
      mxSliderUseFloat: inner.mxSliderUseFloat,
      opsFromSlider: [...inner.opsFromSlider, { t: "int_cast_trunc" }],
    };
  }

  if (ct === "easy mathFloat") {
    const bin = _parseEasyMathBin(ins, true);
    if (!bin) return null;
    const inner = _tryPeelLinearChain(prompt, String(bin.edge[0]), visited);
    if (!inner) return null;
    const { op, lit } = bin;
    const tail: DurationChainOp[] =
      op === "multiply"
        ? [{ t: "float_mul", v: lit }]
        : op === "add"
          ? [{ t: "float_add", v: lit }]
          : op === "subtract"
            ? bin.linkedKey === "a"
              ? [{ t: "float_add", v: -lit }]
              : [{ t: "float_mul", v: -1 }, { t: "float_add", v: lit }]
            : op === "divide" && bin.linkedKey === "a" && lit !== 0
              ? [{ t: "float_mul", v: 1 / lit }]
              : [];
    if (!tail.length) return null;
    return {
      sliderNodeId: inner.sliderNodeId,
      mxSliderUseFloat: inner.mxSliderUseFloat,
      opsFromSlider: [...inner.opsFromSlider, ...tail],
    };
  }

  if (ct === "easy mathInt") {
    const bin = _parseEasyMathBin(ins, false);
    if (!bin) return null;
    const inner = _tryPeelLinearChain(prompt, String(bin.edge[0]), visited);
    if (!inner) return null;
    const { op, lit, linkedKey } = bin;
    const tail: DurationChainOp[] =
      op === "add"
        ? [{ t: "int_add", v: lit }]
        : op === "subtract" && linkedKey === "a"
          ? [{ t: "int_add", v: -lit }]
          : op === "subtract" && linkedKey === "b"
            ? [{ t: "int_mul", v: -1 }, { t: "int_add", v: lit }]
            : op === "multiply"
              ? [{ t: "int_mul", v: lit }]
              : op === "divide" && linkedKey === "a" && lit !== 0
                ? [{ t: "int_div", v: lit }]
                : [];
    if (!tail.length) return null;
    return {
      sliderNodeId: inner.sliderNodeId,
      mxSliderUseFloat: inner.mxSliderUseFloat,
      opsFromSlider: [...inner.opsFromSlider, ...tail],
    };
  }

  return null;
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

/**
 * Resolve how `WanImageToVideo.length` is driven: literal on Wan, int `mxSlider` upstream,
 * peeled linear chain (easy mathInt / mathFloat / convertAnything) to mxSlider, or unsupported.
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

    const peeled = _tryPeelLinearChain(prompt, srcId, new Set<string>());
    if (peeled && peeled.opsFromSlider.length > 0) {
      return {
        surface: {
          kind: "peeled_linear_chain",
          wanNodeId: wanId,
          sliderNodeId: peeled.sliderNodeId,
          mxSliderUseFloat: peeled.mxSliderUseFloat,
          opsFromSlider: peeled.opsFromSlider,
        },
        nativeLabel: `control · node ${peeled.sliderNodeId} · mxSlider → chain → Wan ${wanId} · length`,
      };
    }

    return {
      surface: {
        kind: "unsupported",
        wanNodeId: wanId,
        reason: `length is linked to ${srcCt} node ${srcId}; only literal Wan length, int mxSlider, or a supported easy math peel is handled in Quick Edits.`,
      },
      nativeLabel: `node ${wanId} · WanImageToVideo · length → ${srcCt} ${srcId}`,
    };
  }
  return null;
}

function _readSliderNative(prompt: ComfyPromptMap, sliderNodeId: string, useFloat: boolean): number | null {
  const ins = _nodeInputs(prompt, sliderNodeId);
  if (!ins) return null;
  const v = _readMxSliderScalar(ins);
  return v != null && Number.isFinite(v) ? v : null;
}

/** Effective value at Wan.length implied by the chain (not clamped to Wan widget range). */
export function forwardDurationChainToWanScalar(prompt: ComfyPromptMap, surface: DurationControlSurface): number | null {
  if (surface.kind !== "peeled_linear_chain") return null;
  const s = _readSliderNative(prompt, surface.sliderNodeId, surface.mxSliderUseFloat);
  if (s == null) return null;
  return _forwardChain(s, surface.opsFromSlider);
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
  if (surface.kind === "peeled_linear_chain") {
    const v = _readSliderNative(prompt, surface.sliderNodeId, surface.mxSliderUseFloat);
    return v != null && Number.isFinite(v) ? (surface.mxSliderUseFloat ? v : Math.round(v)) : null;
  }
  return null;
}

/** Approximate Wan-side length in frames after the chain (rounded); for display / seconds hint. */
export function readDurationApproxWanFrames(prompt: ComfyPromptMap, surface: DurationControlSurface): number | null {
  if (surface.kind === "peeled_linear_chain") {
    const w = forwardDurationChainToWanScalar(prompt, surface);
    if (w == null || !Number.isFinite(w)) return null;
    return _clampFrames(Math.round(w));
  }
  return readDurationNativeFrames(prompt, surface);
}

function _writeMxSliderPair(
  prompt: ComfyPromptMap,
  sliderNodeId: string,
  value: number,
  useFloat: boolean,
  setPromptInput: (nodeId: string, inputKey: string, value: unknown, meta?: SetPromptInputMeta) => void,
  meta: SetPromptInputMeta,
): void {
  const ins = _nodeInputs(prompt, sliderNodeId);
  if (!ins) return;
  if (useFloat) {
    const v = Number.isFinite(value) ? value : 0;
    setPromptInput(sliderNodeId, "Xf", v, meta);
    setPromptInput(sliderNodeId, "Xi", Math.round(v), { ...meta, recordHistory: false });
    return;
  }
  const r = Math.round(value);
  setPromptInput(sliderNodeId, "Xi", r, meta);
  setPromptInput(sliderNodeId, "Xf", r, { ...meta, recordHistory: false });
}

function _solveSliderForTargetWan(surface: PeeledLinearChainSurface, targetWanFrames: number): number | null {
  const target = _clampFrames(targetWanFrames);
  let bestS = 0;
  let bestErr = Number.POSITIVE_INFINITY;
  if (!surface.mxSliderUseFloat) {
    for (let s = DISCOVERY_DURATION_FRAMES_MIN; s <= DISCOVERY_DURATION_FRAMES_MAX; s++) {
      const w = Math.round(_forwardChain(s, surface.opsFromSlider));
      const err = Math.abs(w - target);
      if (err < bestErr) {
        bestErr = err;
        bestS = s;
      }
      if (err === 0) break;
    }
    return bestS;
  }
  for (let x = 0; x <= 512; x += 0.05) {
    const w = Math.round(_forwardChain(x, surface.opsFromSlider));
    const err = Math.abs(w - target);
    if (err < bestErr) {
      bestErr = err;
      bestS = x;
    }
    if (err === 0) break;
  }
  return Number.isFinite(bestS) ? bestS : null;
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
    _writeMxSliderPair(prompt, surface.sliderNodeId, f, false, setPromptInput, meta);
    return;
  }
  if (surface.kind === "peeled_linear_chain") {
    const sliderVal = _solveSliderForTargetWan(surface, f);
    if (sliderVal == null) return;
    _writeMxSliderPair(prompt, surface.sliderNodeId, sliderVal, surface.mxSliderUseFloat, setPromptInput, meta);
  }
}

/**
 * Edit mxSlider only (no chain inverse). Used when encode FPS is missing so we do not imply output-time meaning.
 */
export function writeDurationSliderNative(
  prompt: ComfyPromptMap,
  surface: DurationControlSurface,
  nativeValue: number,
  setPromptInput: (nodeId: string, inputKey: string, value: unknown, meta?: SetPromptInputMeta) => void,
  meta: SetPromptInputMeta,
): void {
  if (surface.kind !== "peeled_linear_chain") return;
  _writeMxSliderPair(prompt, surface.sliderNodeId, nativeValue, surface.mxSliderUseFloat, setPromptInput, meta);
}

export function framesToSeconds(frames: number, fps: number): number {
  if (!Number.isFinite(fps) || fps <= 0) return NaN;
  return frames / fps;
}

export function secondsToFrames(seconds: number, fps: number): number {
  if (!Number.isFinite(fps) || fps <= 0) return DISCOVERY_DURATION_FRAMES_MIN;
  return _clampFrames(Math.round(seconds * fps));
}
