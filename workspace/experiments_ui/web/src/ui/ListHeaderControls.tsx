import React from "react";
import { FilterBox } from "./FilterBox";

export type ListHeaderControlsProps = {
  filterValue: string;
  onFilterChange: (next: string) => void;
  filterPlaceholder?: string;
  filterAriaLabel?: string;
  filterRight?: React.ReactNode;
  filterShowClear?: boolean;
  filterOnClear?: () => void;
  filterClearTitle?: string;
  filterClearAriaLabel?: string;

  actionsLeft?: React.ReactNode;
  actionsRight?: React.ReactNode;

  /** Use to tighten vertical space (default true). */
  compact?: boolean;
};

/**
 * Reusable "list header" controls:
 * - filter input row (with optional right-side adornment, e.g. clear button)
 * - actions/status row (left cluster + right status text)
 *
 * Styling is intentionally lightweight and mostly inline so callsites can customize freely.
 */
export function ListHeaderControls({
  filterValue,
  onFilterChange,
  filterPlaceholder,
  filterAriaLabel,
  filterRight,
  filterShowClear,
  filterOnClear,
  filterClearTitle,
  filterClearAriaLabel,
  actionsLeft,
  actionsRight,
  compact = true,
}: ListHeaderControlsProps) {
  const rowMargin = compact ? 0 : undefined;
  const gap = compact ? 8 : 10;

  return (
    <div style={{ display: "grid", gap: compact ? 8 : 10 }}>
      <div style={{ margin: rowMargin }}>
        <FilterBox
          value={filterValue}
          onChange={onFilterChange}
          placeholder={filterPlaceholder}
          ariaLabel={filterAriaLabel}
          right={filterRight}
          showClear={filterShowClear}
          onClear={filterOnClear}
          clearTitle={filterClearTitle}
          clearAriaLabel={filterClearAriaLabel}
        />
      </div>

      {actionsLeft || actionsRight ? (
        <div className="row" style={{ margin: rowMargin, justifyContent: "space-between", alignItems: "flex-start" }}>
          <div style={{ display: "flex", gap, flexWrap: "wrap", alignItems: "center" }}>{actionsLeft}</div>
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", color: "var(--muted)", fontSize: 12 }}>
            {actionsRight}
          </div>
        </div>
      ) : null}
    </div>
  );
}

