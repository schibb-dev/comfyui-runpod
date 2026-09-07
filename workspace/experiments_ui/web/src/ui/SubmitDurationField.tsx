import React, { useEffect, useState } from "react";
import {
  SUBMIT_FRAMES_MAX,
  SUBMIT_FRAMES_MIN,
  SUBMIT_GEN_FPS,
  clampSubmitFrames,
  framesToSubmitSeconds,
  secondsToSubmitFrames,
} from "./submitDuration";

export function SubmitDurationField({
  frames,
  seedFrames,
  disabled,
  onChange,
}: {
  frames: number | null;
  seedFrames?: number | null;
  disabled?: boolean;
  onChange: (frames: number | null) => void;
}) {
  const effective = frames ?? seedFrames ?? null;
  const [secondsText, setSecondsText] = useState(() =>
    effective != null ? framesToSubmitSeconds(effective).toFixed(2) : "",
  );

  useEffect(() => {
    if (effective == null) {
      setSecondsText("");
      return;
    }
    setSecondsText(framesToSubmitSeconds(effective).toFixed(2));
  }, [effective]);

  const commitResolved = (next: number | null) => {
    if (next != null && seedFrames != null && next === seedFrames) {
      onChange(null);
      return;
    }
    onChange(next);
  };

  const commitFrames = (raw: string) => {
    const t = raw.trim();
    if (!t) {
      commitResolved(null);
      return;
    }
    const n = Number(t);
    if (!Number.isFinite(n)) return;
    commitResolved(clampSubmitFrames(n));
  };

  const commitSeconds = (raw: string) => {
    const t = raw.trim();
    if (!t) {
      commitResolved(null);
      return;
    }
    const n = Number(t);
    if (!Number.isFinite(n) || n <= 0) return;
    commitResolved(secondsToSubmitFrames(n));
  };

  return (
    <div className="submit-composer__tunables" role="group" aria-label="Tunable parameters">
      <span className="work-product-quick-queue__family-label" title="Recipe knobs for this run. Empty uses the family or variant template.">
        Tunables
      </span>
      <div className="submit-composer__duration" role="group" aria-label="Generation duration">
        <span className="submit-composer__duration-label">Duration</span>
        <label className="submit-composer__duration-field">
          <input
            type="number"
            inputMode="numeric"
            min={SUBMIT_FRAMES_MIN}
            max={SUBMIT_FRAMES_MAX}
            step={1}
            disabled={disabled}
            value={frames ?? seedFrames ?? ""}
            placeholder={seedFrames != null ? String(seedFrames) : "default"}
            aria-label="Generation frames"
            title="Wan output length in frames. Shows the family or variant default until you change it."
            onChange={(e) => commitFrames(e.target.value)}
          />
          <span>frames</span>
        </label>
        <label className="submit-composer__duration-field">
          <input
            type="number"
            inputMode="decimal"
            min={0.1}
            max={framesToSubmitSeconds(SUBMIT_FRAMES_MAX)}
            step={0.25}
            disabled={disabled}
            value={secondsText}
            placeholder={seedFrames != null ? framesToSubmitSeconds(seedFrames).toFixed(2) : "—"}
            aria-label="Generation seconds"
            title={`Wall-clock length at ${SUBMIT_GEN_FPS} fps encode. Empty uses the family or variant template.`}
            onChange={(e) => setSecondsText(e.target.value)}
            onBlur={(e) => commitSeconds(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                commitSeconds((e.target as HTMLInputElement).value);
              }
            }}
          />
          <span>s @{SUBMIT_GEN_FPS}fps</span>
        </label>
      </div>
    </div>
  );
}
