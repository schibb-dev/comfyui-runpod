import React, { useCallback, useMemo, useState } from "react";

export type HintCalloutPlacement = "below" | "above";
export type HintCalloutAlign = "start" | "end";

export type HintCalloutProps = {
  text: string;
  show?: boolean;
  placement?: HintCalloutPlacement;
  align?: HintCalloutAlign;
  offsetPx?: number;
  maxWidthPx?: number;
  zIndex?: number;
  role?: "status" | "note";
  style?: React.CSSProperties;
};

/**
 * Floating hint callout (tooltip-like) that doesn't affect layout.
 *
 * Customize via props (placement/align/maxWidth/offset/style).
 * Render it inside a `position: relative` container.
 */
export function HintCallout({
  text,
  show,
  placement = "below",
  align = "start",
  offsetPx = 4,
  maxWidthPx = 360,
  zIndex = 60,
  role = "status",
  style,
}: HintCalloutProps) {
  const visible = show ?? Boolean(text);
  if (!visible) return null;

  const pos: React.CSSProperties =
    placement === "above"
      ? { bottom: `calc(100% + ${offsetPx}px)` }
      : { top: `calc(100% + ${offsetPx}px)` };

  const side: React.CSSProperties = align === "end" ? { right: 0 } : { left: 0 };

  return (
    <div
      role={role}
      aria-live="polite"
      style={{
        position: "absolute",
        ...side,
        ...pos,
        zIndex,
        maxWidth: maxWidthPx,
        padding: "6px 8px",
        borderRadius: 10,
        border: "1px solid rgba(255, 255, 255, 0.14)",
        background: "rgba(10, 15, 30, 0.72)",
        backdropFilter: "blur(10px)",
        color: "var(--muted)",
        fontSize: 11,
        lineHeight: 1.2,
        pointerEvents: "none",
        whiteSpace: "nowrap",
        overflow: "hidden",
        textOverflow: "ellipsis",
        ...style,
      }}
    >
      {text}
    </div>
  );
}

export type UseHintCalloutOpts = {
  /**
   * Extract hint text from an event target (hover/focus).
   * Defaults to reading `data-hint` then `aria-label` from the closest <button>.
   */
  getHintFromTarget?: (t: EventTarget | null) => string;
  /** Clear hint when pointer leaves container. Default true. */
  clearOnMouseLeave?: boolean;
};

export function defaultHintFromTarget(t: EventTarget | null): string {
  const el = (t as HTMLElement | null) ?? null;
  const btn = el?.closest?.("button") as HTMLButtonElement | null;
  if (!btn) return "";
  return btn.getAttribute("data-hint") || btn.getAttribute("aria-label") || "";
}

/**
 * Small helper to wire up "immediate" hover/focus hints for a group of buttons.
 * Returns props to spread on the group container + each item.
 */
export function useHintCallout(opts: UseHintCalloutOpts = {}) {
  const getHint = opts.getHintFromTarget ?? defaultHintFromTarget;
  const clearOnMouseLeave = opts.clearOnMouseLeave ?? true;
  const [text, setText] = useState<string>("");

  const onMouseEnterItem = useCallback(
    (e: React.MouseEvent) => {
      setText(getHint(e.target));
    },
    [getHint]
  );

  const containerProps = useMemo(() => {
    return {
      onMouseLeave: clearOnMouseLeave ? () => setText("") : undefined,
      onFocusCapture: (e: React.FocusEvent) => setText(getHint(e.target)),
      onBlurCapture: (e: React.FocusEvent) => {
        const next = e.relatedTarget as HTMLElement | null;
        if (next && (e.currentTarget as HTMLElement).contains(next)) return;
        setText("");
      },
    } as const;
  }, [clearOnMouseLeave, getHint]);

  const itemProps = useMemo(() => {
    return {
      onMouseEnter: onMouseEnterItem,
      onFocus: (e: React.FocusEvent) => setText(getHint(e.target)),
    } as const;
  }, [getHint, onMouseEnterItem]);

  return { text, setText, containerProps, itemProps } as const;
}

