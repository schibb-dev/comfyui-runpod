import React, { useCallback, useEffect, useState } from "react";
import { SliderNumRow } from "./DiscoverySliderNumRow";
import type { ComfyPromptMap } from "./discoveryDurationControl";
import {
  DISCOVERY_DURATION_FRAMES_MAX,
  DISCOVERY_DURATION_FRAMES_MIN,
  type DurationControlResolution,
  framesToSeconds,
  readDurationNativeFrames,
  secondsToFrames,
  writeDurationNativeFrames,
} from "./discoveryDurationControl";
import type { SetPromptInputMeta } from "./usePromptDraftHistory";

const QH = { recordHistory: true } satisfies SetPromptInputMeta;

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
  /** Literal `frame_rate` from a `VHS_VideoCombine` in the prompt, if any; enables seconds field. */
  fpsHint: number | null;
  setPromptInput: (nodeId: string, inputKey: string, value: unknown, meta?: SetPromptInputMeta) => void;
  onSliderBurstEnd?: () => void;
  disabled?: boolean;
}) {
  const { surface, nativeLabel } = resolution;
  const framesRead = readDurationNativeFrames(promptDraft, surface);
  const frames = framesRead ?? DISCOVERY_DURATION_FRAMES_MIN;

  const [secondsText, setSecondsText] = useState(() =>
    fpsHint != null && Number.isFinite(fpsHint) && fpsHint > 0
      ? framesToSeconds(frames, fpsHint).toFixed(3)
      : "",
  );

  useEffect(() => {
    if (fpsHint == null || !Number.isFinite(fpsHint) || fpsHint <= 0) {
      setSecondsText("");
      return;
    }
    const s = framesToSeconds(frames, fpsHint);
    setSecondsText(Number.isFinite(s) ? s.toFixed(3) : "");
  }, [frames, fpsHint]);

  const applyFrames = useCallback(
    (n: number, meta?: SetPromptInputMeta) => {
      writeDurationNativeFrames(promptDraft, surface, n, setPromptInput, { ...QH, ...meta });
    },
    [promptDraft, surface, setPromptInput],
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

  return (
    <div className="discovery-comfy-q-card discovery-comfy-q-card-inline-controls">
      <div className="discovery-comfy-q-card-title">Duration</div>
      <div className="discovery-comfy-q-card-summary">
        <span className="mono">{nativeLabel}</span>
      </div>
      <p className="discovery-comfy-q-inline-hint">
        Stored in graph: frame count (native). Seconds are derived only when encode FPS is known from a literal{" "}
        <span className="mono">VHS_VideoCombine.frame_rate</span> in this prompt.
      </p>
      <SliderNumRow
        label="Frames (native)"
        value={frames}
        min={DISCOVERY_DURATION_FRAMES_MIN}
        max={DISCOVERY_DURATION_FRAMES_MAX}
        step={1}
        intMode
        inputMin={DISCOVERY_DURATION_FRAMES_MIN}
        inputMax={DISCOVERY_DURATION_FRAMES_MAX}
        disabled={disabled}
        onRangePointerUp={onSliderBurstEnd}
        onChange={(n, meta) => applyFrames(n, meta)}
      />
      {fpsHint != null && Number.isFinite(fpsHint) && fpsHint > 0 ? (
        <div className="discovery-comfy-q-slider-row">
          <div className="discovery-comfy-q-slider-label">Seconds (derived)</div>
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
                const s = framesToSeconds(frames, fpsHint);
                setSecondsText(Number.isFinite(s) ? s.toFixed(3) : "");
                return;
              }
              applyFrames(secondsToFrames(raw, fpsHint));
            }}
          />
          <span className="discovery-comfy-q-inline-hint" style={{ alignSelf: "center", marginLeft: 8 }}>
            at {fpsHint} fps
          </span>
        </div>
      ) : (
        <p className="discovery-comfy-q-inline-hint">No literal encode FPS in this prompt; seconds field is hidden.</p>
      )}
    </div>
  );
}
