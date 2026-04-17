import React, { useCallback, useEffect, useState } from "react";
import { SliderNumRow } from "./DiscoverySliderNumRow";
import type { ComfyPromptMap } from "./discoveryDurationControl";
import {
  DISCOVERY_DURATION_FRAMES_MAX,
  DISCOVERY_DURATION_FRAMES_MIN,
  type DurationControlResolution,
  framesToSeconds,
  readDurationApproxWanFrames,
  readDurationNativeFrames,
  secondsToFrames,
  writeDurationNativeFrames,
  writeDurationSliderNative,
} from "./discoveryDurationControl";
import type { SetPromptInputMeta } from "./usePromptDraftHistory";

const QH = { recordHistory: true } satisfies SetPromptInputMeta;

const PEELED_FLOAT_SLIDER_MAX = 512;

export function DiscoveryDurationTranslatingCard({
  promptDraft,
  resolution,
  fpsHint,
  setPromptInput,
  onSliderBurstEnd,
  disabled,
}: {
  promptDraft: ComfyPromptMap;
  resolution: DurationControlResolution;
  /** Literal `frame_rate` from a `VHS_VideoCombine` in the prompt, if any; enables seconds field when semantics allow. */
  fpsHint: number | null;
  setPromptInput: (nodeId: string, inputKey: string, value: unknown, meta?: SetPromptInputMeta) => void;
  onSliderBurstEnd?: () => void;
  disabled?: boolean;
}) {
  const { surface, nativeLabel } = resolution;
  const isPeeled = surface.kind === "peeled_linear_chain";
  const hasFps = fpsHint != null && Number.isFinite(fpsHint) && fpsHint > 0;
  /** Without encode FPS we only expose the raw mxSlider value for peeled chains (no implied wall-clock / output semantics). */
  const primaryIsSliderNative = isPeeled && !hasFps;
  const useFloatSlider = isPeeled && surface.mxSliderUseFloat;

  const primaryValue = primaryIsSliderNative
    ? readDurationNativeFrames(promptDraft, surface) ?? 0
    : (readDurationApproxWanFrames(promptDraft, surface) ?? DISCOVERY_DURATION_FRAMES_MIN);

  const approxWanForHint = readDurationApproxWanFrames(promptDraft, surface);

  const framesForSeconds = isPeeled ? (approxWanForHint ?? DISCOVERY_DURATION_FRAMES_MIN) : primaryValue;

  const [secondsText, setSecondsText] = useState(() =>
    hasFps ? framesToSeconds(framesForSeconds, fpsHint!).toFixed(3) : "",
  );

  useEffect(() => {
    if (!hasFps) {
      setSecondsText("");
      return;
    }
    const s = framesToSeconds(framesForSeconds, fpsHint!);
    setSecondsText(Number.isFinite(s) ? s.toFixed(3) : "");
  }, [framesForSeconds, fpsHint, hasFps]);

  const applyPrimary = useCallback(
    (n: number, meta?: SetPromptInputMeta) => {
      if (primaryIsSliderNative) {
        writeDurationSliderNative(promptDraft, surface, n, setPromptInput, { ...QH, ...meta });
        return;
      }
      writeDurationNativeFrames(promptDraft, surface, n, setPromptInput, { ...QH, ...meta });
    },
    [promptDraft, surface, setPromptInput, primaryIsSliderNative],
  );

  if (surface.kind === "unsupported") {
    return (
      <div className="discovery-comfy-q-card discovery-comfy-q-card-inline-controls">
        <div className="discovery-comfy-q-card-title">Duration</div>
        <div className="discovery-comfy-q-card-summary mono">{nativeLabel}</div>
        <p className="discovery-comfy-q-inline-hint">{surface.reason}</p>
        <p className="discovery-comfy-q-inline-hint">Adjust length on the graph in Comfy for this wiring pattern.</p>
      </div>
    );
  }

  const primaryLabel = primaryIsSliderNative
    ? useFloatSlider
      ? "mxSlider value (graph native)"
      : "mxSlider value (graph native, int)"
    : isPeeled
      ? "Wan length (est., frames)"
      : "Frames (native)";

  const primaryMin = primaryIsSliderNative ? (useFloatSlider ? 0 : DISCOVERY_DURATION_FRAMES_MIN) : DISCOVERY_DURATION_FRAMES_MIN;
  const primaryMax = primaryIsSliderNative
    ? useFloatSlider
      ? PEELED_FLOAT_SLIDER_MAX
      : DISCOVERY_DURATION_FRAMES_MAX
    : DISCOVERY_DURATION_FRAMES_MAX;
  const primaryStep = primaryIsSliderNative && useFloatSlider ? 0.05 : 1;
  const primaryIntMode = !(primaryIsSliderNative && useFloatSlider);

  return (
    <div className="discovery-comfy-q-card discovery-comfy-q-card-inline-controls">
      <div className="discovery-comfy-q-card-title">Duration</div>
      <div className="discovery-comfy-q-card-summary">
        <span className="mono">{nativeLabel}</span>
      </div>
      {isPeeled ? (
        <p className="discovery-comfy-q-inline-hint">
          Seconds and wall-clock meaning need a literal <span className="mono">VHS_VideoCombine.frame_rate</span> and a clear mapping through the math chain; see hints below.
        </p>
      ) : (
        <p className="discovery-comfy-q-inline-hint">
          Stored in graph: frame count (native). Seconds are derived only when encode FPS is known from a literal{" "}
          <span className="mono">VHS_VideoCombine.frame_rate</span> in this prompt.
        </p>
      )}
      {isPeeled ? (
        <p className="discovery-comfy-q-inline-hint">
          Length passes through easy math / convert nodes before Wan; Wan-side values are estimates from the same math this panel applies to API inputs (Comfy rounding can still differ).
        </p>
      ) : null}
      {primaryIsSliderNative ? (
        <p className="discovery-comfy-q-inline-hint">
          No literal encode FPS in this prompt: edit the upstream mxSlider value only. We do not infer wall-clock time or final video length from this number alone.
        </p>
      ) : null}
      {isPeeled && hasFps && approxWanForHint != null ? (
        <p className="discovery-comfy-q-inline-hint">Wan length (chain estimate): {approxWanForHint} frames (used for seconds below).</p>
      ) : null}
      <SliderNumRow
        label={primaryLabel}
        value={primaryValue}
        min={primaryMin}
        max={primaryMax}
        step={primaryStep}
        intMode={primaryIntMode}
        inputMin={primaryMin}
        inputMax={primaryMax}
        disabled={disabled}
        onRangePointerUp={onSliderBurstEnd}
        onChange={(n, meta) => applyPrimary(n, meta)}
      />
      {hasFps && !primaryIsSliderNative ? (
        <div className="discovery-comfy-q-slider-row">
          <div className="discovery-comfy-q-slider-label">Seconds {isPeeled ? "(from est. Wan frames)" : "(derived)"}</div>
          <input
            type="number"
            className="discovery-comfy-q-num discovery-comfy-q-slider-num"
            disabled={disabled}
            min={0}
            step={0.001}
            value={secondsText}
            onChange={(e) => setSecondsText(e.target.value)}
            onBlur={() => {
              const raw = Number.parseFloat(secondsText);
              if (!Number.isFinite(raw) || raw < 0) {
                const s = framesToSeconds(framesForSeconds, fpsHint!);
                setSecondsText(Number.isFinite(s) ? s.toFixed(3) : "");
                return;
              }
              applyPrimary(secondsToFrames(raw, fpsHint!));
            }}
          />
          <span className="discovery-comfy-q-inline-hint" style={{ alignSelf: "center", marginLeft: 8 }}>
            at {fpsHint} fps
          </span>
        </div>
      ) : hasFps && primaryIsSliderNative ? (
        <p className="discovery-comfy-q-inline-hint">
          Seconds field hidden: with a peeled math chain, set encode FPS and reload this draft to target Wan length (and seconds) instead of raw mxSlider.
        </p>
      ) : (
        <p className="discovery-comfy-q-inline-hint">No literal encode FPS in this prompt; seconds field is hidden.</p>
      )}
    </div>
  );
}
