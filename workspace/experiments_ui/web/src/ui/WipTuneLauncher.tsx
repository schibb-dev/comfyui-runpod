import React, { useCallback } from "react";
import type { RunsItem, WipDateDir, WipMediaEntry, WipPlannedExperiment } from "./types";

const DEFAULT_SEED = 42;
const DEFAULT_DURATION = 5;
const DEFAULT_CFG = "5.0 5.5";
const DEFAULT_DENOISE = "0.82 0.84";
const DEFAULT_STEPS = "28 32";

export function parseList(s: string): (number | string)[] {
  return s
    .split(/[\s,]+/)
    .map((p) => p.trim())
    .filter(Boolean)
    .map((p) => {
      const n = Number(p);
      return Number.isFinite(n) ? n : p;
    });
}

export function sweepFromParamStrings(cfgStr: string, denoiseStr: string, stepsStr: string): Record<string, unknown> {
  const cfg = parseList(cfgStr);
  const denoise = parseList(denoiseStr);
  const steps = parseList(stepsStr);
  const sweep: Record<string, unknown> = {};
  if (cfg.length) sweep.cfg = cfg;
  if (denoise.length) sweep.denoise = denoise;
  if (steps.length) sweep.steps = steps;
  return sweep;
}

export type WipFormParams = {
  seed: number;
  duration_sec: number;
  baseline_first: boolean;
  max_runs: number;
  cfgStr: string;
  denoiseStr: string;
  stepsStr: string;
};

export const DEFAULT_WIP_PARAMS: WipFormParams = {
  seed: DEFAULT_SEED,
  duration_sec: DEFAULT_DURATION,
  baseline_first: false,
  max_runs: 20,
  cfgStr: DEFAULT_CFG,
  denoiseStr: DEFAULT_DENOISE,
  stepsStr: DEFAULT_STEPS,
};

function nextId(): string {
  return `wip-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

export type WipSidebarContentProps = {
  planned: WipPlannedExperiment[];
  onPlannedChange: (next: WipPlannedExperiment[]) => void;
  selectedWipRelpath: string | null;
  onSelectWip: (relpath: string | null) => void;
  editingId: string | null;
  onEdit: (id: string | null) => void;
  dates: WipDateDir[];
  media: WipMediaEntry[];
  currentDir: string;
  loading: boolean;
  error: string;
  onLoadWip: (dir?: string) => Promise<void>;
  onOpenMain?: () => void;
};

export function WipSidebarContent({
  planned,
  onPlannedChange,
  selectedWipRelpath,
  onSelectWip,
  editingId,
  onEdit,
  dates,
  media,
  currentDir,
  loading,
  error,
  onLoadWip,
}: WipSidebarContentProps) {
  const movePlanned = useCallback(
    (index: number, delta: number) => {
      const next = [...planned];
      const to = index + delta;
      if (to < 0 || to >= next.length) return;
      [next[index], next[to]] = [next[to], next[index]];
      onPlannedChange(next);
    },
    [planned, onPlannedChange]
  );

  const removePlanned = useCallback(
    (id: string) => {
      onPlannedChange(planned.filter((p) => p.id !== id));
      if (editingId === id) onEdit(null);
    },
    [planned, editingId, onPlannedChange, onEdit]
  );

  const selectForEdit = useCallback(
    (item: WipPlannedExperiment) => {
      onEdit(item.id);
      onSelectWip(null);
    },
    [onEdit, onSelectWip]
  );

  return (
    <div className="wip-tune-sidebar" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {error ? (
        <div className="error-callout" style={{ padding: 8, background: "var(--error-bg)", color: "var(--error-fg)", borderRadius: 4, fontSize: 12 }}>
          {error}
        </div>
      ) : null}

      <div>
        <label style={{ fontSize: 12, color: "var(--muted)" }}>Planned experiments</label>
        <ul className="wip-planned-list" style={{ listStyle: "none", margin: "4px 0 0", padding: 0 }}>
          {planned.length === 0 ? (
            <li style={{ color: "var(--muted)", fontSize: 12 }}>None yet. Select a video on the right and click Add.</li>
          ) : null}
          {planned.map((item, i) => (
            <li
              key={item.id}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 4,
                padding: "4px 0",
                borderBottom: "1px solid var(--border)",
              }}
            >
              <span
                className="drag-handle"
                style={{ cursor: "grab", color: "var(--muted)", fontSize: 10 }}
                title="Reorder"
              >
                ⋮⋮
              </span>
              <button
                type="button"
                className={`link-like ${editingId === item.id ? "active" : ""}`}
                onClick={() => selectForEdit(item)}
                style={{ flex: 1, textAlign: "left", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 12 }}
                title={item.videoName}
              >
                {item.videoName.replace(/\.[^.]+$/, "")} · {item.duration_sec}s
              </button>
              <div style={{ display: "flex", gap: 2 }}>
                <button type="button" className="icon-btn" onClick={() => movePlanned(i, -1)} disabled={i === 0} title="Move up" aria-label="Move up">
                  ↑
                </button>
                <button type="button" className="icon-btn" onClick={() => movePlanned(i, 1)} disabled={i === planned.length - 1} title="Move down" aria-label="Move down">
                  ↓
                </button>
                <button type="button" className="icon-btn" onClick={() => removePlanned(item.id)} title="Remove" aria-label="Remove">
                  ×
                </button>
              </div>
            </li>
          ))}
        </ul>
      </div>

      <div>
        <label style={{ fontSize: 12, color: "var(--muted)" }}>Select WIP video</label>
        {!currentDir ? (
          <ul className="wip-dates" style={{ listStyle: "none", margin: "4px 0 0", padding: 0 }}>
            {dates.length === 0 && !loading ? <li style={{ color: "var(--muted)", fontSize: 12 }}>No date folders</li> : null}
            {dates.map((d) => (
              <li key={d.name}>
                <button type="button" className="link-like" onClick={() => onLoadWip(d.name)} style={{ padding: "2px 0", fontSize: 12 }}>
                  {d.name}
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <div style={{ marginTop: 4 }}>
            <button type="button" className="link-like" onClick={() => onLoadWip()} style={{ marginRight: 8, fontSize: 12 }}>
              ← Back
            </button>
            <span className="mono" style={{ fontSize: 12 }}>{currentDir}</span>
          </div>
        )}
        {loading ? <div style={{ marginTop: 4, color: "var(--muted)", fontSize: 12 }}>Loading…</div> : null}
        {media.length > 0 ? (
          <ul className="wip-media" style={{ listStyle: "none", margin: "4px 0 0", padding: 0, maxHeight: 160, overflowY: "auto" }}>
            {media.map((m) => (
              <li key={m.relpath} style={{ padding: "2px 0" }}>
                <button
                  type="button"
                  className={`link-like ${selectedWipRelpath === m.relpath ? "active" : ""}`}
                  onClick={() => {
                    onSelectWip(m.relpath);
                    onEdit(null);
                  }}
                  style={{ fontSize: 12, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", display: "block", width: "100%", textAlign: "left" }}
                  title={m.name}
                >
                  {m.name}
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </div>
  );
}

export type WipMainContentProps = {
  planned: WipPlannedExperiment[];
  selectedWipRelpath: string | null;
  editingId: string | null;
  params: WipFormParams;
  onParamsChange: (next: WipFormParams) => void;
  onAddExperiment: () => void;
  onUpdateExperiment: () => void;
  onCreateAll: () => Promise<void>;
  creating: boolean;
  createLog: string | null;
};

export function WipMainContent({
  planned,
  selectedWipRelpath,
  editingId,
  params,
  onParamsChange,
  onAddExperiment,
  onUpdateExperiment,
  onCreateAll,
  creating,
  createLog,
}: WipMainContentProps) {
  const editing = editingId ? planned.find((p) => p.id === editingId) : null;
  const relpath = selectedWipRelpath ?? editing?.base_mp4_relpath ?? null;
  const canAdd = Boolean(selectedWipRelpath);
  const canUpdate = Boolean(editingId);

  return (
    <div className="wip-main-content" style={{ display: "flex", flex: 1, minHeight: 0, gap: 16, padding: 12 }}>
      <div className="wip-video-pane" style={{ flex: "1 1 60%", minWidth: 200, display: "flex", flexDirection: "column", background: "var(--bg-2)", borderRadius: 8, overflow: "hidden" }}>
        {relpath ? (
          <video
            key={relpath}
            src={`/files/${encodeURIComponent(relpath)}`}
            controls
            playsInline
            preload="metadata"
            style={{ width: "100%", height: "100%", objectFit: "contain" }}
          />
        ) : (
          <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--muted)", fontSize: 14 }}>
            Select a WIP video from the left, or click a planned experiment to edit.
          </div>
        )}
      </div>
      <div className="wip-params-pane" style={{ flex: "0 0 280px", display: "flex", flexDirection: "column", gap: 10 }}>
        <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ width: 72, fontSize: 12 }}>Seed</span>
          <input
            type="number"
            value={params.seed}
            onChange={(e) => onParamsChange({ ...params, seed: Number(e.target.value) || 0 })}
            style={{ width: 72 }}
          />
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ width: 72, fontSize: 12 }}>Duration (s)</span>
          <input
            type="number"
            step={0.5}
            value={params.duration_sec}
            onChange={(e) => onParamsChange({ ...params, duration_sec: Number(e.target.value) || 0 })}
            style={{ width: 72 }}
          />
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ width: 72, fontSize: 12 }}>CFG</span>
          <input type="text" value={params.cfgStr} onChange={(e) => onParamsChange({ ...params, cfgStr: e.target.value })} placeholder="5.0 5.5" style={{ flex: 1 }} />
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ width: 72, fontSize: 12 }}>Denoise</span>
          <input type="text" value={params.denoiseStr} onChange={(e) => onParamsChange({ ...params, denoiseStr: e.target.value })} placeholder="0.82 0.84" style={{ flex: 1 }} />
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ width: 72, fontSize: 12 }}>Steps</span>
          <input type="text" value={params.stepsStr} onChange={(e) => onParamsChange({ ...params, stepsStr: e.target.value })} placeholder="28 32" style={{ flex: 1 }} />
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <input
            type="checkbox"
            checked={params.baseline_first}
            onChange={(e) => onParamsChange({ ...params, baseline_first: e.target.checked })}
          />
          <span style={{ fontSize: 12 }}>Baseline first</span>
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ width: 72, fontSize: 12 }}>Max runs</span>
          <input
            type="number"
            min={1}
            value={params.max_runs}
            onChange={(e) => onParamsChange({ ...params, max_runs: Number(e.target.value) || 20 })}
            style={{ width: 72 }}
          />
        </label>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 8 }}>
          {canAdd ? (
            <button type="button" className="btn primary" onClick={onAddExperiment}>
              Add experiment
            </button>
          ) : null}
          {canUpdate ? (
            <button type="button" className="btn" onClick={onUpdateExperiment}>
              Update experiment
            </button>
          ) : null}
        </div>
        {planned.length > 0 ? (
          <button type="button" className="btn primary" onClick={() => void onCreateAll()} disabled={creating} style={{ marginTop: 8 }}>
            {creating ? "Creating…" : `Create ${planned.length} experiment(s)`}
          </button>
        ) : null}
        {createLog ? <pre style={{ margin: "8px 0 0", fontSize: 11, whiteSpace: "pre-wrap", color: "var(--muted)", maxHeight: 80, overflow: "auto" }}>{createLog}</pre> : null}
      </div>
    </div>
  );
}

export function paramsFromPlanned(item: WipPlannedExperiment): WipFormParams {
  const arr = (v: unknown) => (Array.isArray(v) ? v.map(String).join(" ") : "");
  const sweep = item.sweep ?? {};
  return {
    seed: item.seed,
    duration_sec: item.duration_sec,
    baseline_first: item.baseline_first,
    max_runs: item.max_runs,
    cfgStr: arr(sweep.cfg) || DEFAULT_CFG,
    denoiseStr: arr(sweep.denoise) || DEFAULT_DENOISE,
    stepsStr: arr(sweep.steps) || DEFAULT_STEPS,
  };
}

/** Derive WipFormParams from a run's params (for "Branch experiment" from run). */
export function paramsFromRun(run: RunsItem): WipFormParams {
  const p = run.params ?? {};
  const num = (v: unknown): number => (typeof v === "number" && Number.isFinite(v) ? v : typeof v === "string" ? Number(v) || 0 : 0);
  const arr = (v: unknown) => (Array.isArray(v) ? v.map(String).join(" ") : typeof v === "number" || typeof v === "string" ? String(v) : "");
  const sweep = (typeof p.sweep === "object" && p.sweep !== null && !Array.isArray(p.sweep) ? p.sweep as Record<string, unknown> : {}) as Record<string, unknown>;
  return {
    seed: num(p.seed) || DEFAULT_SEED,
    duration_sec: num(p.duration_sec) || num(p.duration) || DEFAULT_DURATION,
    baseline_first: Boolean(p.baseline_first),
    max_runs: num(p.max_runs) || 20,
    cfgStr: arr(sweep.cfg ?? p.cfg) || DEFAULT_CFG,
    denoiseStr: arr(sweep.denoise ?? p.denoise) || DEFAULT_DENOISE,
    stepsStr: arr(sweep.steps ?? p.steps) || DEFAULT_STEPS,
  };
}

export { nextId };
