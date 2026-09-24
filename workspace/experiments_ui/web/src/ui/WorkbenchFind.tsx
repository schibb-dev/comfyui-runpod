import React from "react";
import { APPETITE_FILTER_KEYS, APPETITE_FILTER_LABEL, type AppetiteFilterKey } from "./workProductAppetite";
import {
  JOB_OVERRIDE_KINDS,
  JOB_OVERRIDE_LABEL,
  type OverrideNeed,
} from "./jobOverrides";

const FILTER_CHIP_DBLCLICK_HINT = "double-click to show only this · again to show all";

function statusFilterVisual(status: string): string {
  const s = status.toLowerCase();
  if (s === "running") return "running";
  if (s === "queued" || s === "submitted") return "queued";
  if (s === "editing") return "editing";
  if (s === "error" || s === "failed") return "error";
  if (s === "interrupted") return "interrupted";
  if (s === "complete" || s === "deposited") return "ok";
  if (s === "abandoned" || s === "unknown") return "muted";
  return "pending";
}

function markerFilterVisual(marker: string): string {
  const s = marker.toLowerCase();
  if (s === "extend") return "extend";
  if (s === "replay") return "replay";
  if (s === "derive" || s === "predicted_derive" || s === "predicted") return "derive";
  if (s === "product") return "ok";
  if (s === "other" || s === "unset") return "muted";
  return "pending";
}

function chipClass(visual: string, on: boolean): string {
  return `work-products-status-toggle work-products-status-toggle--${visual}${on ? " is-on" : " is-off"}`;
}

export type WorkbenchFindChip = { id: string; label: string; count: number; on: boolean };

export function WorkbenchFindPanel({
  variant,
  nameQuery,
  onNameQuery,
  searchPlaceholder,
  followUpSet,
  hourlyOnly,
  onHourlyOnly,
  showStillTags,
  stillTagCount,
  onToggleStillTags,
  markers,
  onToggleMarker,
  onFocusMarker,
  appetites,
  onToggleAppetite,
  onFocusAppetite,
  statuses,
  onToggleStatus,
  onFocusStatus,
  overrideNeed,
  onToggleOverride,
  onFocusOverride,
  families,
  familyFilter,
  onFamilyFilter,
  advancedOpen,
  onAdvancedOpen,
  onClear,
  activeSummary,
  workingSet,
}: {
  variant: "rail" | "sheet" | "bar";
  nameQuery: string;
  onNameQuery: (next: string) => void;
  searchPlaceholder: string;
  followUpSet: boolean;
  hourlyOnly: boolean;
  onHourlyOnly: () => void;
  showStillTags: boolean;
  stillTagCount: number;
  onToggleStillTags: () => void;
  markers: WorkbenchFindChip[];
  onToggleMarker: (id: string) => void;
  onFocusMarker: (id: string) => void;
  appetites: WorkbenchFindChip[];
  onToggleAppetite: (id: AppetiteFilterKey) => void;
  onFocusAppetite: (id: AppetiteFilterKey) => void;
  statuses: WorkbenchFindChip[];
  onToggleStatus: (id: string) => void;
  onFocusStatus: (id: string) => void;
  overrideNeed: Set<OverrideNeed>;
  onToggleOverride: (id: OverrideNeed) => void;
  onFocusOverride: (id: OverrideNeed) => void;
  families: string[];
  familyFilter: string;
  onFamilyFilter: (next: string) => void;
  advancedOpen: boolean;
  onAdvancedOpen: (open: boolean) => void;
  onClear: () => void;
  activeSummary: string;
  workingSet?: React.ReactNode;
}) {
  const search = (
    <label className="work-products-search work-products-find__search">
      <span className="work-products-search__label">Find</span>
      <input
        type="search"
        value={nameQuery}
        onChange={(e) => onNameQuery(e.target.value)}
        placeholder={searchPlaceholder}
        aria-label="Filter work products by name"
      />
    </label>
  );

  const chips = (
    <div className="work-products-status-filters work-products-find__chips" role="group" aria-label="Work product filters">
      <button
        type="button"
        className={`work-products-status-toggle work-products-status-toggle--hourly${
          hourlyOnly && !followUpSet ? " is-on" : " is-off"
        }`}
        aria-pressed={hourlyOnly && !followUpSet}
        disabled={followUpSet}
        title={
          followUpSet
            ? "Hourly filter does not apply to follow-up piles"
            : hourlyOnly
              ? "Hourly only — click to show all jobs"
              : "Showing all jobs — click for hourly only"
        }
        onClick={onHourlyOnly}
      >
        <span className="work-products-status-toggle__label">hourly only</span>
      </button>
      <button
        type="button"
        className={chipClass("muted", showStillTags)}
        aria-pressed={showStillTags}
        disabled={followUpSet}
        title={
          followUpSet
            ? "Still-tag filter does not apply to follow-up piles"
            : showStillTags
              ? `Showing still tags (${stillTagCount}) — click to hide`
              : `Still tags hidden (${stillTagCount}) — click to show`
        }
        onClick={onToggleStillTags}
      >
        <span className="work-products-status-toggle__label">still tags</span>
        <span className="work-products-status-toggle__count">{stillTagCount}</span>
      </button>
      {markers.length ? (
        <>
          <span className="work-products-status-filters__sep" aria-hidden="true" />
          <div className="work-products-status-filters__group" role="group" aria-label="Filter by pick mode">
            {markers.map((marker) => (
              <button
                key={`marker-${marker.id}`}
                type="button"
                className={chipClass(markerFilterVisual(marker.id), marker.on)}
                aria-pressed={marker.on}
                title={
                  marker.on
                    ? `Showing ${marker.label} (${marker.count}) — click to hide · ${FILTER_CHIP_DBLCLICK_HINT}`
                    : `Hidden ${marker.label} (${marker.count}) — click to show · ${FILTER_CHIP_DBLCLICK_HINT}`
                }
                onClick={() => onToggleMarker(marker.id)}
                onDoubleClick={(e) => {
                  e.preventDefault();
                  onFocusMarker(marker.id);
                }}
              >
                <span className="work-products-status-toggle__label">{marker.label}</span>
                <span className="work-products-status-toggle__count">{marker.count}</span>
              </button>
            ))}
          </div>
        </>
      ) : null}
      <span className="work-products-status-filters__sep" aria-hidden="true" />
      <div className="work-products-status-filters__group" role="group" aria-label="Filter by appetite">
        {APPETITE_FILTER_KEYS.map((key) => {
          const chip = appetites.find((a) => a.id === key);
          const on = chip?.on ?? true;
          const count = chip?.count || 0;
          const label = APPETITE_FILTER_LABEL[key];
          return (
            <button
              key={`appetite-${key}`}
              type="button"
              className={chipClass(`appetite-${key}`, on)}
              aria-pressed={on}
              title={
                on
                  ? `Showing ${label} (${count}) — click to hide · ${FILTER_CHIP_DBLCLICK_HINT}`
                  : `Hidden ${label} (${count}) — click to show · ${FILTER_CHIP_DBLCLICK_HINT}`
              }
              onClick={() => onToggleAppetite(key)}
              onDoubleClick={(e) => {
                e.preventDefault();
                onFocusAppetite(key);
              }}
            >
              <span className="work-products-status-toggle__label">{label}</span>
              <span className="work-products-status-toggle__count">{count}</span>
            </button>
          );
        })}
      </div>
      {statuses.length ? (
        <>
          <span className="work-products-status-filters__sep" aria-hidden="true" />
          <div className="work-products-status-filters__group" role="group" aria-label="Filter by job status">
            {statuses.map((status) => (
              <button
                key={`status-${status.id}`}
                type="button"
                className={chipClass(statusFilterVisual(status.id), status.on)}
                aria-pressed={status.on}
                title={
                  status.on
                    ? `Showing ${status.id} (${status.count}) — click to hide · ${FILTER_CHIP_DBLCLICK_HINT}`
                    : `Hidden ${status.id} (${status.count}) — click to show · ${FILTER_CHIP_DBLCLICK_HINT}`
                }
                onClick={() => onToggleStatus(status.id)}
                onDoubleClick={(e) => {
                  e.preventDefault();
                  onFocusStatus(status.id);
                }}
              >
                <span className="work-products-status-toggle__label">{status.id}</span>
                <span className="work-products-status-toggle__count">{status.count}</span>
              </button>
            ))}
          </div>
        </>
      ) : null}
    </div>
  );

  const chipFilterOn =
    (hourlyOnly && !followUpSet) ||
    (showStillTags && !followUpSet) ||
    markers.some((m) => !m.on) ||
    APPETITE_FILTER_KEYS.some((key) => !(appetites.find((a) => a.id === key)?.on ?? true)) ||
    statuses.some((s) => !s.on);

  const filters = (
    <details className="work-products-find__filters">
      <summary>
        Filters
        {chipFilterOn ? <span className="work-products-find__filters-on"> on</span> : null}
      </summary>
      {chips}
    </details>
  );

  const overrideChips: Array<{ id: OverrideNeed; label: string }> = [
    { id: "any", label: "any override" },
    ...JOB_OVERRIDE_KINDS.map((id) => ({ id, label: JOB_OVERRIDE_LABEL[id] })),
  ];

  const advanced = (
    <details
      className="work-products-find__advanced"
      open={advancedOpen}
      onToggle={(e) => onAdvancedOpen((e.currentTarget as HTMLDetailsElement).open)}
    >
      <summary>Advanced</summary>
      <div className="work-products-find__advanced-body">
        <div className="work-products-status-filters__group" role="group" aria-label="Filter by override">
          {overrideChips.map((chip) => {
            const on = overrideNeed.has(chip.id);
            return (
              <button
                key={`ovr-${chip.id}`}
                type="button"
                className={chipClass(chip.id === "any" ? "editing" : "pending", on)}
                aria-pressed={on}
                title={
                  on
                    ? `Showing ${chip.label} — click to clear · ${FILTER_CHIP_DBLCLICK_HINT}`
                    : `Not filtering by ${chip.label} — click to require · ${FILTER_CHIP_DBLCLICK_HINT}`
                }
                onClick={() => onToggleOverride(chip.id)}
                onDoubleClick={(e) => {
                  e.preventDefault();
                  onFocusOverride(chip.id);
                }}
              >
                <span className="work-products-status-toggle__label">{chip.label}</span>
              </button>
            );
          })}
        </div>
        {families.length > 1 ? (
          <label className="work-products-limit work-products-find__family">
            Family
            <select
              value={familyFilter}
              onChange={(e) => onFamilyFilter(e.target.value)}
              aria-label="Filter work products by family"
            >
              <option value="">All families</option>
              {families.map((fam) => (
                <option key={fam} value={fam}>
                  {fam}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </div>
    </details>
  );

  return (
    <div className={`work-products-find work-products-find--${variant}`} aria-label="Find jobs">
      {workingSet ? <div className="work-products-find__working-set">{workingSet}</div> : null}
      <div className="work-products-find__bar">
        {search}
        {activeSummary ? (
          <span className="work-products-find__active" title={activeSummary}>
            {activeSummary}
          </span>
        ) : null}
        {activeSummary ? (
          <button type="button" className="work-products-find__clear" onClick={onClear}>
            Clear
          </button>
        ) : null}
      </div>
      {variant !== "bar" ? filters : null}
      {variant !== "bar" ? advanced : null}
    </div>
  );
}
