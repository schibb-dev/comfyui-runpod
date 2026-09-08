import React, { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { fetchShapeFactoryPromptProfile, updateShapeFactoryOwnedPrompt } from "./api";
import {
  clonePromptRows,
  encodePromptRowsClient,
  PromptChunkEditor,
  rowsFromRawText,
} from "./PromptChunks";
import { PromptMarkupTable } from "./PromptPeek";
import type { WorkProductPromptProfile, WorkProductPromptRow } from "./types";

export type SubmitPromptOverride = {
  label?: string;
  positive: string;
  negative: string;
};

export type SubmitPromptEditorHandle = {
  flush: () => Promise<boolean>;
  dirty: () => boolean;
};

function rowsFromProfileText(text: string, rows?: WorkProductPromptRow[] | null): WorkProductPromptRow[] {
  if (rows && rows.length) return clonePromptRows(rows);
  return rowsFromRawText(text) || clonePromptRows(undefined, text);
}

function seedFromPrompt(prompt: WorkProductPromptProfile | null | undefined): {
  label: string;
  positive: string;
  negative: string;
  pos: WorkProductPromptRow[];
  neg: WorkProductPromptRow[];
} {
  const positive = String(prompt?.positive || "");
  const negative = String(prompt?.negative || "");
  return {
    label: String(prompt?.label || prompt?.name || prompt?.basename || "").trim(),
    positive,
    negative,
    pos: rowsFromProfileText(positive, prompt?.positive_rows),
    neg: rowsFromProfileText(negative, prompt?.negative_rows),
  };
}

export const SubmitPromptEditor = forwardRef<
  SubmitPromptEditorHandle,
  {
    heading?: string;
    /** Catalog JSON path — loaded when `prompt` is omitted (compose). */
    profilePath?: string | null;
    /** Owned / already-loaded prompt (edit-in-place). */
    prompt?: WorkProductPromptProfile | null;
    jobKey?: string | null;
    jobPath?: string | null;
    disabled?: boolean;
    /** Compose: emit override only while dirty. Edit-job uses Save / flush. */
    onOverrideChange?: (override: SubmitPromptOverride | null) => void;
    onJobSaved?: () => void;
  }
>(function SubmitPromptEditor(
  { heading = "Prompt", profilePath, prompt, jobKey, jobPath, disabled, onOverrideChange, onJobSaved },
  ref,
) {
  const persistJob = Boolean(jobKey);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rawMode, setRawMode] = useState(false);
  const [label, setLabel] = useState("");
  const [positive, setPositive] = useState("");
  const [negative, setNegative] = useState("");
  const [posChunks, setPosChunks] = useState<WorkProductPromptRow[]>([]);
  const [negChunks, setNegChunks] = useState<WorkProductPromptRow[]>([]);
  const [baseline, setBaseline] = useState({ positive: "", negative: "", label: "" });
  const [dirty, setDirty] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const lastOverrideJson = useRef<string>("");

  const applySeed = useCallback((seed: ReturnType<typeof seedFromPrompt>) => {
    const pos = seed.pos;
    const neg = seed.neg;
    const positiveEnc = encodePromptRowsClient(pos) || seed.positive;
    const negativeEnc = encodePromptRowsClient(neg) || seed.negative;
    setLabel(seed.label);
    setPositive(positiveEnc);
    setNegative(negativeEnc);
    setPosChunks(pos);
    setNegChunks(neg);
    setBaseline({ positive: positiveEnc, negative: negativeEnc, label: seed.label });
    setDirty(false);
    setRawMode(false);
    setError(null);
    setMsg(null);
  }, []);

  useEffect(() => {
    if (prompt) {
      applySeed(seedFromPrompt(prompt));
      setLoading(false);
      return;
    }
    const path = String(profilePath || "").trim();
    if (!path) {
      applySeed(seedFromPrompt(null));
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    void fetchShapeFactoryPromptProfile(path)
      .then((res) => {
        if (cancelled) return;
        applySeed(
          seedFromPrompt({
            path: res.path,
            basename: res.basename,
            label: res.label != null ? String(res.label) : null,
            positive: String(res.positive || ""),
            negative: String(res.negative || ""),
          }),
        );
      })
      .catch((e) => {
        if (cancelled) return;
        applySeed(seedFromPrompt(null));
        setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [applySeed, profilePath, prompt?.content_hash, prompt?.path, prompt?.positive, prompt?.negative]);

  const encodedPositive = rawMode ? positive : encodePromptRowsClient(posChunks);
  const encodedNegative = rawMode ? negative : encodePromptRowsClient(negChunks);

  const currentDirty = useMemo(() => {
    return (
      encodedPositive !== baseline.positive ||
      encodedNegative !== baseline.negative ||
      label !== baseline.label
    );
  }, [baseline.label, baseline.negative, baseline.positive, encodedNegative, encodedPositive, label]);

  useEffect(() => {
    setDirty(currentDirty);
  }, [currentDirty]);

  useEffect(() => {
    if (persistJob) return;
    const next: SubmitPromptOverride | null = currentDirty
      ? { positive: encodedPositive, negative: encodedNegative, ...(label ? { label } : {}) }
      : null;
    const json = JSON.stringify(next);
    if (json === lastOverrideJson.current) return;
    lastOverrideJson.current = json;
    onOverrideChange?.(next);
  }, [currentDirty, encodedNegative, encodedPositive, label, onOverrideChange, persistJob]);

  const saveToJob = useCallback(async (): Promise<boolean> => {
    const key = String(jobKey || "").trim();
    if (!key) return true;
    if (!currentDirty) return true;
    setSaving(true);
    setMsg(null);
    try {
      if (rawMode) {
        await updateShapeFactoryOwnedPrompt({
          job_key: key,
          job_path: jobPath || undefined,
          positive,
          negative,
          ...(label ? { label } : {}),
        });
      } else {
        await updateShapeFactoryOwnedPrompt({
          job_key: key,
          job_path: jobPath || undefined,
          positive_rows: posChunks,
          negative_rows: negChunks,
          ...(label ? { label } : {}),
        });
      }
      setBaseline({ positive: encodedPositive, negative: encodedNegative, label });
      setDirty(false);
      setMsg("Saved to job");
      onJobSaved?.();
      return true;
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setSaving(false);
    }
  }, [
    currentDirty,
    encodedNegative,
    encodedPositive,
    jobKey,
    jobPath,
    label,
    negative,
    onJobSaved,
    posChunks,
    positive,
    negChunks,
    rawMode,
  ]);

  useImperativeHandle(
    ref,
    () => ({
      flush: () => saveToJob(),
      dirty: () => currentDirty,
    }),
    [currentDirty, saveToJob],
  );

  const posSummary = posChunks.length
    ? `${posChunks.length} part${posChunks.length === 1 ? "" : "s"}`
    : encodedPositive.trim()
      ? `${encodedPositive.trim().length} chars`
      : "empty";
  const negSummary = negChunks.length
    ? `${negChunks.length} part${negChunks.length === 1 ? "" : "s"}`
    : encodedNegative.trim()
      ? `${encodedNegative.trim().length} chars`
      : "empty";
  const busy = Boolean(disabled || loading || saving);
  const sourceName = prompt?.name || prompt?.label || prompt?.basename || (profilePath || "").split(/[\\/]/).pop() || "";
  const hasSource = Boolean(prompt || String(profilePath || "").trim());

  if (!hasSource && !loading) {
    return (
      <div className="work-product-prompt-editor submit-composer__prompt">
        <div className="work-product-prompt-editor__head">
          <span className="work-product-prompt-editor__title">{heading}</span>
          <span className="factory-muted">Select a variant to edit the prompt</span>
        </div>
      </div>
    );
  }

  return (
    <div className="work-product-prompt-editor submit-composer__prompt">
      <div className="work-product-prompt-editor__head">
        <span className="work-product-prompt-editor__title">{heading}</span>
        <span className="work-product-prompt-editor__name" title={profilePath || prompt?.path || undefined}>
          {sourceName || "prompt"}
        </span>
        {currentDirty ? <span className="work-product-badge">edited</span> : null}
        <div className="work-product-prompt-editor__actions">
          <button
            type="button"
            className="drt-btn"
            disabled={busy}
            onClick={() => {
              if (!rawMode) {
                setPositive(encodePromptRowsClient(posChunks));
                setNegative(encodePromptRowsClient(negChunks));
              } else {
                setPosChunks(rowsFromRawText(positive));
                setNegChunks(rowsFromRawText(negative));
              }
              setRawMode((v) => !v);
            }}
          >
            {rawMode ? "Edit chunks" : "Edit raw"}
          </button>
          {currentDirty ? (
            <button
              type="button"
              className="drt-btn"
              disabled={busy}
              onClick={() => applySeed(seedFromPrompt(prompt || { positive: baseline.positive, negative: baseline.negative, label: baseline.label }))}
            >
              Reset
            </button>
          ) : null}
          {persistJob ? (
            <button type="button" className="drt-btn" disabled={!currentDirty || busy} onClick={() => void saveToJob()}>
              {saving ? "Saving…" : "Save to job"}
            </button>
          ) : null}
        </div>
      </div>
      {loading ? <p className="factory-muted">Loading prompt…</p> : null}
      {error ? (
        <p className="work-product-prompt-editor__msg" role="alert">
          {error}
        </p>
      ) : null}
      {!loading && !error ? (
        <>
          <details className="work-product-prompt-editor__section" open>
            <summary className="work-product-prompt-editor__section-summary">
              <span>Positive</span>
              <span className="factory-muted">{posSummary}</span>
            </summary>
            <div className="work-product-prompt-editor__section-body">
              {rawMode ? (
                <textarea
                  className="work-product-prompt-editor__textarea"
                  value={positive}
                  disabled={busy}
                  rows={8}
                  onChange={(e) => {
                    setPositive(e.target.value);
                    setDirty(true);
                  }}
                />
              ) : (
                <PromptChunkEditor
                  rows={posChunks}
                  disabled={busy}
                  onChange={(next) => {
                    setPosChunks(next);
                    setDirty(true);
                  }}
                />
              )}
            </div>
          </details>
          <details className="work-product-prompt-editor__section" open>
            <summary className="work-product-prompt-editor__section-summary">
              <span>Negative</span>
              <span className="factory-muted">{negSummary}</span>
            </summary>
            <div className="work-product-prompt-editor__section-body">
              {rawMode ? (
                <textarea
                  className="work-product-prompt-editor__textarea"
                  value={negative}
                  disabled={busy}
                  rows={4}
                  onChange={(e) => {
                    setNegative(e.target.value);
                    setDirty(true);
                  }}
                />
              ) : negChunks.length || currentDirty ? (
                <PromptChunkEditor
                  rows={negChunks}
                  disabled={busy}
                  onChange={(next) => {
                    setNegChunks(next);
                    setDirty(true);
                  }}
                />
              ) : (
                <PromptMarkupTable title="" rows={[]} fallbackText={encodedNegative} />
              )}
            </div>
          </details>
        </>
      ) : null}
      {msg ? <p className="work-product-prompt-editor__msg factory-muted">{msg}</p> : null}
    </div>
  );
});
