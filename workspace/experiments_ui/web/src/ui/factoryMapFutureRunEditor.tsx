import React, { useCallback, useEffect, useMemo, useState } from "react";
import { fetchShapeFactoryPromptProfile } from "./api";
import type { FutureRunDraft, ShapeFactoryMapQueueOverrides } from "./types";

const EMPTY_DRAFT: FutureRunDraft = {
  promptProfile: { label: "", positive: "", negative: "" },
  parameters: { frames: "", steps: "", overlap: "", frame_load_cap: "" },
};

function parseOptionalInt(raw: string): number | undefined {
  const t = raw.trim();
  if (!t) return undefined;
  const n = Number(t);
  return Number.isFinite(n) ? Math.trunc(n) : undefined;
}

export function buildQueueOverrides(
  draft: FutureRunDraft,
  baseline: FutureRunDraft,
): ShapeFactoryMapQueueOverrides | undefined {
  const overrides: ShapeFactoryMapQueueOverrides = {};
  const promptDirty =
    draft.promptProfile.positive !== baseline.promptProfile.positive ||
    draft.promptProfile.negative !== baseline.promptProfile.negative ||
    draft.promptProfile.label !== baseline.promptProfile.label;

  if (promptDirty) {
    overrides.prompt_profile = {
      label: draft.promptProfile.label,
      positive: draft.promptProfile.positive,
      negative: draft.promptProfile.negative,
    };
  }

  const params: ShapeFactoryMapQueueOverrides["parameters"] = {};
  const frames = parseOptionalInt(draft.parameters.frames);
  const steps = parseOptionalInt(draft.parameters.steps);
  const overlap = parseOptionalInt(draft.parameters.overlap);
  const frameLoadCap = parseOptionalInt(draft.parameters.frame_load_cap);
  if (frames != null) params.frames = frames;
  if (steps != null) params.steps = steps;
  if (overlap != null) params.overlap = overlap;
  if (frameLoadCap != null) params.frame_load_cap = frameLoadCap;
  if (Object.keys(params).length) overrides.parameters = params;

  return Object.keys(overrides).length ? overrides : undefined;
}

export function isFutureRunDraftDirty(draft: FutureRunDraft, baseline: FutureRunDraft): boolean {
  return Boolean(buildQueueOverrides(draft, baseline));
}

type FutureRunEditorProps = {
  promptProfilePath?: string;
  onDraftChange: (draft: FutureRunDraft, baseline: FutureRunDraft) => void;
};

export function FutureRunEditor({ promptProfilePath, onDraftChange }: FutureRunEditorProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [sourceLabel, setSourceLabel] = useState("");
  const [baseline, setBaseline] = useState<FutureRunDraft>(EMPTY_DRAFT);
  const [draft, setDraft] = useState<FutureRunDraft>(EMPTY_DRAFT);
  const [paramsOpen, setParamsOpen] = useState(false);

  useEffect(() => {
    if (!promptProfilePath) {
      setBaseline(EMPTY_DRAFT);
      setDraft(EMPTY_DRAFT);
      setSourceLabel("");
      setError("");
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError("");
    void fetchShapeFactoryPromptProfile(promptProfilePath)
      .then((res) => {
        if (cancelled) return;
        const next: FutureRunDraft = {
          promptProfile: {
            label: String(res.label ?? res.basename?.replace(/\.json$/i, "") ?? ""),
            positive: String(res.positive ?? ""),
            negative: String(res.negative ?? ""),
          },
          parameters: { frames: "", steps: "", overlap: "", frame_load_cap: "" },
        };
        setBaseline(next);
        setDraft(next);
        setSourceLabel(res.basename || res.path || promptProfilePath);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
        setBaseline(EMPTY_DRAFT);
        setDraft(EMPTY_DRAFT);
        setSourceLabel("");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [promptProfilePath]);

  useEffect(() => {
    onDraftChange(draft, baseline);
  }, [draft, baseline, onDraftChange]);

  const dirty = useMemo(() => isFutureRunDraftDirty(draft, baseline), [draft, baseline]);

  const reset = useCallback(() => {
    setDraft(baseline);
  }, [baseline]);

  if (!promptProfilePath) {
    return <div className="sfmap-future-edit sfmap-future-edit--empty">No prompt profile for this combo.</div>;
  }

  return (
    <section className="sfmap-detail-section sfmap-future-edit">
      <div className="sfmap-future-edit__head">
        <h3>Edit before queue</h3>
        {dirty ? <span className="sfmap-future-edit__dirty">Modified</span> : null}
      </div>
      <div className="sfmap-future-edit__source mono">{sourceLabel || promptProfilePath}</div>

      {loading ? <div className="sfmap-future-edit__loading">Loading prompt…</div> : null}
      {error ? (
        <div className="sfmap-future-edit__error" role="alert">
          {error}
        </div>
      ) : null}

      {!loading && !error ? (
        <>
          <label className="sfmap-future-field">
            <span className="sfmap-future-field__label">Label</span>
            <input
              type="text"
              className="sfmap-future-field__input mono"
              value={draft.promptProfile.label}
              onChange={(e) =>
                setDraft((prev) => ({
                  ...prev,
                  promptProfile: { ...prev.promptProfile, label: e.target.value },
                }))
              }
            />
          </label>

          <label className="sfmap-future-field">
            <span className="sfmap-future-field__label">Positive prompt</span>
            <textarea
              className="sfmap-future-field__textarea mono"
              rows={4}
              value={draft.promptProfile.positive}
              onChange={(e) =>
                setDraft((prev) => ({
                  ...prev,
                  promptProfile: { ...prev.promptProfile, positive: e.target.value },
                }))
              }
            />
          </label>

          <label className="sfmap-future-field">
            <span className="sfmap-future-field__label">Negative prompt</span>
            <textarea
              className="sfmap-future-field__textarea mono"
              rows={2}
              value={draft.promptProfile.negative}
              onChange={(e) =>
                setDraft((prev) => ({
                  ...prev,
                  promptProfile: { ...prev.promptProfile, negative: e.target.value },
                }))
              }
            />
          </label>

          <div className="sfmap-future-params">
            <button
              type="button"
              className="sfmap-future-params__toggle"
              aria-expanded={paramsOpen}
              onClick={() => setParamsOpen((v) => !v)}
            >
              Parameters {paramsOpen ? "▾" : "▸"}
              <span className="sfmap-future-params__hint">optional · adhoc overrides</span>
            </button>
            {paramsOpen ? (
              <div className="sfmap-future-params__grid">
                {(
                  [
                    ["frames", "Frames"],
                    ["steps", "Steps"],
                    ["overlap", "Overlap"],
                    ["frame_load_cap", "Frame load cap"],
                  ] as const
                ).map(([key, label]) => (
                  <label key={key} className="sfmap-future-field sfmap-future-field--compact">
                    <span className="sfmap-future-field__label">{label}</span>
                    <input
                      type="number"
                      min={1}
                      className="sfmap-future-field__input mono"
                      placeholder="default"
                      value={draft.parameters[key]}
                      onChange={(e) =>
                        setDraft((prev) => ({
                          ...prev,
                          parameters: { ...prev.parameters, [key]: e.target.value },
                        }))
                      }
                    />
                  </label>
                ))}
              </div>
            ) : null}
          </div>

          {dirty ? (
            <button type="button" className="sfmap-future-reset-btn" onClick={reset}>
              Reset edits
            </button>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
