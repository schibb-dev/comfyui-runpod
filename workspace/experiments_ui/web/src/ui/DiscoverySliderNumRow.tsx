import React, { useEffect, useState } from "react";
import type { SetPromptInputMeta } from "./usePromptDraftHistory";

export function SliderNumRow({
  label,
  value,
  min,
  max,
  step,
  disabled,
  onChange,
  onRangePointerUp,
  intMode,
  inputMin,
  inputMax,
  /** LoRA dialog: label + range + value on one tight row (range shares row with number, not full-width alone). */
  compactInline,
  /** When `label` is empty (e.g. LoRA rows), exposed on the range input for a11y. */
  ariaLabel,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  disabled?: boolean;
  intMode?: boolean;
  onChange: (n: number, meta?: SetPromptInputMeta) => void;
  /** End coalesced undo burst after a range drag (see usePromptDraftHistory). */
  onRangePointerUp?: () => void;
  /** Optional HTML `min` / `max` on the number input (e.g. Comfy widget bounds). */
  inputMin?: number;
  inputMax?: number;
  compactInline?: boolean;
  ariaLabel?: string;
}) {
  const [text, setText] = useState(() => String(intMode ? Math.round(value) : value));
  useEffect(() => {
    setText(String(intMode ? Math.round(value) : value));
  }, [value, intMode]);
  const compactNoLabel = Boolean(compactInline && label.trim() === "");
  const rangeProps = {
    type: "range" as const,
    className: "discovery-comfy-q-range" + (compactInline ? " discovery-comfy-q-range--compact" : ""),
    disabled,
    min,
    max,
    step,
    ...(compactNoLabel && ariaLabel ? ({ "aria-label": ariaLabel } as const) : {}),
    value: Math.min(max, Math.max(min, value)),
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => {
      const raw = Number.parseFloat(e.target.value);
      const n = intMode ? Math.round(raw) : raw;
      if (Number.isFinite(n)) onChange(n, { coalesce: true });
    },
    onPointerUp: () => onRangePointerUp?.(),
    onPointerCancel: () => onRangePointerUp?.(),
  };
  const numProps = {
    type: "number" as const,
    className:
      "discovery-comfy-q-num discovery-comfy-q-slider-num" + (compactInline ? " discovery-comfy-q-slider-num--compact" : ""),
    disabled,
    step,
    ...(inputMin != null && Number.isFinite(inputMin) ? { min: inputMin } : {}),
    ...(inputMax != null && Number.isFinite(inputMax) ? { max: inputMax } : {}),
    value: text,
    ...(compactNoLabel && ariaLabel ? ({ "aria-label": `${ariaLabel} (value)` } as const) : {}),
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => setText(e.target.value),
    onBlur: () => {
      const raw = intMode ? Number.parseInt(text, 10) : Number.parseFloat(text);
      if (!Number.isFinite(raw)) {
        setText(String(intMode ? Math.round(value) : value));
        return;
      }
      const clamped = Math.min(max, Math.max(min, raw));
      onChange(intMode ? Math.round(clamped) : clamped);
      setText(String(intMode ? Math.round(clamped) : clamped));
    },
  };
  if (compactInline) {
    const showLabel = label.trim().length > 0;
    return (
      <div
        className={
          "discovery-comfy-q-slider-row discovery-comfy-q-slider-row--compact-inline" +
          (showLabel ? "" : " discovery-comfy-q-slider-row--compact-inline-no-label")
        }
      >
        {showLabel ? <div className="discovery-comfy-q-slider-label">{label}</div> : null}
        <div className="discovery-comfy-q-slider-inline-controls">
          <input {...rangeProps} />
          <input {...numProps} />
        </div>
      </div>
    );
  }
  return (
    <div className="discovery-comfy-q-slider-row">
      <div className="discovery-comfy-q-slider-label">{label}</div>
      <input {...rangeProps} />
      <input {...numProps} />
    </div>
  );
}
