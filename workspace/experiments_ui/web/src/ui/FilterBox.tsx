import React from "react";

export type FilterBoxProps = {
  /** Optional left-side label/adornment (e.g. "Filter"). */
  left?: React.ReactNode;

  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
  ariaLabel?: string;

  /** Optional right-side adornment (e.g. extra buttons). */
  right?: React.ReactNode;

  /** If true, show a clear (X) icon when value is non-empty. */
  showClear?: boolean;
  onClear?: () => void;
  clearAriaLabel?: string;
  clearTitle?: string;
};

export function FilterBox({
  left,
  value,
  onChange,
  placeholder,
  ariaLabel,
  right,
  showClear,
  onClear,
  clearAriaLabel,
  clearTitle,
}: FilterBoxProps) {
  const canClear = Boolean((value ?? "").trim());
  const shouldShowClear = Boolean(showClear && canClear);

  return (
    <div className="row" style={{ margin: 0 }}>
      {left ? <div style={{ flex: "0 0 auto" }}>{left}</div> : null}
      <div style={{ flex: "1 1 auto", minWidth: 0 }}>
        <input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          aria-label={ariaLabel ?? placeholder ?? "Filter"}
          type="text"
        />
      </div>

      {shouldShowClear ? (
        <button
          type="button"
          className="icon-btn"
          onClick={() => onClear?.()}
          aria-label={clearAriaLabel ?? "Clear filter"}
          title={clearTitle ?? "Clear filter"}
        >
          <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false">
            <path
              d="M6 6l12 12M18 6L6 18"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
            />
          </svg>
        </button>
      ) : null}

      {right}
    </div>
  );
}

