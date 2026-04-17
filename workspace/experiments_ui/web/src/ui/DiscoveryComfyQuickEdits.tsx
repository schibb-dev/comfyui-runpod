import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { DiscoveryDurationTranslatingCard } from "./DiscoveryDurationTranslatingCard";
import { SliderNumRow } from "./DiscoverySliderNumRow";
import {
  discoveryLiteralFpsFromVhsCombine,
  type DurationControlResolution,
  resolveDurationControlSurface,
} from "./discoveryDurationControl";
import type { SetPromptInputMeta } from "./usePromptDraftHistory";

/**
 * Maintenance bookmark: this file mixes prompt-graph resolvers and UI and is already large. On the next
 * substantial check-in here, plan to extract shared Comfy prompt primitives (dedupe with duration control)
 * and split resolver domains (sampler, seed, VHS, CLIP, LoRA) into sibling modules instead of growing further.
 */
const QH = { recordHistory: true } satisfies SetPromptInputMeta;

/**
 * Comfy core `KSampler` / `KSamplerAdvanced` INT `steps` widget allows min 1, max 10000, step 1.
 * Discovery Quick Edits caps the Steps slider at 64; edit the raw prompt elsewhere if you truly need more.
 */
const COMFY_KSAMPLER_STEPS_MIN = 1;
const DISCOVERY_QUICK_EDIT_STEPS_MAX = 64;
const COMFY_KSAMPLER_STEPS_STEP = 1;

/** Comfy API prompt: node id → { class_type, inputs } */
export type ComfyPromptMap = Record<string, unknown>;

/**
 * VideoHelperSuite (VHS) quick-edit contract — extend here when adding Discovery controls.
 * All writes go through `setPromptInput(nodeId, inputKey, value)` on API prompt `inputs`.
 *
 * **Loaders** (`VHS_LoadVideoPath`, `VHS_LoadVideo`, `VHS_LoadVideoFFmpeg`, …): common scalar keys
 * include `skip_first_frames`, `frame_load_cap`, `force_rate` (0 = native rate on load).
 * Path / combo `video` is left for programmatic runners (e.g. `tune_experiment.py`).
 *
 * **Encode / save** (`VHS_VideoCombine`): `filename_prefix`, `frame_rate` (encode FPS; not `force_rate`),
 * `save_output`, `save_metadata`. Other booleans (`pingpong`, `trim_to_audio`, …) can be added later
 * using the same pattern without changing existing keys.
 */
export const VHS_QUICK_EDIT_INPUT_KEYS = {
  loader: ["skip_first_frames", "frame_load_cap", "force_rate"] as const,
  combine: ["filename_prefix", "frame_rate", "save_output", "save_metadata"] as const,
} as const;

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

/** Florence-based prompt grooming is deprecated; do not resolve text through these nodes. */
function _isFlorenceFamilyClass(ct: string): boolean {
  return /florence/i.test(ct);
}

function _isComfyEdgeRef(v: unknown): v is [string, number] {
  return Array.isArray(v) && v.length >= 2 && typeof v[0] === "string" && typeof v[1] === "number";
}

/** Literal widget numbers in API prompts are usually JSON numbers; some exports use strings. */
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

function _readPromptBool(v: unknown): boolean | null {
  if (typeof v === "boolean") return v;
  if (v === 1 || v === 0) return Boolean(v);
  if (typeof v === "string") {
    const t = v.trim().toLowerCase();
    if (t === "true" || t === "1") return true;
    if (t === "false" || t === "0") return false;
  }
  return null;
}

/** ComfyUI-mxToolkit `mxSlider` / `mxSlider2D`: value is `Xi` or `Xf` depending on `isfloatX`. */
function _readMxSliderScalar(ins: Record<string, unknown>): number | null {
  const floatMode = _readLiteralNumber(ins.isfloatX);
  const useFloat = floatMode != null && floatMode > 0;
  return useFloat ? _readLiteralNumber(ins.Xf) : _readLiteralNumber(ins.Xi);
}

function _mxSliderWriteKey(ins: Record<string, unknown>): "Xi" | "Xf" {
  const floatMode = _readLiteralNumber(ins.isfloatX);
  return floatMode != null && floatMode > 0 ? "Xf" : "Xi";
}

/** Nodes that reshape `sigmas` but take the real step budget from further upstream. */
const _SIGMAS_CHAIN_PASSTHROUGH: ReadonlySet<string> = new Set([
  "FlipSigmas",
  "SplitSigmas",
  "SplitSigmasDenoise",
  "SetFirstSigma",
  "ExtendIntermediateSigmas",
]);

function _resolveSigmasUpstreamStepsAndTarget(
  prompt: ComfyPromptMap,
  value: unknown,
  visited: Set<string>,
): { value: number; target: { nodeId: string; inputKey: string } } | null {
  if (!_isComfyEdgeRef(value)) return null;
  const nid = String(value[0]);
  if (visited.has(nid)) return null;
  visited.add(nid);
  const ins = _nodeInputs(prompt, nid);
  if (!ins) return null;
  const ct = _classType(prompt, nid);

  if (ct === "mxSlider" || ct === "mxSlider2D") {
    const v = _readMxSliderScalar(ins);
    if (v == null) return null;
    return { value: Math.round(v), target: { nodeId: nid, inputKey: _mxSliderWriteKey(ins) } };
  }

  if (_SIGMAS_CHAIN_PASSTHROUGH.has(ct)) {
    if (!("sigmas" in ins)) return null;
    return _resolveSigmasUpstreamStepsAndTarget(prompt, ins.sigmas, visited);
  }

  if ("steps" in ins) {
    const stepsV = ins.steps;
    const lit = _readLiteralNumber(stepsV);
    if (lit != null) return { value: Math.round(lit), target: { nodeId: nid, inputKey: "steps" } };
    const sub = _resolveSigmasUpstreamStepsAndTarget(prompt, stepsV, visited);
    if (sub) return sub;
  }

  if ("sigmas" in ins) {
    return _resolveSigmasUpstreamStepsAndTarget(prompt, ins.sigmas, visited);
  }

  return null;
}

function _resolveSigmasChainDenoiseValueAndTarget(
  prompt: ComfyPromptMap,
  value: unknown,
  visited: Set<string>,
): { value: number; target: { nodeId: string; inputKey: string } } | null {
  if (!_isComfyEdgeRef(value)) return null;
  const nid = String(value[0]);
  if (visited.has(nid)) return null;
  visited.add(nid);
  const ins = _nodeInputs(prompt, nid);
  if (!ins) return null;
  const ct = _classType(prompt, nid);

  if (ct === "BasicScheduler" || ct === "SDTurboScheduler") {
    const dEdge = ins.denoise;
    const dLit = _readLiteralNumber(dEdge);
    if (dLit != null) return { value: dLit, target: { nodeId: nid, inputKey: "denoise" } };
    if (_isComfyEdgeRef(dEdge)) {
      const dn = String(dEdge[0]);
      if (!visited.has(dn)) {
        visited.add(dn);
        const dIns = _nodeInputs(prompt, dn);
        const dCt = dIns ? _classType(prompt, dn) : "";
        if ((dCt === "mxSlider" || dCt === "mxSlider2D") && dIns) {
          const v = _readMxSliderScalar(dIns);
          if (v != null) {
            return { value: v, target: { nodeId: dn, inputKey: _mxSliderWriteKey(dIns) } };
          }
        }
      }
    }
    return null;
  }

  if (_SIGMAS_CHAIN_PASSTHROUGH.has(ct) && "sigmas" in ins) {
    return _resolveSigmasChainDenoiseValueAndTarget(prompt, ins.sigmas, visited);
  }

  if ("sigmas" in ins) {
    return _resolveSigmasChainDenoiseValueAndTarget(prompt, ins.sigmas, visited);
  }

  return null;
}

function _resolveGuiderCfgValueAndTarget(
  prompt: ComfyPromptMap,
  guiderRef: unknown,
): { value: number; target: { nodeId: string; inputKey: string } } | null {
  if (!_isComfyEdgeRef(guiderRef)) return null;
  const nid = String(guiderRef[0]);
  const ins = _nodeInputs(prompt, nid);
  if (!ins) return null;
  const ct = _classType(prompt, nid);
  if (ct === "CFGGuider") {
    const v = _resolveLinkedNumberForInput(prompt, ins.cfg, "cfg", 0, new Set());
    if (v == null) return null;
    return { value: v, target: { nodeId: nid, inputKey: "cfg" } };
  }
  if (ct === "DualCFGGuider") {
    const v1 = _resolveLinkedNumberForInput(prompt, ins.cfg_conds, "cfg", 0, new Set());
    if (v1 != null) return { value: v1, target: { nodeId: nid, inputKey: "cfg_conds" } };
    const v2 = _resolveLinkedNumberForInput(prompt, ins.cfg_cond2_negative, "cfg", 0, new Set());
    if (v2 != null) return { value: v2, target: { nodeId: nid, inputKey: "cfg_cond2_negative" } };
    return null;
  }
  return null;
}

function _traceGuiderToClipTextEncodeIds(
  prompt: ComfyPromptMap,
  guiderRef: unknown,
  visited: Set<string>,
): { pos: string | null; neg: string | null } {
  if (!_isComfyEdgeRef(guiderRef)) return { pos: null, neg: null };
  const nid = String(guiderRef[0]);
  if (visited.has(nid)) return { pos: null, neg: null };
  visited.add(nid);
  const ins = _nodeInputs(prompt, nid);
  if (!ins) return { pos: null, neg: null };
  const ct = _classType(prompt, nid);
  if (ct === "CFGGuider") {
    return {
      pos: _traceToClipTextEncodeId(prompt, ins.positive, new Set()),
      neg: _traceToClipTextEncodeId(prompt, ins.negative, new Set()),
    };
  }
  if (ct === "DualCFGGuider") {
    return {
      pos: _traceToClipTextEncodeId(prompt, ins.cond1, new Set()),
      neg: _traceToClipTextEncodeId(prompt, ins.negative, new Set()),
    };
  }
  if (ct === "BasicGuider") {
    return {
      pos: _traceToClipTextEncodeId(prompt, ins.conditioning, new Set()),
      neg: null,
    };
  }
  return { pos: null, neg: null };
}

function _traceSamplerClipIds(prompt: ComfyPromptMap, samplerNodeId: string): { pos: string | null; neg: string | null } {
  const ct = _classType(prompt, samplerNodeId);
  const ins = _nodeInputs(prompt, samplerNodeId);
  if (!ins) return { pos: null, neg: null };
  if (ct === "KSampler" || ct === "KSamplerAdvanced") {
    return {
      pos: _traceToClipTextEncodeId(prompt, ins.positive, new Set()),
      neg: _traceToClipTextEncodeId(prompt, ins.negative, new Set()),
    };
  }
  if (ct === "SamplerCustomAdvanced") {
    return _traceGuiderToClipTextEncodeIds(prompt, ins.guider, new Set());
  }
  if (ct === "SamplerCustom") {
    return {
      pos: _traceToClipTextEncodeId(prompt, ins.positive, new Set()),
      neg: _traceToClipTextEncodeId(prompt, ins.negative, new Set()),
    };
  }
  if (ct === "WanImageToVideo") {
    return {
      pos: _traceToClipTextEncodeId(prompt, ins.positive, new Set()),
      neg: _traceToClipTextEncodeId(prompt, ins.negative, new Set()),
    };
  }
  return { pos: null, neg: null };
}

/**
 * Resolve `steps` or `cfg` when the KSampler input is a literal, a numeric string, or an edge into
 * schedulers / primitives (e.g. BasicScheduler → steps). Skips unrelated ints (seeds) by key order.
 */
function _resolveLinkedNumberForInput(
  prompt: ComfyPromptMap,
  value: unknown,
  semantic: "steps" | "cfg",
  depth: number,
  visited: Set<string>,
): number | null {
  if (depth > 28) return null;
  const lit = _readLiteralNumber(value);
  if (lit != null) return lit;
  if (!_isComfyEdgeRef(value)) return null;
  const nid = String(value[0]);
  if (visited.has(nid)) return null;
  visited.add(nid);
  const ins = _nodeInputs(prompt, nid);
  if (!ins) return null;
  const ct = _classType(prompt, nid);

  if (semantic === "steps" && (ct === "mxSlider" || ct === "mxSlider2D")) {
    return _readMxSliderScalar(ins);
  }

  const stepKeys = ["steps", "sigmas", "amount"] as const;
  const cfgKeys = ["cfg", "guidance_scale", "guidance"] as const;
  const stepPrimitiveKeys = ["value", "Number", "int", "amount", "Xi", "Xf"] as const;
  const cfgPrimitiveKeys = ["value", "Number", "int", "amount"] as const;

  if (semantic === "steps") {
    if (/scheduler|sigmas|step/i.test(ct)) {
      for (const k of stepKeys) {
        if (!(k in ins)) continue;
        const got = _resolveLinkedNumberForInput(prompt, ins[k], "steps", depth + 1, visited);
        if (got != null) return got;
      }
    }
    for (const k of [...stepKeys, ...stepPrimitiveKeys]) {
      if (!(k in ins)) continue;
      const got = _resolveLinkedNumberForInput(prompt, ins[k], "steps", depth + 1, visited);
      if (got != null) return got;
    }
    return null;
  }
  if (/guidance|cfg/i.test(ct)) {
    for (const k of cfgKeys) {
      if (!(k in ins)) continue;
      const got = _resolveLinkedNumberForInput(prompt, ins[k], "cfg", depth + 1, visited);
      if (got != null) return got;
    }
  }
  for (const k of [...cfgKeys, ...cfgPrimitiveKeys]) {
    if (!(k in ins)) continue;
    const got = _resolveLinkedNumberForInput(prompt, ins[k], "cfg", depth + 1, visited);
    if (got != null) return got;
  }
  return null;
}

function _resolveKsamplerCfgSteps(
  prompt: ComfyPromptMap,
  nodeId: string,
): { cfg: number | null; steps: number | null } {
  const ins = _nodeInputs(prompt, nodeId);
  if (!ins) return { cfg: null, steps: null };
  const cfg = _resolveLinkedNumberForInput(prompt, ins.cfg, "cfg", 0, new Set());
  const steps = _resolveLinkedNumberForInput(prompt, ins.steps, "steps", 0, new Set());
  return { cfg, steps };
}

/** Lower = preferred when two sampler nodes tie on BFS depth (sigmas path before legacy KSampler). */
function _primarySamplerTypeRank(ct: string): number | null {
  if (ct === "SamplerCustomAdvanced") return 0;
  if (ct === "SamplerCustom") return 1;
  if (ct === "KSamplerAdvanced") return 2;
  if (ct === "KSampler") return 3;
  return null;
}

/** BFS backward from image sinks; first dequeued visit per node = shortest hop count from any seed. */
function _bfsNearestPrimarySamplerFromSeeds(prompt: ComfyPromptMap, seeds: string[]): string | null {
  type Item = { nid: string; depth: number };
  const q: Item[] = seeds.map((nid) => ({ nid, depth: 0 }));
  const dequeued = new Set<string>();
  let best: { nid: string; depth: number; rank: number } | null = null;
  while (q.length > 0) {
    const { nid, depth } = q.shift()!;
    if (dequeued.has(nid)) continue;
    dequeued.add(nid);
    const ct = _classType(prompt, nid);
    const rank = _primarySamplerTypeRank(ct);
    if (rank != null) {
      if (
        !best ||
        depth < best.depth ||
        (depth === best.depth && rank < best.rank) ||
        (depth === best.depth && rank === best.rank && _sortNodeIds([nid, best.nid])[0] === nid)
      ) {
        best = { nid, depth, rank };
      }
    }
    const ins = _nodeInputs(prompt, nid);
    if (!ins) continue;
    const nd = depth + 1;
    for (const v of Object.values(ins)) {
      if (!_isComfyEdgeRef(v)) continue;
      const next = String(v[0]);
      q.push({ nid: next, depth: nd });
    }
  }
  return best?.nid ?? null;
}

/** Frame/tensor sources feeding SaveImage / PreviewImage / VHS_VideoCombine (for upstream BFS). */
function _collectImageSinkFrameSourceSeeds(prompt: ComfyPromptMap): string[] {
  const seeds: string[] = [];
  for (const nodeId of _sortNodeIds(Object.keys(prompt))) {
    const ct = _classType(prompt, nodeId);
    const ins = _nodeInputs(prompt, nodeId);
    if (!ins) continue;
    const keys: string[] = [];
    if (/SaveImage|PreviewImage|ImageSave$/i.test(ct)) keys.push("images", "image");
    else if (/VideoCombine|VHS_VideoCombine/i.test(ct)) keys.push("images");
    for (const k of keys) {
      if (!(k in ins)) continue;
      const v = ins[k];
      if (_isComfyEdgeRef(v)) seeds.push(String(v[0]));
    }
  }
  return seeds;
}

/** Primary sampler (SamplerCustom* / KSampler) closest to Save/Preview/VideoCombine (matches “what made this PNG”). */
function _findPrimarySamplerNearestImageOutput(prompt: ComfyPromptMap): string | null {
  const seeds = _collectImageSinkFrameSourceSeeds(prompt);
  if (!seeds.length) return null;
  return _bfsNearestPrimarySamplerFromSeeds(prompt, seeds);
}

const _VHS_LOADER_CLASS_PREFIX = /^VHS_LoadVideo/i;

function _isVhsLoaderClass(ct: string): boolean {
  return _VHS_LOADER_CLASS_PREFIX.test(ct);
}

/** Multi-source BFS backward along input edges; minimum hop count from any seed. */
function _multiSourceUpstreamDistance(prompt: ComfyPromptMap, seeds: string[], maxDepth: number): Map<string, number> {
  const dist = new Map<string, number>();
  const q: { nid: string; d: number }[] = [];
  for (const s of seeds) {
    if (!dist.has(s)) {
      dist.set(s, 0);
      q.push({ nid: s, d: 0 });
    }
  }
  while (q.length > 0) {
    const { nid, d } = q.shift()!;
    if (d >= maxDepth) continue;
    const ins = _nodeInputs(prompt, nid);
    if (!ins) continue;
    for (const v of Object.values(ins)) {
      if (!_isComfyEdgeRef(v)) continue;
      const src = String(v[0]);
      const nd = d + 1;
      const prev = dist.get(src);
      if (prev === undefined || nd < prev) {
        dist.set(src, nd);
        q.push({ nid: src, d: nd });
      }
    }
  }
  return dist;
}

export type VhsLoaderQuickEdit = {
  nodeId: string;
  classType: string;
  hasSkipFirst: boolean;
  skip_first_frames: number;
  hasFrameCap: boolean;
  frame_load_cap: number;
  hasForceRate: boolean;
  force_rate: number;
};

function _findVhsLoaderQuickEdit(prompt: ComfyPromptMap, upstream: Map<string, number>): VhsLoaderQuickEdit | null {
  let best: { nid: string; d: number } | null = null;
  for (const nid of _sortNodeIds(Object.keys(prompt))) {
    const ct = _classType(prompt, nid);
    if (!_isVhsLoaderClass(ct)) continue;
    const d = upstream.get(nid);
    if (d === undefined) continue;
    if (!best || d < best.d || (d === best.d && _sortNodeIds([nid, best.nid])[0] === nid)) best = { nid, d };
  }
  if (!best) return null;
  const nodeId = best.nid;
  const ins = _nodeInputs(prompt, nodeId);
  if (!ins) return null;
  const hasSkip =
    Object.prototype.hasOwnProperty.call(ins, "skip_first_frames") && !_isComfyEdgeRef(ins.skip_first_frames);
  const hasCap = Object.prototype.hasOwnProperty.call(ins, "frame_load_cap") && !_isComfyEdgeRef(ins.frame_load_cap);
  const hasRate = Object.prototype.hasOwnProperty.call(ins, "force_rate") && !_isComfyEdgeRef(ins.force_rate);
  if (!hasSkip && !hasCap && !hasRate) return null;
  const sf = _readLiteralNumber(ins.skip_first_frames);
  const cap = _readLiteralNumber(ins.frame_load_cap);
  const fr = _readLiteralNumber(ins.force_rate);
  return {
    nodeId,
    classType: _classType(prompt, nodeId),
    hasSkipFirst: hasSkip,
    skip_first_frames: sf != null ? Math.round(sf) : 0,
    hasFrameCap: hasCap,
    frame_load_cap: cap != null ? Math.round(cap) : 0,
    hasForceRate: hasRate,
    force_rate: fr != null ? fr : 0,
  };
}

export type VhsVideoCombineQuickEdit = {
  nodeId: string;
  classType: string;
  filename_prefix: string;
  frame_rate: number;
  hasSaveOut: boolean;
  save_output: boolean;
  hasSaveMeta: boolean;
  save_metadata: boolean;
};

function _findVhsVideoCombineQuickEdit(prompt: ComfyPromptMap, upstream: Map<string, number>): VhsVideoCombineQuickEdit | null {
  let best: { nid: string; srcDepth: number } | null = null;
  for (const nid of _sortNodeIds(Object.keys(prompt))) {
    if (!/VHS_VideoCombine/i.test(_classType(prompt, nid))) continue;
    const ins = _nodeInputs(prompt, nid);
    if (!ins) continue;
    const img = ins.images;
    if (!_isComfyEdgeRef(img)) continue;
    const src = String(img[0]);
    const d = upstream.get(src);
    if (d === undefined) continue;
    if (!best || d < best.srcDepth || (d === best.srcDepth && _sortNodeIds([nid, best.nid])[0] === nid)) best = { nid, srcDepth: d };
  }
  if (!best) return null;
  const nodeId = best.nid;
  const ins = _nodeInputs(prompt, nodeId);
  if (!ins) return null;
  if (_isComfyEdgeRef(ins.filename_prefix) || _isComfyEdgeRef(ins.frame_rate)) return null;
  if (!Object.prototype.hasOwnProperty.call(ins, "frame_rate")) return null;
  const frLit = _readLiteralNumber(ins.frame_rate);
  if (frLit == null || frLit <= 0) return null;
  const fp = ins.filename_prefix;
  const filename_prefix = typeof fp === "string" ? fp : typeof fp === "number" ? String(fp) : "";
  const so = _readPromptBool(ins.save_output);
  const sm = _readPromptBool(ins.save_metadata);
  return {
    nodeId,
    classType: _classType(prompt, nodeId),
    filename_prefix,
    frame_rate: frLit,
    hasSaveOut: Object.prototype.hasOwnProperty.call(ins, "save_output") && !_isComfyEdgeRef(ins.save_output),
    save_output: so ?? true,
    hasSaveMeta: Object.prototype.hasOwnProperty.call(ins, "save_metadata") && !_isComfyEdgeRef(ins.save_metadata),
    save_metadata: sm ?? false,
  };
}

/** ComfyUI / KSampler-style after-run seed behavior (API ``inputs`` string). */
const _CONTROL_AFTER_MODES = ["fixed", "randomize", "increment"] as const;

/*
 * Future (seed surfing): multi-run experiments that sweep seeds both ways from a baseline at chosen
 * intervals, to study seed stability and adjacent states. See docs/PROJECT_ORGANIZATION_PROPOSAL.md §10.
 */

export type SeedLikeRow = {
  nodeId: string;
  classType: string;
  intKey: "noise_seed" | "seed";
  seedValue: number | null;
  control_after_generate: string | null;
};

/**
 * Lists every RandomNoise and KSampler-like node in the API prompt for correlating with embedded
 * ``prompt`` metadata on PNG/MP4 outputs (past runs). See ``collect_seeds_from_prompt`` in
 * ``workspace/scripts/comfy_meta_lib.py`` for the Python twin used in scripts.
 */
export function collectSeedLikeRowsFromPrompt(prompt: ComfyPromptMap): SeedLikeRow[] {
  const rows: SeedLikeRow[] = [];
  for (const nodeId of _sortNodeIds(Object.keys(prompt))) {
    const ct = _classType(prompt, nodeId);
    const ins = _nodeInputs(prompt, nodeId);
    if (!ins) continue;
    if (ct === "RandomNoise") {
      const ns = ins.noise_seed;
      const cad = ins.control_after_generate;
      const lit = _isComfyEdgeRef(ns) ? null : _readLiteralNumber(ns);
      rows.push({
        nodeId,
        classType: ct,
        intKey: "noise_seed",
        seedValue: lit != null && Number.isFinite(lit) ? Math.round(lit) : null,
        control_after_generate: typeof cad === "string" && !_isComfyEdgeRef(cad) ? cad : null,
      });
      continue;
    }
    if (ct === "KSampler" || ct === "KSamplerAdvanced") {
      const s = ins.seed;
      const cad = ins.control_after_generate;
      const lit = _isComfyEdgeRef(s) ? null : _readLiteralNumber(s);
      rows.push({
        nodeId,
        classType: ct,
        intKey: "seed",
        seedValue: lit != null && Number.isFinite(lit) ? Math.round(lit) : null,
        control_after_generate: typeof cad === "string" && !_isComfyEdgeRef(cad) ? cad : null,
      });
    }
  }
  return rows;
}

function _findFirstWanImageToVideoId(prompt: ComfyPromptMap): string | null {
  for (const nodeId of _sortNodeIds(Object.keys(prompt))) {
    if (_classType(prompt, nodeId) === "WanImageToVideo") return nodeId;
  }
  return null;
}

function _firstRandomNoiseNodeId(prompt: ComfyPromptMap): string | null {
  for (const nodeId of _sortNodeIds(Object.keys(prompt))) {
    if (_classType(prompt, nodeId) === "RandomNoise") return nodeId;
  }
  return null;
}

function _pickFurthestUpstreamRandomNoise(prompt: ComfyPromptMap, anchorId: string): string | null {
  const dist = _upstreamMinDepthMap(prompt, anchorId, 48);
  let best: { nid: string; d: number } | null = null;
  for (const [nid, d] of dist) {
    if (_classType(prompt, nid) !== "RandomNoise") continue;
    if (!best || d > best.d || (d === best.d && _sortNodeIds([nid, best.nid])[0] === nid)) best = { nid, d };
  }
  return best?.nid ?? null;
}

function _pickFurthestUpstreamKsamplerWithLiteralSeed(prompt: ComfyPromptMap, anchorId: string): string | null {
  const dist = _upstreamMinDepthMap(prompt, anchorId, 48);
  let best: { nid: string; d: number } | null = null;
  for (const [nid, d] of dist) {
    const ct = _classType(prompt, nid);
    if (ct !== "KSampler" && ct !== "KSamplerAdvanced") continue;
    const ins = _nodeInputs(prompt, nid);
    if (!ins) continue;
    const s = ins.seed;
    if (_isComfyEdgeRef(s)) continue;
    if (_readLiteralNumber(s) == null) continue;
    if (!best || d > best.d || (d === best.d && _sortNodeIds([nid, best.nid])[0] === nid)) best = { nid, d };
  }
  return best?.nid ?? null;
}

export type NoiseSeedQuickEdit = {
  nodeId: string;
  classType: string;
  intKey: "noise_seed" | "seed";
  seedValue: number;
  /** Comfy after-run behavior: ``fixed`` | ``randomize`` | ``increment`` (lowercase in API). */
  control_after_generate: string;
};

function _normalizeControlAfterGenerate(raw: unknown): string {
  if (typeof raw !== "string") return "fixed";
  const t = raw.trim().toLowerCase();
  return _CONTROL_AFTER_MODES.includes(t as (typeof _CONTROL_AFTER_MODES)[number]) ? t : "fixed";
}

function _noiseSeedQuickFromRandomNoise(prompt: ComfyPromptMap, nid: string): NoiseSeedQuickEdit | null {
  const ins = _nodeInputs(prompt, nid);
  if (!ins) return null;
  const ns = ins.noise_seed;
  if (_isComfyEdgeRef(ns)) return null;
  const lit = _readLiteralNumber(ns);
  if (lit == null || !Number.isFinite(lit)) return null;
  return {
    nodeId: nid,
    classType: "RandomNoise",
    intKey: "noise_seed",
    seedValue: Math.round(lit),
    control_after_generate: _normalizeControlAfterGenerate(ins.control_after_generate),
  };
}

function _noiseSeedQuickFromKsampler(prompt: ComfyPromptMap, nid: string): NoiseSeedQuickEdit | null {
  const ins = _nodeInputs(prompt, nid);
  if (!ins) return null;
  const s = ins.seed;
  if (_isComfyEdgeRef(s)) return null;
  const lit = _readLiteralNumber(s);
  if (lit == null || !Number.isFinite(lit)) return null;
  return {
    nodeId: nid,
    classType: _classType(prompt, nid),
    intKey: "seed",
    seedValue: Math.round(lit),
    control_after_generate: _normalizeControlAfterGenerate(ins.control_after_generate),
  };
}

/**
 * Primary noise / seed widget driving the sampler nearest the image sink (RandomNoise upstream of
 * SamplerCustom*, else KSampler seed). Falls back to any RandomNoise in the graph.
 */
export function findNoiseSeedQuickEdit(prompt: ComfyPromptMap): NoiseSeedQuickEdit | null {
  const sampler = _findSamplerQuick(prompt);
  const anchor = sampler?.nodeId ?? _findFirstWanImageToVideoId(prompt);
  if (anchor) {
    const rn = _pickFurthestUpstreamRandomNoise(prompt, anchor);
    if (rn) {
      const q = _noiseSeedQuickFromRandomNoise(prompt, rn);
      if (q) return q;
    }
    const ks = _pickFurthestUpstreamKsamplerWithLiteralSeed(prompt, anchor);
    if (ks) {
      const q = _noiseSeedQuickFromKsampler(prompt, ks);
      if (q) return q;
    }
  }
  const anyRn = _firstRandomNoiseNodeId(prompt);
  if (anyRn) {
    const q = _noiseSeedQuickFromRandomNoise(prompt, anyRn);
    if (q) return q;
  }
  for (const nodeId of _sortNodeIds(Object.keys(prompt))) {
    const ct = _classType(prompt, nodeId);
    if (ct !== "KSampler" && ct !== "KSamplerAdvanced") continue;
    const q = _noiseSeedQuickFromKsampler(prompt, nodeId);
    if (q) return q;
  }
  return null;
}

function _randomSeedInt(): number {
  try {
    const buf = new Uint32Array(2);
    crypto.getRandomValues(buf);
    return (buf[0]! >>> 0) * 0x100000000 + (buf[1]! >>> 0);
  } catch {
    return Math.floor(Math.random() * Number.MAX_SAFE_INTEGER);
  }
}

/** Inputs that often carry CONDITIONING edges toward a CLIPTextEncode. */
const _CONDITIONING_EDGE_KEYS: readonly string[] = [
  "positive",
  "negative",
  "conditioning",
  "conditioning_1",
  "conditioning_2",
  "positive_1",
  "positive_2",
  "negative_1",
  "negative_2",
];

function _traceToClipTextEncodeId(prompt: ComfyPromptMap, ref: unknown, visited: Set<string>): string | null {
  if (!_isComfyEdgeRef(ref)) return null;
  const nid = String(ref[0]);
  const sourceOutSlot = ref[1];
  if (visited.has(nid)) return null;
  visited.add(nid);
  const node = prompt[nid];
  if (typeof node !== "object" || node === null) return null;
  const ct = _classType(prompt, nid);
  if (_isFlorenceFamilyClass(ct)) return null;
  if (ct.includes("CLIPTextEncode")) return nid;
  const ins = _nodeInputs(prompt, nid);
  if (!ins) return null;
  // WanImageToVideo has two CONDITIONING outputs (API slot 0 = positive, 1 = negative). Edges from
  // CFGGuider carry [wanId, slot]. A generic scan of conditioning inputs always hits `positive` first,
  // so the negative branch incorrectly resolved to the positive CLIP; follow only the matching input.
  if (ct === "WanImageToVideo" && (sourceOutSlot === 0 || sourceOutSlot === 1)) {
    const branchKey = sourceOutSlot === 0 ? "positive" : "negative";
    if (Object.prototype.hasOwnProperty.call(ins, branchKey)) {
      const found = _traceToClipTextEncodeId(prompt, ins[branchKey], visited);
      if (found) return found;
    }
  }
  for (const k of _CONDITIONING_EDGE_KEYS) {
    if (!(k in ins)) continue;
    const found = _traceToClipTextEncodeId(prompt, ins[k], visited);
    if (found) return found;
  }
  return null;
}

/**
 * Resolve `inputs.text` (string or edge) for display/editing. Does not implement Florence outputs;
 * edges into Florence-family nodes yield "" so Quick Edits stay empty until the graph uses literals or other nodes.
 */
function _resolveTextInputValue(prompt: ComfyPromptMap, value: unknown, depth: number, visited: Set<string>): string {
  if (depth > 28) return "";
  if (typeof value === "string") return value;
  if (!_isComfyEdgeRef(value)) return "";
  const nid = String(value[0]);
  if (visited.has(nid)) return "";
  visited.add(nid);
  const ct = _classType(prompt, nid);
  if (_isFlorenceFamilyClass(ct)) return "";
  if (ct.includes("CLIPTextEncode")) {
    const ins = _nodeInputs(prompt, nid);
    if (!ins) return "";
    return _resolveTextInputValue(prompt, ins.text, depth + 1, visited);
  }
  const ins = _nodeInputs(prompt, nid);
  if (!ins) return "";
  let best = "";
  for (const v of Object.values(ins)) {
    if (typeof v === "string" && v.length > best.length) best = v;
  }
  for (const v of Object.values(ins)) {
    if (_isComfyEdgeRef(v)) {
      const s = _resolveTextInputValue(prompt, v, depth + 1, visited);
      if (s.length > best.length) best = s;
    }
  }
  return best;
}

function _clipResolvedTextOnEncodeNode(prompt: ComfyPromptMap, encodeNodeId: string): string {
  const ins = _nodeInputs(prompt, encodeNodeId);
  if (!ins) return "";
  return _resolveTextInputValue(prompt, ins.text, 0, new Set());
}

function _allClipEncodeSlots(prompt: ComfyPromptMap): { nodeId: string; text: string }[] {
  const out: { nodeId: string; text: string }[] = [];
  for (const nodeId of _sortNodeIds(Object.keys(prompt))) {
    const ct = _classType(prompt, nodeId);
    if (!ct.includes("CLIPTextEncode")) continue;
    out.push({ nodeId, text: _clipResolvedTextOnEncodeNode(prompt, nodeId) });
  }
  return out;
}

function _primarySamplerNodeIdsWithResolvableQuickControls(prompt: ComfyPromptMap): string[] {
  const ids: string[] = [];
  for (const nodeId of _sortNodeIds(Object.keys(prompt))) {
    const ct = _classType(prompt, nodeId);
    if (ct === "KSampler" || ct === "KSamplerAdvanced") {
      const { cfg, steps } = _resolveKsamplerCfgSteps(prompt, nodeId);
      if (cfg != null && steps != null) ids.push(nodeId);
      continue;
    }
    if (ct === "SamplerCustomAdvanced") {
      const ins = _nodeInputs(prompt, nodeId);
      if (!ins) continue;
      const cfgT = _resolveGuiderCfgValueAndTarget(prompt, ins.guider);
      const stepsT = _resolveSigmasUpstreamStepsAndTarget(prompt, ins.sigmas, new Set());
      if (cfgT != null && stepsT != null) ids.push(nodeId);
      continue;
    }
    if (ct === "SamplerCustom") {
      const ins = _nodeInputs(prompt, nodeId);
      if (!ins) continue;
      const cfg = _resolveLinkedNumberForInput(prompt, ins.cfg, "cfg", 0, new Set());
      const stepsT = _resolveSigmasUpstreamStepsAndTarget(prompt, ins.sigmas, new Set());
      if (cfg != null && stepsT != null) ids.push(nodeId);
    }
  }
  return ids;
}

/** Prefer the sampler feeding the saved image so prompts + Steps match the output file. */
function _orderedPrimarySamplersForQuickEdits(prompt: ComfyPromptMap, ids: string[]): string[] {
  const nearest = _findPrimarySamplerNearestImageOutput(prompt);
  if (nearest && ids.includes(nearest)) return [nearest, ...ids.filter((id) => id !== nearest)];
  return ids;
}

/** API `inputs` keys that usually mean “CFG-like” user knobs (anchored subgraph scan). */
const _QUICK_CFG_WIDGET_KEYS = new Set([
  "cfg",
  "guidance",
  "guidance_scale",
  "cfg_conds",
  "cfg_cond2_negative",
]);
/** Keys that usually mean “step count” style knobs (not sigmas tensors). */
const _QUICK_STEPS_WIDGET_KEYS = new Set(["steps"]);
/** Skip obvious non-widget numerics when scanning upstream of the sampler. */
const _QUICK_WIDGET_SKIP_INPUT_KEYS = new Set([
  "noise_seed",
  "seed",
  "batch_index",
  "width",
  "height",
  "x",
  "y",
  "frame_rate",
  "loop_count",
  "isfloatX",
  "isfloatY",
]);

type _AnchoredDiscoveredWidget = {
  nodeId: string;
  inputKey: string;
  value: number;
  depth: number;
  classType: string;
  role: "steps" | "cfg" | "denoise";
};

/** Minimum hop count from `anchorId` backward along input edge refs (anchor = 0). */
function _upstreamMinDepthMap(prompt: ComfyPromptMap, anchorId: string, maxDepth: number): Map<string, number> {
  const dist = new Map<string, number>([[anchorId, 0]]);
  const q: string[] = [anchorId];
  while (q.length > 0) {
    const nid = q.shift()!;
    const d = dist.get(nid)!;
    if (d >= maxDepth) continue;
    const ins = _nodeInputs(prompt, nid);
    if (!ins) continue;
    for (const v of Object.values(ins)) {
      if (!_isComfyEdgeRef(v)) continue;
      const src = String(v[0]);
      const nd = d + 1;
      if (!dist.has(src)) {
        dist.set(src, nd);
        q.push(src);
      }
    }
  }
  return dist;
}

/**
 * Find literal numeric “widget” inputs upstream of the primary sampler so Quick Edits can target
 * the same nodes users drag in the graph (mxToolkit sliders, scheduler steps, Flux guidance, etc.).
 */
function _discoverAnchoredQuickWidgets(
  prompt: ComfyPromptMap,
  anchorId: string,
): { steps?: _AnchoredDiscoveredWidget; cfg?: _AnchoredDiscoveredWidget; denoise?: _AnchoredDiscoveredWidget } {
  const dist = _upstreamMinDepthMap(prompt, anchorId, 28);
  const stepPool: _AnchoredDiscoveredWidget[] = [];
  const cfgPool: _AnchoredDiscoveredWidget[] = [];
  const denoisePool: _AnchoredDiscoveredWidget[] = [];

  for (const [nid, depth] of dist) {
    const ins = _nodeInputs(prompt, nid);
    if (!ins) continue;
    const ct = _classType(prompt, nid);

    if (ct === "mxSlider" || ct === "mxSlider2D") {
      const v = _readMxSliderScalar(ins);
      if (v != null) {
        stepPool.push({
          nodeId: nid,
          inputKey: _mxSliderWriteKey(ins),
          value: Math.round(v),
          depth,
          classType: ct,
          role: "steps",
        });
      }
    }

    for (const key of Object.keys(ins)) {
      if (_QUICK_WIDGET_SKIP_INPUT_KEYS.has(key)) continue;
      const val = ins[key];
      if (_isComfyEdgeRef(val)) continue;
      const lit = _readLiteralNumber(val);
      if (lit == null) continue;

      if (_QUICK_STEPS_WIDGET_KEYS.has(key)) {
        stepPool.push({
          nodeId: nid,
          inputKey: key,
          value: Math.round(lit),
          depth,
          classType: ct,
          role: "steps",
        });
      } else if (_QUICK_CFG_WIDGET_KEYS.has(key)) {
        cfgPool.push({ nodeId: nid, inputKey: key, value: lit, depth, classType: ct, role: "cfg" });
      } else if (key === "denoise") {
        denoisePool.push({ nodeId: nid, inputKey: key, value: lit, depth, classType: ct, role: "denoise" });
      }
    }
  }

  const pickSteps = (): _AnchoredDiscoveredWidget | undefined => {
    if (!stepPool.length) return undefined;
    const mx = stepPool.filter((p) => p.classType === "mxSlider" || p.classType === "mxSlider2D");
    const pool = mx.length ? mx : stepPool;
    return pool.sort((a, b) => b.depth - a.depth || a.nodeId.localeCompare(b.nodeId))[0];
  };

  const pickCfg = (): _AnchoredDiscoveredWidget | undefined => {
    if (!cfgPool.length) return undefined;
    const named = cfgPool.filter(
      (p) =>
        p.classType === "CFGGuider" ||
        p.classType === "DualCFGGuider" ||
        /fluxguidance|flux.*guidance|cfg.*guidance/i.test(p.classType),
    );
    const pool = named.length ? named : cfgPool;
    return pool.sort((a, b) => b.depth - a.depth || a.nodeId.localeCompare(b.nodeId))[0];
  };

  const pickDenoise = (): _AnchoredDiscoveredWidget | undefined => {
    if (!denoisePool.length) return undefined;
    const sched = denoisePool.filter((p) => p.classType === "BasicScheduler" || p.classType === "SDTurboScheduler");
    const pool = sched.length ? sched : denoisePool;
    return pool.sort((a, b) => b.depth - a.depth || a.nodeId.localeCompare(b.nodeId))[0];
  };

  return { steps: pickSteps(), cfg: pickCfg(), denoise: pickDenoise() };
}

function _formatQuickWidgetSummary(d: ReturnType<typeof _discoverAnchoredQuickWidgets>): string | undefined {
  const parts: string[] = [];
  if (d.steps) parts.push(`steps → ${d.steps.classType} ${d.steps.nodeId}.${d.steps.inputKey} (${d.steps.value})`);
  if (d.cfg) parts.push(`cfg → ${d.cfg.classType} ${d.cfg.nodeId}.${d.cfg.inputKey} (${d.cfg.value})`);
  if (d.denoise) parts.push(`denoise → ${d.denoise.classType} ${d.denoise.nodeId}.${d.denoise.inputKey} (${d.denoise.value})`);
  return parts.length ? `Widget targets (upstream of sampler): ${parts.join(" · ")}` : undefined;
}

function _applyAnchoredWidgetDiscoveryToSamplerRow(
  prompt: ComfyPromptMap,
  anchorId: string,
  row: {
    cfg: number;
    steps: number;
    denoise: number | null;
    stepsWrite?: { nodeId: string; inputKey: string };
    cfgWrite?: { nodeId: string; inputKey: string };
    denoiseWrite?: { nodeId: string; inputKey: string };
  },
): { quickWidgetSummary?: string } {
  const disc = _discoverAnchoredQuickWidgets(prompt, anchorId);
  const dist = _upstreamMinDepthMap(prompt, anchorId, 28);

  const depthOf = (nodeId: string | undefined): number => (nodeId ? dist.get(nodeId) ?? -1 : -1);

  if (disc.steps != null && Math.abs(disc.steps.value - row.steps) < 0.51) {
    const curD = depthOf(row.stepsWrite?.nodeId);
    if (disc.steps.classType === "mxSlider" || disc.steps.classType === "mxSlider2D" || disc.steps.depth >= curD) {
      row.stepsWrite = { nodeId: disc.steps.nodeId, inputKey: disc.steps.inputKey };
    }
  }

  if (disc.cfg != null && Math.abs(disc.cfg.value - row.cfg) < 0.05) {
    const curD = depthOf(row.cfgWrite?.nodeId);
    if (/fluxguidance|flux.*guidance/i.test(disc.cfg.classType) || disc.cfg.depth >= curD) {
      row.cfgWrite = { nodeId: disc.cfg.nodeId, inputKey: disc.cfg.inputKey };
    }
  }

  if (disc.denoise != null && row.denoise != null && Math.abs(disc.denoise.value - row.denoise) < 0.001) {
    const curD = depthOf(row.denoiseWrite?.nodeId);
    if (disc.denoise.classType === "BasicScheduler" || disc.denoise.classType === "SDTurboScheduler" || disc.denoise.depth >= curD) {
      row.denoiseWrite = { nodeId: disc.denoise.nodeId, inputKey: disc.denoise.inputKey };
    }
  }

  return { quickWidgetSummary: _formatQuickWidgetSummary(disc) };
}

/** Discovery Quick Edits: non-fatal config / tracing hints, grouped by UI area. */
export type QuickEditConfigNote = { section: string; message: string };

function _appendUnresolvedPromptNote(
  prompt: ComfyPromptMap,
  slot: { nodeId: string; text: string } | null,
  section: string,
  notes: QuickEditConfigNote[],
) {
  if (!slot || slot.text.trim().length > 0) return;
  const ins = _nodeInputs(prompt, slot.nodeId);
  const t = ins?.text;
  if (_isComfyEdgeRef(t)) {
    const hopCt = _classType(prompt, String(t[0]));
    if (_isFlorenceFamilyClass(hopCt)) {
      notes.push({
        section,
        message: `CLIP node ${slot.nodeId}: \`text\` is linked from a Florence node. Quick Edits does not read Florence outputs—use a literal on CLIPTextEncode or a string primitive you want to edit here.`,
      });
    } else {
      notes.push({
        section,
        message: `CLIP node ${slot.nodeId}: \`text\` is linked but no displayable string was resolved (custom or unsupported upstream). Use “All node fields (advanced)” or simplify the wiring.`,
      });
    }
  }
}

export type QuickEditClipDerive = {
  pos: { nodeId: string; text: string } | null;
  neg: { nodeId: string; text: string } | null;
  /** Non-fatal hints when heuristics may be wrong or text could not be shown. */
  notes: QuickEditConfigNote[];
};

/**
 * Best-effort positive/negative CLIP selection—not exhaustive for every custom conditioning graph.
 * Surfaces `notes` so the UI can explain fallbacks instead of failing silently.
 */
function _derivePosNegClipDetailed(prompt: ComfyPromptMap): QuickEditClipDerive {
  const notes: QuickEditConfigNote[] = [];

  const ks = _primarySamplerNodeIdsWithResolvableQuickControls(prompt);
  const ksOrdered = _orderedPrimarySamplersForQuickEdits(prompt, ks);
  if (ks.length > 1) {
    const preview = ks.slice(0, 6).join(", ");
    notes.push({
      section: "Sampler (CFG / Steps / Denoise)",
      message: `Multiple primary samplers with resolvable cfg/steps (${ks.length} nodes: ${preview}${ks.length > 6 ? ", …" : ""}). Prompt tracing uses the sampler nearest SaveImage/Preview/VideoCombine first (${ksOrdered[0]}), then others by node id. If that is not your main sampler, use “All node fields (advanced)”.`,
    });
  } else if (ks.length === 0) {
    notes.push({
      section: "Sampler (CFG / Steps / Denoise)",
      message:
        "No KSampler / KSamplerAdvanced / SamplerCustom* with resolvable cfg and steps (including sigmas chains and mxToolkit sliders) was found; prompt tracing tries WanImageToVideo, then CLIP node order.",
    });
  }

  for (const nodeId of ksOrdered) {
    const { pos: posId, neg: negId } = _traceSamplerClipIds(prompt, nodeId);
    if (posId && negId) {
      const pos = { nodeId: posId, text: _clipResolvedTextOnEncodeNode(prompt, posId) };
      const neg = { nodeId: negId, text: _clipResolvedTextOnEncodeNode(prompt, negId) };
      _appendUnresolvedPromptNote(prompt, pos, "Positive prompt", notes);
      _appendUnresolvedPromptNote(prompt, neg, "Negative prompt", notes);
      return { pos, neg, notes };
    }
    if (posId || negId) {
      const traced = [posId ? "positive" : "", negId ? "negative" : ""].filter(Boolean).join(" and ");
      notes.push({
        section: "Positive & negative prompts",
        message: `Sampler node ${nodeId}: traced only ${traced} to CLIPTextEncode; the other branch did not reach a CLIP node via known conditioning inputs. Using partial mapping; check “All node fields (advanced)”.`,
      });
      const pos = posId ? { nodeId: posId, text: _clipResolvedTextOnEncodeNode(prompt, posId) } : null;
      const neg = negId ? { nodeId: negId, text: _clipResolvedTextOnEncodeNode(prompt, negId) } : null;
      _appendUnresolvedPromptNote(prompt, pos, "Positive prompt", notes);
      _appendUnresolvedPromptNote(prompt, neg, "Negative prompt", notes);
      return { pos, neg, notes };
    }
  }

  for (const nodeId of _sortNodeIds(Object.keys(prompt))) {
    const ct = _classType(prompt, nodeId);
    if (ct !== "WanImageToVideo") continue;
    const ins = _nodeInputs(prompt, nodeId);
    if (!ins) continue;
    const { pos: posId, neg: negId } = _traceSamplerClipIds(prompt, nodeId);
    if (posId && negId) {
      notes.push({
        section: "Positive & negative prompts",
        message: `Prompts traced from WanImageToVideo node ${nodeId} (not KSampler).`,
      });
      const pos = { nodeId: posId, text: _clipResolvedTextOnEncodeNode(prompt, posId) };
      const neg = { nodeId: negId, text: _clipResolvedTextOnEncodeNode(prompt, negId) };
      _appendUnresolvedPromptNote(prompt, pos, "Positive prompt", notes);
      _appendUnresolvedPromptNote(prompt, neg, "Negative prompt", notes);
      return { pos, neg, notes };
    }
    if (posId || negId) {
      const tracedW = [posId ? "positive" : "", negId ? "negative" : ""].filter(Boolean).join(" and ");
      notes.push({
        section: "Positive & negative prompts",
        message: `WanImageToVideo node ${nodeId}: traced only ${tracedW} to CLIPTextEncode. Partial mapping.`,
      });
      const pos = posId ? { nodeId: posId, text: _clipResolvedTextOnEncodeNode(prompt, posId) } : null;
      const neg = negId ? { nodeId: negId, text: _clipResolvedTextOnEncodeNode(prompt, negId) } : null;
      _appendUnresolvedPromptNote(prompt, pos, "Positive prompt", notes);
      _appendUnresolvedPromptNote(prompt, neg, "Negative prompt", notes);
      return { pos, neg, notes };
    }
  }

  notes.push({
    section: "Positive & negative prompts",
    message:
      "Could not trace positive/negative from a KSampler or WanImageToVideo to CLIPTextEncode (unknown conditioning nodes or wiring). Using CLIPTextEncode nodes in numeric id order—labels may not match true pos/neg.",
  });
  const all = _allClipEncodeSlots(prompt);
  const pos = all[0] ?? null;
  const neg = all[1] ?? null;
  _appendUnresolvedPromptNote(prompt, pos, "First CLIP slot", notes);
  _appendUnresolvedPromptNote(prompt, neg, "Second CLIP slot", notes);
  return { pos, neg, notes };
}

function _findSamplerQuick(prompt: ComfyPromptMap): {
  nodeId: string;
  classType: string;
  cfg: number;
  steps: number;
  denoise: number | null;
  keys: { cfg: string; steps: string; denoise?: string };
  /** When set, Steps quick edit writes this node/input (e.g. mxSlider `Xi` feeding a sigmas chain). */
  stepsWrite?: { nodeId: string; inputKey: string };
  cfgWrite?: { nodeId: string; inputKey: string };
  denoiseWrite?: { nodeId: string; inputKey: string };
  /** Human-readable summary of anchored widget discovery (upstream of the primary sampler). */
  quickWidgetSummary?: string;
} | null {
  const candidates = _primarySamplerNodeIdsWithResolvableQuickControls(prompt);
  if (!candidates.length) return null;
  const ordered = _orderedPrimarySamplersForQuickEdits(prompt, candidates);
  const nodeId = ordered[0];
  const ct = _classType(prompt, nodeId);

  type MutableWriteRow = {
    cfg: number;
    steps: number;
    denoise: number | null;
    stepsWrite?: { nodeId: string; inputKey: string };
    cfgWrite?: { nodeId: string; inputKey: string };
    denoiseWrite?: { nodeId: string; inputKey: string };
  };

  if (ct === "KSampler" || ct === "KSamplerAdvanced") {
    const ins = _nodeInputs(prompt, nodeId);
    if (!ins) return null;
    const { cfg: cfgResolved, steps: stepsResolved } = _resolveKsamplerCfgSteps(prompt, nodeId);
    if (cfgResolved == null || stepsResolved == null) return null;
    const denoiseLit = _readLiteralNumber(ins.denoise);
    const hasDenoise = denoiseLit != null;
    const row: MutableWriteRow = {
      cfg: cfgResolved,
      steps: Math.round(stepsResolved),
      denoise: hasDenoise ? denoiseLit : null,
    };
    const extra = _applyAnchoredWidgetDiscoveryToSamplerRow(prompt, nodeId, row);
    return {
      nodeId,
      classType: ct,
      keys: { cfg: "cfg", steps: "steps", ...(hasDenoise ? { denoise: "denoise" } : {}) },
      ...row,
      ...extra,
    };
  }

  if (ct === "SamplerCustomAdvanced") {
    const ins = _nodeInputs(prompt, nodeId);
    if (!ins) return null;
    const cfgT = _resolveGuiderCfgValueAndTarget(prompt, ins.guider);
    const stepsT = _resolveSigmasUpstreamStepsAndTarget(prompt, ins.sigmas, new Set());
    if (cfgT == null || stepsT == null) return null;
    const denT = _resolveSigmasChainDenoiseValueAndTarget(prompt, ins.sigmas, new Set());
    const row: MutableWriteRow = {
      cfg: cfgT.value,
      steps: stepsT.value,
      denoise: denT?.value ?? null,
      cfgWrite: cfgT.target,
      stepsWrite: stepsT.target,
      denoiseWrite: denT?.target,
    };
    const extra = _applyAnchoredWidgetDiscoveryToSamplerRow(prompt, nodeId, row);
    return {
      nodeId,
      classType: ct,
      keys: { cfg: "cfg", steps: "steps", ...(denT ? { denoise: "denoise" } : {}) },
      ...row,
      ...extra,
    };
  }

  if (ct === "SamplerCustom") {
    const ins = _nodeInputs(prompt, nodeId);
    if (!ins) return null;
    const cfg = _resolveLinkedNumberForInput(prompt, ins.cfg, "cfg", 0, new Set());
    const stepsT = _resolveSigmasUpstreamStepsAndTarget(prompt, ins.sigmas, new Set());
    if (cfg == null || stepsT == null) return null;
    const denT = _resolveSigmasChainDenoiseValueAndTarget(prompt, ins.sigmas, new Set());
    const row: MutableWriteRow = {
      cfg,
      steps: stepsT.value,
      denoise: denT?.value ?? null,
      cfgWrite: { nodeId, inputKey: "cfg" },
      stepsWrite: stepsT.target,
      denoiseWrite: denT?.target,
    };
    const extra = _applyAnchoredWidgetDiscoveryToSamplerRow(prompt, nodeId, row);
    return {
      nodeId,
      classType: ct,
      keys: { cfg: "cfg", steps: "steps", ...(denT ? { denoise: "denoise" } : {}) },
      ...row,
      ...extra,
    };
  }

  return null;
}

type _SamplerQuickRow = NonNullable<ReturnType<typeof _findSamplerQuick>>;

/** Explanatory copy for the bottom “Config notes” panel (Sampler card stays controls-only). */
function _samplerQuickEditConfigNotes(s: _SamplerQuickRow): QuickEditConfigNote[] {
  const section = "Sampler (CFG / Steps / Denoise)";
  const parts = [`Primary sampler: node ${s.nodeId} · ${s.classType}.`];
  if (s.cfgWrite && (s.cfgWrite.nodeId !== s.nodeId || s.cfgWrite.inputKey !== s.keys.cfg)) {
    parts.push(`CFG writes ${s.cfgWrite.inputKey} on node ${s.cfgWrite.nodeId} (not on the sampler node).`);
  }
  if (s.stepsWrite && (s.stepsWrite.nodeId !== s.nodeId || s.stepsWrite.inputKey !== s.keys.steps)) {
    parts.push(
      `Steps writes ${s.stepsWrite.inputKey} on node ${s.stepsWrite.nodeId} (e.g. mxSlider / scheduler feeding a sigmas chain).`,
    );
  }
  if (
    s.keys.denoise &&
    s.denoiseWrite &&
    (s.denoiseWrite.nodeId !== s.nodeId || s.denoiseWrite.inputKey !== s.keys.denoise)
  ) {
    parts.push(`Denoise writes ${s.denoiseWrite.inputKey} on node ${s.denoiseWrite.nodeId} (not on the sampler node).`);
  }
  const out: QuickEditConfigNote[] = [{ section, message: parts.join(" ") }];
  if (s.quickWidgetSummary) {
    out.push({
      section,
      message: `Upstream widgets (same values as these sliders when aligned): ${s.quickWidgetSummary}`,
    });
  }
  return out;
}

function _durationQuickEditConfigNotes(res: DurationControlResolution): QuickEditConfigNote[] {
  const section = "Duration";
  const { surface, nativeLabel } = res;
  if (surface.kind === "unsupported") {
    return [
      {
        section,
        message: `WanImageToVideo node ${surface.wanNodeId}: ${surface.reason} (${nativeLabel}).`,
      },
    ];
  }
  if (surface.kind === "literal_wan_length") {
    return [
      {
        section,
        message: `Writes inputs.length on WanImageToVideo node ${surface.wanNodeId}. Generation length in frames for this graph—not Discovery trim.`,
      },
    ];
  }
  return [
    {
      section,
      message: `Writes int mxSlider Xi and Xf on node ${surface.sliderNodeId}, which feeds WanImageToVideo.length on node ${surface.wanNodeId}. Wan widget values can diverge from linked API inputs until you reload in Comfy.`,
    },
  ];
}

function _seedQuickEditConfigNotes(seed: NoiseSeedQuickEdit, seedAuditRowCount: number): QuickEditConfigNote[] {
  const section = "Seed";
  const pin =
    seed.control_after_generate === "fixed"
      ? "Mode is fixed (pinned): this seed is what the next queue uses; Comfy will not replace it after a run until you change it here."
      : "Not pinned: after a run, Comfy may change the stored number on the node. Same / New both set fixed for a predictable next submit.";
  const out: QuickEditConfigNote[] = [
    {
      section,
      message: `Noise / seed: node ${seed.nodeId} · ${seed.classType} · ${seed.intKey}. ${pin}`,
    },
    {
      section,
      message:
        "control_after_generate: fixed keeps this value for the next submit; randomize draws a new seed after each run; increment advances the widget. Those modes can desync what you see from the next run.",
    },
  ];
  if (seedAuditRowCount > 0) {
    out.push({
      section,
      message: `There are ${seedAuditRowCount} other seed-like node(s) in this graph—use “All seed-like nodes” on the Seed card for the audit table.`,
    });
  }
  return out;
}

function _findTeaCacheSpeed(prompt: ComfyPromptMap): { nodeId: string; classType: string; rel_l1: number } | null {
  for (const nodeId of _sortNodeIds(Object.keys(prompt))) {
    const ct = _classType(prompt, nodeId);
    if (!/teacache/i.test(ct)) continue;
    const ins = _nodeInputs(prompt, nodeId);
    if (!ins) continue;
    const v = ins.rel_l1_thresh;
    if (typeof v !== "number" || !Number.isFinite(v)) continue;
    return { nodeId, classType: ct, rel_l1: v };
  }
  return null;
}

type PowerLoraSlot = {
  nodeId: string;
  slotKey: string;
  name: string;
  on: boolean;
  strength: number;
  strengthTwo: number | null;
};

type StandardLoraRow = {
  nodeId: string;
  classType: string;
  name: string;
  strengthModel: number;
  strengthClip: number | null;
};

function _isLoraSlotObj(v: unknown): v is { on?: unknown; lora?: unknown; strength?: unknown; strengthTwo?: unknown } {
  return typeof v === "object" && v !== null && !Array.isArray(v) && "lora" in (v as object);
}

function _collectPowerLoraSlots(prompt: ComfyPromptMap): PowerLoraSlot[] {
  const slots: PowerLoraSlot[] = [];
  for (const nodeId of _sortNodeIds(Object.keys(prompt))) {
    const ct = _classType(prompt, nodeId);
    if (!ct.toLowerCase().includes("power lora loader")) continue;
    const ins = _nodeInputs(prompt, nodeId);
    if (!ins) continue;
    for (const key of Object.keys(ins).sort((a, b) => a.localeCompare(b))) {
      if (!/^lora_\d+$/i.test(key)) continue;
      const v = ins[key];
      if (!_isLoraSlotObj(v)) continue;
      const name = typeof v.lora === "string" ? v.lora : "";
      const on = Boolean(v.on);
      const strength = typeof v.strength === "number" && Number.isFinite(v.strength) ? v.strength : 1;
      const st2 = v.strengthTwo;
      const strengthTwo = typeof st2 === "number" && Number.isFinite(st2) ? st2 : null;
      slots.push({ nodeId, slotKey: key, name, on, strength, strengthTwo });
    }
  }
  return slots;
}

function _collectStandardLoras(prompt: ComfyPromptMap): StandardLoraRow[] {
  const rows: StandardLoraRow[] = [];
  for (const nodeId of _sortNodeIds(Object.keys(prompt))) {
    const ct = _classType(prompt, nodeId);
    if (!ct.includes("LoraLoader")) continue;
    if (ct.toLowerCase().includes("power lora")) continue;
    const ins = _nodeInputs(prompt, nodeId);
    if (!ins) continue;
    const nameRaw = ins.lora_name ?? ins.lora;
    const name = typeof nameRaw === "string" ? nameRaw : "";
    const sm = ins.strength_model;
    const sc = ins.strength_clip;
    const strengthModel = typeof sm === "number" && Number.isFinite(sm) ? sm : 1;
    const strengthClip = typeof sc === "number" && Number.isFinite(sc) ? sc : null;
    rows.push({ nodeId, classType: ct, name, strengthModel, strengthClip });
  }
  return rows;
}

function _summarizePrompt(text: string, maxLen = 96): string {
  const t = text.replace(/\s+/g, " ").trim();
  if (!t) return "(empty)";
  if (t.length <= maxLen) return t;
  return `${t.slice(0, maxLen - 1)}…`;
}

type QuickDialog =
  | { kind: "pos"; nodeId: string }
  | { kind: "neg"; nodeId: string }
  | { kind: "prompt"; nodeId: string }
  | { kind: "speed" }
  | { kind: "lora" }
  | { kind: "vhsLoader" }
  | { kind: "vhsOutput" };

export function DiscoveryComfyQuickEditsSection({
  promptDraft,
  setPromptInput,
  onSliderBurstEnd,
  disabled,
}: {
  promptDraft: ComfyPromptMap;
  setPromptInput: (nodeId: string, inputKey: string, value: unknown, meta?: SetPromptInputMeta) => void;
  /** Call after a range slider gesture so the next drag starts a new undo step. */
  onSliderBurstEnd?: () => void;
  disabled?: boolean;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [dlg, setDlg] = useState<QuickDialog | null>(null);

  const allClipSlots = useMemo(() => _allClipEncodeSlots(promptDraft), [promptDraft]);
  const { pos: posSlot, neg: negSlot, notes: quickEditNotes } = useMemo(() => _derivePosNegClipDetailed(promptDraft), [promptDraft]);
  const extraClip = useMemo(() => {
    const skip = new Set<string>([posSlot?.nodeId, negSlot?.nodeId].filter((x): x is string => Boolean(x)));
    return allClipSlots.filter((c) => !skip.has(c.nodeId));
  }, [allClipSlots, posSlot?.nodeId, negSlot?.nodeId]);

  const sampler = useMemo(() => _findSamplerQuick(promptDraft), [promptDraft]);
  const speed = useMemo(() => _findTeaCacheSpeed(promptDraft), [promptDraft]);
  const powerSlots = useMemo(() => _collectPowerLoraSlots(promptDraft), [promptDraft]);
  const standardLoras = useMemo(() => _collectStandardLoras(promptDraft), [promptDraft]);

  const vhsLoader = useMemo(() => {
    const seeds = _collectImageSinkFrameSourceSeeds(promptDraft);
    if (!seeds.length) return null;
    return _findVhsLoaderQuickEdit(promptDraft, _multiSourceUpstreamDistance(promptDraft, seeds, 48));
  }, [promptDraft]);

  const vhsCombine = useMemo(() => {
    const seeds = _collectImageSinkFrameSourceSeeds(promptDraft);
    if (!seeds.length) return null;
    return _findVhsVideoCombineQuickEdit(promptDraft, _multiSourceUpstreamDistance(promptDraft, seeds, 48));
  }, [promptDraft]);

  const noiseSeed = useMemo(() => findNoiseSeedQuickEdit(promptDraft), [promptDraft]);
  const durationResolution = useMemo(() => resolveDurationControlSurface(promptDraft), [promptDraft]);
  const durationEncodeFps = useMemo(() => discoveryLiteralFpsFromVhsCombine(promptDraft), [promptDraft]);
  const seedAuditRows = useMemo(() => collectSeedLikeRowsFromPrompt(promptDraft), [promptDraft]);

  /**
   * Value reapplied by "Same" (repeat pin). Updated when the targeted seed widget changes or the user
   * edits the number field — not when "New" runs — so New → Same restores the pre-New seed (idempotent Same).
   */
  const sameSeedBaselineRef = useRef<number | null>(null);
  const noiseSeedPinKey = noiseSeed ? `${noiseSeed.nodeId}:${noiseSeed.intKey}` : "";
  useEffect(() => {
    if (!noiseSeed) {
      sameSeedBaselineRef.current = null;
      return;
    }
    sameSeedBaselineRef.current = noiseSeed.seedValue;
    // Baseline resets when the quick-edit seed target changes; draft updates from "New" do not reset it.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- deps: noiseSeedPinKey only (see comment above)
  }, [noiseSeedPinKey]);

  const samplerConfigNotes = useMemo(() => (sampler ? _samplerQuickEditConfigNotes(sampler) : []), [sampler]);
  const seedConfigNotes = useMemo(
    () => (noiseSeed ? _seedQuickEditConfigNotes(noiseSeed, seedAuditRows.length) : []),
    [noiseSeed, seedAuditRows.length],
  );
  const durationConfigNotes = useMemo(
    () => (durationResolution ? _durationQuickEditConfigNotes(durationResolution) : []),
    [durationResolution],
  );
  const allConfigNotes = useMemo(
    () => [...quickEditNotes, ...samplerConfigNotes, ...seedConfigNotes, ...durationConfigNotes],
    [quickEditNotes, samplerConfigNotes, seedConfigNotes, durationConfigNotes],
  );

  const openDlg = useCallback((d: QuickDialog) => {
    setDlg(d);
    queueMicrotask(() => dialogRef.current?.showModal());
  }, []);

  const closeDlg = useCallback(() => {
    dialogRef.current?.close();
    setDlg(null);
  }, []);

  useEffect(() => {
    const el = dialogRef.current;
    if (!el) return;
    const onClose = () => setDlg(null);
    el.addEventListener("close", onClose);
    return () => el.removeEventListener("close", onClose);
  }, []);

  const loraSummary = useMemo(() => {
    const parts: string[] = [];
    if (powerSlots.length) {
      const onN = powerSlots.filter((s) => s.on).length;
      parts.push(`${powerSlots.length} Power LoRA slots (${onN} on)`);
    }
    if (standardLoras.length) {
      parts.push(`${standardLoras.length} LoRA loader node(s)`);
    }
    if (!parts.length) return "No LoRA nodes detected in this prompt.";
    return parts.join(" · ");
  }, [powerSlots, standardLoras]);

  const hasAnyQuick =
    Boolean(posSlot) ||
    Boolean(negSlot) ||
    Boolean(sampler) ||
    Boolean(speed) ||
    powerSlots.length > 0 ||
    standardLoras.length > 0 ||
    Boolean(vhsLoader) ||
    Boolean(vhsCombine) ||
    Boolean(noiseSeed) ||
    Boolean(durationResolution);

  const renderDialogBody = () => {
    if (!dlg) return null;
    if (dlg.kind === "pos" && posSlot) {
      return (
        <>
          <h3 className="discovery-comfy-q-dlg-title">Positive prompt</h3>
          <p className="discovery-comfy-q-dlg-hint">Node {posSlot.nodeId}</p>
          <textarea
            className="discovery-comfy-q-dlg-textarea mono"
            rows={14}
            spellCheck={false}
            disabled={disabled}
            value={posSlot.text}
            onChange={(e) => setPromptInput(posSlot.nodeId, "text", e.target.value, QH)}
          />
        </>
      );
    }
    if (dlg.kind === "neg" && negSlot) {
      return (
        <>
          <h3 className="discovery-comfy-q-dlg-title">Negative prompt</h3>
          <p className="discovery-comfy-q-dlg-hint">Node {negSlot.nodeId}</p>
          <textarea
            className="discovery-comfy-q-dlg-textarea mono"
            rows={12}
            spellCheck={false}
            disabled={disabled}
            value={negSlot.text}
            onChange={(e) => setPromptInput(negSlot.nodeId, "text", e.target.value, QH)}
          />
        </>
      );
    }
    if (dlg.kind === "prompt") {
      const slot = allClipSlots.find((c) => c.nodeId === dlg.nodeId);
      if (!slot) return null;
      return (
        <>
          <h3 className="discovery-comfy-q-dlg-title">Prompt (node {slot.nodeId})</h3>
          <textarea
            className="discovery-comfy-q-dlg-textarea mono"
            rows={12}
            spellCheck={false}
            disabled={disabled}
            value={slot.text}
            onChange={(e) => setPromptInput(slot.nodeId, "text", e.target.value, QH)}
          />
        </>
      );
    }
    if (dlg.kind === "speed" && speed) {
      return (
        <>
          <h3 className="discovery-comfy-q-dlg-title">Speed (TeaCache)</h3>
          <p className="discovery-comfy-q-dlg-hint mono">
            Node {speed.nodeId} · {speed.classType} · rel_l1_thresh (lower often = stronger cache)
          </p>
          <SliderNumRow
            label="rel_l1_thresh"
            value={speed.rel_l1}
            min={0}
            max={0.5}
            step={0.005}
            disabled={disabled}
            onRangePointerUp={onSliderBurstEnd}
            onChange={(n, meta) => setPromptInput(speed.nodeId, "rel_l1_thresh", n, { ...QH, ...meta })}
          />
        </>
      );
    }
    if (dlg.kind === "lora") {
      return (
        <>
          <h3 className="discovery-comfy-q-dlg-title">LoRA stack</h3>
          <p className="discovery-comfy-q-dlg-hint">Power LoRA slots and standard LoRA loaders in this graph.</p>
          {powerSlots.length ? (
            <div className="discovery-comfy-q-lora-block">
              <div className="discovery-comfy-q-lora-block-title">Power LoRA Loader</div>
              {powerSlots.map((s) => {
                const slotInactive = !s.on;
                return (
                <div
                  key={`${s.nodeId}:${s.slotKey}`}
                  className={
                    "discovery-comfy-q-lora-slot discovery-comfy-q-lora-slot--split" +
                    (slotInactive ? " discovery-comfy-q-lora-slot--inactive" : "")
                  }
                >
                  <div className="discovery-comfy-q-lora-slot-controls">
                    <SliderNumRow
                      label=""
                      ariaLabel="Strength"
                      value={s.strength}
                      min={-4}
                      max={4}
                      step={0.05}
                      compactInline
                      disabled={disabled || slotInactive}
                      onRangePointerUp={onSliderBurstEnd}
                      onChange={(n, meta) => {
                        const ins = _nodeInputs(promptDraft, s.nodeId);
                        const cur = ins?.[s.slotKey];
                        if (typeof cur !== "object" || cur === null || Array.isArray(cur)) return;
                        setPromptInput(s.nodeId, s.slotKey, { ...(cur as Record<string, unknown>), strength: n }, { ...QH, ...meta });
                      }}
                    />
                  </div>
                  <div className="discovery-comfy-q-lora-slot-meta">
                    <label className="discovery-comfy-q-lora-name discovery-comfy-q-lora-name--meta mono" title={s.name}>
                      <input
                        type="checkbox"
                        checked={s.on}
                        disabled={disabled}
                        onChange={(e) => {
                          const ins = _nodeInputs(promptDraft, s.nodeId);
                          const cur = ins?.[s.slotKey];
                          if (typeof cur !== "object" || cur === null || Array.isArray(cur)) return;
                          setPromptInput(s.nodeId, s.slotKey, { ...(cur as Record<string, unknown>), on: e.target.checked }, QH);
                        }}
                      />
                      <span>{s.name || s.slotKey}</span>
                    </label>
                  </div>
                </div>
                );
              })}
            </div>
          ) : null}
          {standardLoras.length ? (
            <div className="discovery-comfy-q-lora-block">
              <div className="discovery-comfy-q-lora-block-title">LoRA loaders</div>
              {standardLoras.map((r) => {
                const slotInactive = !r.name.trim();
                return (
                <div
                  key={r.nodeId}
                  className={
                    "discovery-comfy-q-lora-slot discovery-comfy-q-lora-slot--split" +
                    (slotInactive ? " discovery-comfy-q-lora-slot--inactive" : "")
                  }
                >
                  <div className="discovery-comfy-q-lora-slot-controls">
                    <SliderNumRow
                      label=""
                      ariaLabel="Model strength"
                      value={r.strengthModel}
                      min={-4}
                      max={4}
                      step={0.05}
                      compactInline
                      disabled={disabled || slotInactive}
                      onRangePointerUp={onSliderBurstEnd}
                      onChange={(n, meta) => setPromptInput(r.nodeId, "strength_model", n, { ...QH, ...meta })}
                    />
                    {r.strengthClip != null ? (
                      <SliderNumRow
                        label=""
                        ariaLabel="CLIP strength"
                        value={r.strengthClip}
                        min={-4}
                        max={4}
                        step={0.05}
                        compactInline
                        disabled={disabled || slotInactive}
                        onRangePointerUp={onSliderBurstEnd}
                        onChange={(n, meta) => setPromptInput(r.nodeId, "strength_clip", n, { ...QH, ...meta })}
                      />
                    ) : null}
                  </div>
                  <div className="discovery-comfy-q-lora-slot-meta">
                    <div className="discovery-comfy-q-lora-name discovery-comfy-q-lora-name--meta mono" title={r.name}>
                      {r.classType} · {r.name || r.nodeId}
                    </div>
                  </div>
                </div>
                );
              })}
            </div>
          ) : null}
        </>
      );
    }
    if (dlg.kind === "vhsLoader" && vhsLoader) {
      const L = vhsLoader.nodeId;
      return (
        <>
          <h3 className="discovery-comfy-q-dlg-title">Video load (VHS)</h3>
          <p className="discovery-comfy-q-dlg-hint mono">
            Node {L} · {vhsLoader.classType}
            <br />
            Keys: {VHS_QUICK_EDIT_INPUT_KEYS.loader.join(", ")} - path / <span className="mono">video</span> is usually set by
            runners, not here.
          </p>
          {vhsLoader.hasSkipFirst ? (
            <SliderNumRow
              label="skip_first_frames"
              value={vhsLoader.skip_first_frames}
              min={0}
              max={512}
              step={1}
              intMode
              inputMin={0}
              inputMax={10_000}
              disabled={disabled}
              onRangePointerUp={onSliderBurstEnd}
              onChange={(n, meta) => setPromptInput(L, "skip_first_frames", n, { ...QH, ...meta })}
            />
          ) : null}
          {vhsLoader.hasFrameCap ? (
            <SliderNumRow
              label="frame_load_cap"
              value={vhsLoader.frame_load_cap}
              min={0}
              max={4096}
              step={1}
              intMode
              inputMin={0}
              inputMax={65_536}
              disabled={disabled}
              onRangePointerUp={onSliderBurstEnd}
              onChange={(n, meta) => setPromptInput(L, "frame_load_cap", n, { ...QH, ...meta })}
            />
          ) : null}
          {vhsLoader.hasForceRate ? (
            <SliderNumRow
              label="force_rate (0 = native)"
              value={vhsLoader.force_rate}
              min={0}
              max={120}
              step={0.5}
              disabled={disabled}
              onRangePointerUp={onSliderBurstEnd}
              onChange={(n, meta) => setPromptInput(L, "force_rate", n, { ...QH, ...meta })}
            />
          ) : null}
        </>
      );
    }
    if (dlg.kind === "vhsOutput" && vhsCombine) {
      const C = vhsCombine.nodeId;
      return (
        <>
          <h3 className="discovery-comfy-q-dlg-title">Video output (VHS_VideoCombine)</h3>
          <p className="discovery-comfy-q-dlg-hint mono">
            Node {C} · encode uses <span className="mono">frame_rate</span> (not loader <span className="mono">force_rate</span>).
            Batch jobs can still override <span className="mono">filename_prefix</span> in code.
          </p>
          <label className="discovery-comfy-q-dlg-hint">filename_prefix</label>
          <textarea
            className="discovery-comfy-q-dlg-textarea mono compact"
            rows={4}
            spellCheck={false}
            disabled={disabled}
            value={vhsCombine.filename_prefix}
            onChange={(e) => setPromptInput(C, "filename_prefix", e.target.value, QH)}
          />
          <SliderNumRow
            label="frame_rate"
            value={vhsCombine.frame_rate}
            min={1}
            max={120}
            step={0.5}
            disabled={disabled}
            onRangePointerUp={onSliderBurstEnd}
            onChange={(n, meta) => setPromptInput(C, "frame_rate", n, { ...QH, ...meta })}
          />
          {vhsCombine.hasSaveOut ? (
            <label className="discovery-comfy-q-checkrow">
              <input
                type="checkbox"
                checked={vhsCombine.save_output}
                disabled={disabled}
                onChange={(e) => setPromptInput(C, "save_output", e.target.checked, QH)}
              />
              save_output
            </label>
          ) : null}
          {vhsCombine.hasSaveMeta ? (
            <label className="discovery-comfy-q-checkrow">
              <input
                type="checkbox"
                checked={vhsCombine.save_metadata}
                disabled={disabled}
                onChange={(e) => setPromptInput(C, "save_metadata", e.target.checked, QH)}
              />
              save_metadata
            </label>
          ) : null}
        </>
      );
    }
    return null;
  };

  return (
    <div className="discovery-comfy-q-root">
      <div className="discovery-comfy-q-head">Quick edits</div>
      {!hasAnyQuick ? (
        <p className="discovery-comfy-q-empty">
          No quick-edit targets found (CLIP / sampler / seed / duration (frames) / TeaCache / LoRA / VHS load or
          VHS_VideoCombine on the path
          to a save or preview).
        </p>
      ) : (
        <div className="discovery-comfy-q-cards">
          {posSlot ? (
            <div className="discovery-comfy-q-card">
              <div className="discovery-comfy-q-card-title">Positive prompt</div>
              <div className="discovery-comfy-q-card-summary">{_summarizePrompt(posSlot.text)}</div>
              <button type="button" className="discovery-comfy-q-edit" disabled={disabled} onClick={() => openDlg({ kind: "pos", nodeId: posSlot.nodeId })}>
                Edit
              </button>
            </div>
          ) : null}
          {negSlot ? (
            <div className="discovery-comfy-q-card">
              <div className="discovery-comfy-q-card-title">Negative prompt</div>
              <div className="discovery-comfy-q-card-summary">{_summarizePrompt(negSlot.text)}</div>
              <button type="button" className="discovery-comfy-q-edit" disabled={disabled} onClick={() => openDlg({ kind: "neg", nodeId: negSlot.nodeId })}>
                Edit
              </button>
            </div>
          ) : null}
          {extraClip.map((c) => (
            <div key={c.nodeId} className="discovery-comfy-q-card">
              <div className="discovery-comfy-q-card-title">CLIP text · node {c.nodeId}</div>
              <div className="discovery-comfy-q-card-summary">{_summarizePrompt(c.text)}</div>
              <button type="button" className="discovery-comfy-q-edit" disabled={disabled} onClick={() => openDlg({ kind: "prompt", nodeId: c.nodeId })}>
                Edit
              </button>
            </div>
          ))}
          {noiseSeed ? (
            <div className="discovery-comfy-q-card discovery-comfy-q-card-inline-controls">
              <div className="discovery-comfy-q-card-title">Seed</div>
              <div className="discovery-comfy-q-seed-inline-toolbar">
                <input
                  type="number"
                  className="discovery-comfy-q-num discovery-comfy-q-seed-inline-int"
                  min={0}
                  max={9007199254740991}
                  step={1}
                  disabled={disabled}
                  value={noiseSeed.seedValue}
                  onChange={(e) => {
                    const n = Number.parseInt(e.target.value, 10);
                    if (Number.isFinite(n)) {
                      sameSeedBaselineRef.current = n;
                      setPromptInput(noiseSeed.nodeId, noiseSeed.intKey, n, { ...QH, coalesce: true });
                    }
                  }}
                />
                <button
                  type="button"
                  className="discovery-comfy-q-seed-inline-btn"
                  disabled={disabled}
                  title="Repeat with pinned seed and fix after-run (pin follows this field; New does not move the pin until you edit the number)"
                  onClick={() => {
                    const v = sameSeedBaselineRef.current;
                    if (v == null || !Number.isFinite(v)) return;
                    setPromptInput(noiseSeed.nodeId, noiseSeed.intKey, Math.round(v), QH);
                    setPromptInput(noiseSeed.nodeId, "control_after_generate", "fixed", QH);
                  }}
                >
                  Same
                </button>
                <button
                  type="button"
                  className="discovery-comfy-q-seed-inline-btn discovery-comfy-q-seed-inline-btn-primary"
                  disabled={disabled}
                  title="Random seed and pin"
                  onClick={() => {
                    const n = _randomSeedInt() % Number.MAX_SAFE_INTEGER;
                    setPromptInput(noiseSeed.nodeId, noiseSeed.intKey, n, QH);
                    setPromptInput(noiseSeed.nodeId, "control_after_generate", "fixed", QH);
                  }}
                >
                  New
                </button>
              </div>
              <details className="discovery-comfy-q-seed-inline-details">
                <summary>Advanced</summary>
                <div className="discovery-comfy-q-seed-details-stack">
                  <div className="discovery-comfy-q-slider-row discovery-comfy-q-seed-numrow discovery-comfy-q-seed-numrow-tight">
                    <div className="discovery-comfy-q-slider-label">After run</div>
                    <select
                      className="discovery-comfy-q-num discovery-comfy-q-seed-field-span2"
                      disabled={disabled}
                      value={noiseSeed.control_after_generate}
                      onChange={(e) => setPromptInput(noiseSeed.nodeId, "control_after_generate", e.target.value, QH)}
                    >
                      {_CONTROL_AFTER_MODES.map((m) => (
                        <option key={m} value={m}>
                          {m}
                        </option>
                      ))}
                    </select>
                  </div>
                  {seedAuditRows.length > 0 ? (
                    <table className="discovery-comfy-q-seed-table">
                      <thead>
                        <tr>
                          <th>Node</th>
                          <th>Type</th>
                          <th>Key</th>
                          <th>Value</th>
                          <th>control_after_generate</th>
                        </tr>
                      </thead>
                      <tbody>
                        {seedAuditRows.map((r) => (
                          <tr key={`${r.nodeId}:${r.intKey}`}>
                            <td className="mono">{r.nodeId}</td>
                            <td className="mono">{r.classType}</td>
                            <td className="mono">{r.intKey}</td>
                            <td className="mono">{r.seedValue == null ? "(linked)" : String(r.seedValue)}</td>
                            <td className="mono">{r.control_after_generate ?? "-"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : null}
                </div>
              </details>
            </div>
          ) : null}
          {durationResolution ? (
            <DiscoveryDurationTranslatingCard
              promptDraft={promptDraft}
              resolution={durationResolution}
              fpsHint={durationEncodeFps}
              setPromptInput={setPromptInput}
              onSliderBurstEnd={onSliderBurstEnd}
              disabled={disabled}
            />
          ) : null}
          {sampler ? (
            <div className="discovery-comfy-q-card discovery-comfy-q-card-inline-controls">
              <div className="discovery-comfy-q-card-title">Sampler</div>
              <SliderNumRow
                label="CFG"
                value={sampler.cfg}
                min={1}
                max={30}
                step={0.5}
                disabled={disabled}
                onRangePointerUp={onSliderBurstEnd}
                onChange={(n, meta) => {
                  const w = sampler.cfgWrite ?? { nodeId: sampler.nodeId, inputKey: sampler.keys.cfg };
                  setPromptInput(w.nodeId, w.inputKey, n, { ...QH, ...meta });
                }}
              />
              <SliderNumRow
                label="Steps"
                value={sampler.steps}
                min={COMFY_KSAMPLER_STEPS_MIN}
                max={DISCOVERY_QUICK_EDIT_STEPS_MAX}
                step={COMFY_KSAMPLER_STEPS_STEP}
                intMode
                inputMin={COMFY_KSAMPLER_STEPS_MIN}
                inputMax={DISCOVERY_QUICK_EDIT_STEPS_MAX}
                disabled={disabled}
                onRangePointerUp={onSliderBurstEnd}
                onChange={(n, meta) => {
                  const w = sampler.stepsWrite ?? { nodeId: sampler.nodeId, inputKey: sampler.keys.steps };
                  setPromptInput(w.nodeId, w.inputKey, n, { ...QH, ...meta });
                }}
              />
              {sampler.keys.denoise ? (
                <SliderNumRow
                  label="Denoise"
                  value={sampler.denoise ?? 1}
                  min={0}
                  max={1}
                  step={0.01}
                  disabled={disabled}
                  onRangePointerUp={onSliderBurstEnd}
                  onChange={(n, meta) => {
                    const w = sampler.denoiseWrite ?? { nodeId: sampler.nodeId, inputKey: sampler.keys.denoise! };
                    setPromptInput(w.nodeId, w.inputKey, n, { ...QH, ...meta });
                  }}
                />
              ) : null}
            </div>
          ) : null}
          {speed ? (
            <div className="discovery-comfy-q-card">
              <div className="discovery-comfy-q-card-title">Speed (TeaCache)</div>
              <div className="discovery-comfy-q-card-summary mono">
                rel_l1_thresh {speed.rel_l1} · {speed.classType}
              </div>
              <button type="button" className="discovery-comfy-q-edit" disabled={disabled} onClick={() => openDlg({ kind: "speed" })}>
                Edit
              </button>
            </div>
          ) : null}
          {powerSlots.length || standardLoras.length ? (
            <div className="discovery-comfy-q-card">
              <div className="discovery-comfy-q-card-title">LoRA stack</div>
              <div className="discovery-comfy-q-card-summary">{loraSummary}</div>
              <button type="button" className="discovery-comfy-q-edit" disabled={disabled} onClick={() => openDlg({ kind: "lora" })}>
                Edit
              </button>
            </div>
          ) : null}
          {vhsLoader ? (
            <div className="discovery-comfy-q-card">
              <div className="discovery-comfy-q-card-title">Video load (VHS)</div>
              <div className="discovery-comfy-q-card-summary mono">
                Node {vhsLoader.nodeId} · {vhsLoader.classType}
                {vhsLoader.hasSkipFirst ? ` · skip ${vhsLoader.skip_first_frames}` : ""}
                {vhsLoader.hasFrameCap ? ` · cap ${vhsLoader.frame_load_cap}` : ""}
                {vhsLoader.hasForceRate ? ` · rate ${vhsLoader.force_rate}` : ""}
              </div>
              <button type="button" className="discovery-comfy-q-edit" disabled={disabled} onClick={() => openDlg({ kind: "vhsLoader" })}>
                Edit
              </button>
            </div>
          ) : null}
          {vhsCombine ? (
            <div className="discovery-comfy-q-card">
              <div className="discovery-comfy-q-card-title">Video output (VHS)</div>
              <div className="discovery-comfy-q-card-summary mono">
                Node {vhsCombine.nodeId} · fps {vhsCombine.frame_rate}
                {vhsCombine.hasSaveOut ? (vhsCombine.save_output ? " · save on" : " · save off") : ""}
              </div>
              <button type="button" className="discovery-comfy-q-edit" disabled={disabled} onClick={() => openDlg({ kind: "vhsOutput" })}>
                Edit
              </button>
            </div>
          ) : null}
        </div>
      )}
      {allConfigNotes.length > 0 ? (
        <details className="discovery-comfy-q-config-notes discovery-comfy-q-config-notes-accordion">
          <summary className="discovery-comfy-q-config-notes-summary">
            <span className="discovery-comfy-q-config-notes-summary-start">
              <span className="discovery-comfy-q-config-notes-caret" aria-hidden>
                ▶
              </span>
              <span className="discovery-comfy-q-config-notes-summary-label">Config notes</span>
            </span>
            <span className="discovery-comfy-q-config-notes-badge" aria-label={`${allConfigNotes.length} notes`}>
              {allConfigNotes.length}
            </span>
          </summary>
          <div className="discovery-comfy-q-config-notes-body">
            {allConfigNotes.map((n, i) => (
              <div key={i} className="discovery-comfy-q-config-note">
                <div className="discovery-comfy-q-config-note-section">{n.section}</div>
                <div className="discovery-comfy-q-config-note-message">{n.message}</div>
              </div>
            ))}
          </div>
        </details>
      ) : null}

      <dialog ref={dialogRef} className="discovery-comfy-q-dialog" onCancel={closeDlg}>
        <div
          className={
            "discovery-comfy-q-dlg-inner" + (dlg?.kind === "lora" ? " discovery-comfy-q-dlg-inner--lora" : "")
          }
        >
          {renderDialogBody()}
          <div className="discovery-comfy-q-dlg-actions">
            <button type="button" className="discovery-comfy-q-dlg-close" onClick={closeDlg}>
              Close
            </button>
          </div>
        </div>
      </dialog>
    </div>
  );
}
