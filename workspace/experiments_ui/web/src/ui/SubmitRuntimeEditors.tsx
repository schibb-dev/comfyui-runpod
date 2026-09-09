import React from "react";
import type { WorkProductLoraEntry, WorkProductParamsValues } from "./types";
import { loraBasename } from "./submitRuntime";

const EXTRA_PARAM_FIELDS: Array<{ key: keyof WorkProductParamsValues; label: string; title: string }> = [
  { key: "steps", label: "Steps", title: "Sampler steps for this run. Empty uses the family template." },
  { key: "overlap", label: "Overlap", title: "Extend overlap frames. Empty uses the family template." },
  { key: "seed", label: "Seed", title: "Comfy noise seed. Empty uses the family template / new seed." },
];

export function SubmitParamsExtras({
  draft,
  seed,
  disabled,
  onChange,
}: {
  draft: WorkProductParamsValues;
  seed?: WorkProductParamsValues | null;
  disabled?: boolean;
  onChange: (next: WorkProductParamsValues) => void;
}) {
  const setField = (key: keyof WorkProductParamsValues, raw: string) => {
    const next = { ...draft };
    if (!raw.trim()) {
      delete next[key];
    } else {
      const n = Number(raw);
      if (!Number.isFinite(n)) return;
      next[key] = Math.trunc(n);
    }
    onChange(next);
  };

  return (
    <div className="submit-composer__param-extras" role="group" aria-label="Runtime parameters">
      {EXTRA_PARAM_FIELDS.map(({ key, label, title }) => {
        const jobVal = draft[key] ?? seed?.[key];
        const changed = draft[key] != null && seed?.[key] != null && draft[key] !== seed?.[key];
        return (
          <label
            key={key}
            className={"submit-composer__duration-field" + (changed ? " is-diff" : "")}
            title={title}
          >
            <span className="submit-composer__duration-label">{label}</span>
            <input
              type="number"
              inputMode="numeric"
              step={1}
              disabled={disabled}
              value={jobVal ?? ""}
              placeholder={seed?.[key] != null ? String(seed[key]) : "default"}
              aria-label={label}
              onChange={(e) => setField(key, e.target.value)}
            />
          </label>
        );
      })}
    </div>
  );
}

export function SubmitLorasEditor({
  draft,
  seed,
  disabled,
  onChange,
}: {
  draft: WorkProductLoraEntry[];
  seed?: WorkProductLoraEntry[] | null;
  disabled?: boolean;
  onChange: (next: WorkProductLoraEntry[]) => void;
}) {
  const rows = draft.length ? draft : seed || [];
  const onCount = rows.filter((e) => e.on !== false).length;
  const setEntry = (idx: number, patch: Partial<WorkProductLoraEntry>) => {
    const base = draft.length ? draft : seed || [];
    onChange(base.map((row, i) => (i === idx ? { ...row, ...patch } : row)));
  };

  if (!rows.length) return null;

  return (
    <div className="work-product-prompt-editor work-product-loras-editor submit-composer__loras">
      <details className="work-product-prompt-editor__section" open>
        <summary className="work-product-prompt-editor__section-summary">
          <span>LoRAs</span>
          <span className="factory-muted">{`${onCount}/${rows.length} on`}</span>
        </summary>
        <div className="work-product-prompt-editor__section-body">
          <div className="work-product-loras-editor__list">
            {rows.map((row, idx) => {
              const seedRow = seed?.[idx];
              const changed =
                Boolean(seedRow) &&
                (Boolean(row.on) !== Boolean(seedRow?.on) ||
                  Number(row.strength ?? NaN) !== Number(seedRow?.strength ?? NaN) ||
                  String(row.lora || "") !== String(seedRow?.lora || ""));
              return (
                <div
                  key={`${row.lora}-${idx}`}
                  className={"work-product-loras-editor__row" + (changed ? " is-diff" : "")}
                >
                  <label className="work-product-loras-editor__on">
                    <input
                      type="checkbox"
                      checked={row.on !== false}
                      disabled={disabled}
                      onChange={(e) => setEntry(idx, { on: e.target.checked })}
                    />
                    <span className="work-product-loras-editor__name mono" title={row.lora}>
                      {loraBasename(row.lora)}
                    </span>
                  </label>
                  <label className="work-product-loras-editor__strength">
                    <span className="work-product-params-editor__label">Str</span>
                    <input
                      type="number"
                      step="0.05"
                      className="work-product-params-editor__input"
                      value={row.strength ?? ""}
                      disabled={disabled || row.on === false}
                      onChange={(e) => {
                        const n = Number(e.target.value);
                        setEntry(idx, { strength: Number.isFinite(n) ? n : undefined });
                      }}
                    />
                  </label>
                </div>
              );
            })}
          </div>
        </div>
      </details>
    </div>
  );
}

