import React, { useEffect, useMemo, useRef, useState } from "react";
import { fetchVisionTagJudgment, saveVisionTagJudgment } from "./api";
import { PageHeader } from "./PageHeader";
import type {
  VisionTagJudgmentItem,
  VisionTagJudgmentLeaderboard,
  VisionTagJudgmentResponse,
  VisionTagLabel,
} from "./types";

type ChipState = VisionTagLabel | null;

function cycleLabel(cur: ChipState): ChipState {
  if (cur == null) return "good";
  if (cur === "good") return "bad";
  return null;
}

function fmtPct(x: number | null | undefined): string {
  if (x == null || !Number.isFinite(x)) return "—";
  return `${(x * 100).toFixed(0)}%`;
}

function TagStatList({
  title,
  rows,
  rateKey,
}: {
  title: string;
  rows: {
    tag: string;
    n_labeled?: number;
    n_important?: number;
    n_missing?: number;
    good_rate?: number | null;
    bad_rate?: number | null;
  }[];
  rateKey: "good_rate" | "bad_rate" | "n_important" | "n_missing";
}) {
  if (!rows.length) return null;
  return (
    <div className="tag-judge-tagstats__col">
      <div className="tag-judge-tagstats__title">{title}</div>
      <ul className="tag-judge-tagstats__list">
        {rows.slice(0, 12).map((r) => (
          <li key={r.tag}>
            <span className="tag-judge-tagstats__tag">{r.tag}</span>
            <span className="tag-judge-tagstats__rate">
              {rateKey === "n_important"
                ? `★${r.n_important ?? 0}`
                : rateKey === "n_missing"
                  ? `FN×${r.n_missing ?? 0}`
                  : `${fmtPct(r[rateKey])} · n=${r.n_labeled ?? 0}`}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function LeaderboardTable({ board }: { board: VisionTagJudgmentLeaderboard }) {
  const rows = [...(board.models || []), ...(board.combos || [])].sort(
    (a, b) => (b.f1 ?? -1) - (a.f1 ?? -1) || (b.precision ?? -1) - (a.precision ?? -1),
  );
  const ts = board.tag_stats;
  if (!rows.length && !ts) return null;
  return (
    <div className="tag-judge-board">
      <div className="tag-judge-board__meta">
        Leaderboard · {board.judged_samples ?? 0} samples · {board.labeled_tags ?? 0} labels
        {board.important_tags != null ? ` · ★${board.important_tags}` : ""}
        {board.missing_tags != null ? ` · FN×${board.missing_tags}` : ""}
        {ts?.tag_count != null ? ` · ${ts.tag_count} unique tags` : ""}
      </div>
      {rows.length ? (
        <table className="tag-judge-board__table">
          <thead>
            <tr>
              <th>id</th>
              <th>P</th>
              <th>R</th>
              <th>F1</th>
              <th>FP%</th>
              <th>ImpR</th>
              <th>MissN</th>
              <th>emit</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td>{r.id}</td>
                <td>{fmtPct(r.precision)}</td>
                <td>{fmtPct(r.recall)}</td>
                <td>{fmtPct(r.f1)}</td>
                <td>{fmtPct(r.fp_rate_among_judged)}</td>
                <td>{fmtPct(r.important_recall)}</td>
                <td>{r.missing_n ?? "—"}</td>
                <td>{r.emitted ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {ts ? (
        <div className="tag-judge-tagstats">
          <TagStatList
            title="Commonly misidentified"
            rows={ts.commonly_misidentified || []}
            rateKey="bad_rate"
          />
          <TagStatList title="Commonly correct" rows={ts.commonly_correct || []} rateKey="good_rate" />
          <TagStatList
            title="Commonly important"
            rows={ts.commonly_important || []}
            rateKey="n_important"
          />
          <TagStatList
            title="Commonly missing"
            rows={ts.commonly_missing || []}
            rateKey="n_missing"
          />
        </div>
      ) : null}
    </div>
  );
}

export function VisionTagJudgeApp() {
  const [data, setData] = useState<VisionTagJudgmentResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [idx, setIdx] = useState(0);
  const [labels, setLabels] = useState<Record<string, ChipState>>({});
  const [priorBad, setPriorBad] = useState<Set<string>>(() => new Set());
  const [priorGood, setPriorGood] = useState<Set<string>>(() => new Set());
  const [important, setImportant] = useState<Set<string>>(() => new Set());
  const [missing, setMissing] = useState<Set<string>>(() => new Set());
  const [missingDraft, setMissingDraft] = useState("");
  const [focusTag, setFocusTag] = useState(0);
  const [board, setBoard] = useState<VisionTagJudgmentLeaderboard | null>(null);
  const [passMode, setPassMode] = useState<"normal" | "important" | "missing">("normal");
  const videoRef = useRef<HTMLVideoElement | null>(null);

  const items = data?.items || [];
  const item: VisionTagJudgmentItem | null = items[idx] || null;
  const tags = item?.tags || [];
  const minScore = data?.min_score_samples ?? 15;
  const doneCount = data?.done_count ?? 0;
  const importantPass = passMode === "important";
  const missingPass = passMode === "missing";

  const importantCoverage = useMemo(() => {
    const withStar = items.filter((it) => (it.important || []).length > 0).length;
    return { withStar, total: items.length, missing: Math.max(0, items.length - withStar) };
  }, [items]);

  const missingCoverage = useMemo(() => {
    const withMiss = items.filter((it) => (it.missing || []).length > 0).length;
    const reviewed = items.filter(
      (it) => (it.missing || []).length > 0 || Boolean(it.judged_utc && (it.missing || []).length === 0 && missingPass),
    ).length;
    return { withMiss, total: items.length, reviewed };
  }, [items, missingPass]);

  const applyPayload = (res: VisionTagJudgmentResponse) => {
    setData(res);
    setBoard(res.leaderboard || null);
    setError(null);
  };

  useEffect(() => {
    setLoading(true);
    void fetchVisionTagJudgment()
      .then((res) => {
        applyPayload(res);
        const first = (res.items || []).findIndex((it) => !it.labels && !it.skipped);
        setIdx(first >= 0 ? first : 0);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!item) {
      setLabels({});
      setPriorBad(new Set());
      setPriorGood(new Set());
      setImportant(new Set());
      setMissing(new Set());
      return;
    }
    const next: Record<string, ChipState> = {};
    for (const t of item.tags || []) next[t] = null;
    const fromPriorBad = new Set<string>();
    const fromPriorGood = new Set<string>();
    if (item.labels) {
      for (const [t, v] of Object.entries(item.labels)) {
        if (v === "good" || v === "bad") next[t] = v;
      }
    } else if (item.suggested_labels) {
      for (const [t, v] of Object.entries(item.suggested_labels)) {
        if (v === "good" || v === "bad") {
          next[t] = v;
          if (v === "bad") fromPriorBad.add(t);
          if (v === "good") fromPriorGood.add(t);
        }
      }
    }
    setLabels(next);
    setPriorBad(fromPriorBad);
    setPriorGood(fromPriorGood);

    const tagSet = new Set(item.tags || []);
    const savedImp = new Set((item.important || []).filter((t) => tagSet.has(t)));
    if (importantPass) {
      for (const t of data?.important_vocabulary || []) {
        if (tagSet.has(t)) savedImp.add(t);
      }
    }
    setImportant(savedImp);
    setMissing(new Set((item.missing || []).filter((t) => !tagSet.has(t))));
    setMissingDraft("");
    setFocusTag(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item?.sample_id, importantPass, (data?.important_vocabulary || []).join("\0")]);

  const tagsOrdered = useMemo(() => {
    const starred = tags.filter((t) => important.has(t));
    const rest = tags.filter((t) => !important.has(t));
    return [...starred, ...rest];
  }, [tags, important]);

  const vocabOnSample = useMemo(() => {
    const vocab = data?.important_vocabulary || [];
    return vocab.filter((t) => tags.includes(t));
  }, [data?.important_vocabulary, tags]);

  const missingCandidates = useMemo(() => {
    const fromItem = item?.missing_candidates || [];
    const selected = [...missing];
    const all = new Set([...fromItem, ...selected]);
    return [...all].sort();
  }, [item?.missing_candidates, missing]);

  const clearPrior = (tag: string) => {
    setPriorBad((prev) => {
      if (!prev.has(tag)) return prev;
      const next = new Set(prev);
      next.delete(tag);
      return next;
    });
    setPriorGood((prev) => {
      if (!prev.has(tag)) return prev;
      const next = new Set(prev);
      next.delete(tag);
      return next;
    });
  };

  useEffect(() => {
    const v = videoRef.current;
    if (!v || !item) return;
    const seek = typeof item.excerpt_local_t === "number" ? item.excerpt_local_t : null;
    const kick = () => {
      if (seek != null && Number.isFinite(seek)) {
        try {
          v.currentTime = seek;
        } catch {
          /* ignore */
        }
      }
      void v.play().catch(() => {
        /* autoplay blocked — muted+playsInline usually allows it */
      });
    };
    v.addEventListener("loadedmetadata", kick);
    // Already loaded (cached) — play immediately.
    if (v.readyState >= 1) kick();
    return () => v.removeEventListener("loadedmetadata", kick);
  }, [item?.sample_id, item?.excerpt_video_url, item?.excerpt_local_t]);

  const labeledCount = useMemo(
    () => Object.values(labels).filter((v) => v === "good" || v === "bad").length,
    [labels],
  );

  const toggleImportant = (tag: string) => {
    setImportant((prev) => {
      const next = new Set(prev);
      if (next.has(tag)) next.delete(tag);
      else next.add(tag);
      return next;
    });
  };

  const toggleMissing = (tag: string) => {
    const t = tag.trim().toLowerCase();
    if (!t || tags.includes(t)) return;
    setMissing((prev) => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return next;
    });
  };

  const addMissingDraft = () => {
    const t = missingDraft.trim().toLowerCase();
    if (!t) return;
    if (tags.includes(t)) {
      setError(`“${t}” is already in the model union — mark it ★/good there instead of missing.`);
      return;
    }
    setMissing((prev) => new Set(prev).add(t));
    setMissingDraft("");
    setError(null);
  };

  const persist = async (opts?: { skipped?: boolean; advance?: boolean }) => {
    if (!item || saving) return;
    setSaving(true);
    try {
      const bodyLabels: Record<string, VisionTagLabel> = {};
      for (const [t, v] of Object.entries(labels)) {
        if (v === "good" || v === "bad") bodyLabels[t] = v;
      }
      const res = await saveVisionTagJudgment({
        sample_id: item.sample_id,
        asset_relpath: item.asset_relpath,
        t0: item.t0,
        t1: item.t1,
        slice: item.slice,
        labels: bodyLabels,
        important: [...important].sort(),
        missing: [...missing].sort(),
        skipped: Boolean(opts?.skipped),
      });
      if (res.leaderboard) setBoard(res.leaderboard);
      const fresh = await fetchVisionTagJudgment();
      applyPayload(fresh);
      if (opts?.advance !== false) {
        const list = fresh.items || [];
        const nextIdx = Math.min(idx + 1, Math.max(0, list.length - 1));
        setIdx(nextIdx);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      if (!item || !tagsOrdered.length) return;
      if (e.key === "g" || e.key === "G") {
        e.preventDefault();
        const tag = tagsOrdered[focusTag];
        if (tag) {
          setLabels((prev) => ({ ...prev, [tag]: "good" }));
          clearPrior(tag);
        }
      } else if (e.key === "b" || e.key === "B") {
        e.preventDefault();
        const tag = tagsOrdered[focusTag];
        if (tag) {
          setLabels((prev) => ({ ...prev, [tag]: "bad" }));
          clearPrior(tag);
        }
      } else if (e.key === "i" || e.key === "I") {
        e.preventDefault();
        const tag = tagsOrdered[focusTag];
        if (tag) toggleImportant(tag);
      } else if (e.key === "ArrowRight" || e.key === "ArrowDown") {
        e.preventDefault();
        setFocusTag((i) => Math.min(tagsOrdered.length - 1, i + 1));
      } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
        e.preventDefault();
        setFocusTag((i) => Math.max(0, i - 1));
      } else if (e.key === "n" || e.key === "N") {
        e.preventDefault();
        void persist({ advance: true });
      } else if (e.key === "p" || e.key === "P") {
        e.preventDefault();
        setIdx((i) => Math.max(0, i - 1));
      } else if (e.key === "s" || e.key === "S") {
        e.preventDefault();
        void persist({ skipped: true, advance: true });
      } else if (e.key === " " || e.key === "Enter") {
        e.preventDefault();
        const tag = tagsOrdered[focusTag];
        if (tag) {
          setLabels((prev) => ({ ...prev, [tag]: cycleLabel(prev[tag] ?? null) }));
          clearPrior(tag);
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- persist closes over current labels/item
  }, [item, tagsOrdered, focusTag, labels, important, missing, passMode, idx, saving]);

  const videoSrc = item?.excerpt_video_url || item?.video_url || null;

  return (
    <div className="tag-judge layout">
      <PageHeader
        title="Tag judge"
        subtitle="Blind good/bad on union tags — model names hidden until score. Experiment only, not V5 HITL."
        actions={
          <>
            <a className="btn" href="/vision/slices">
              ← Vision slices
            </a>
            <button
              type="button"
              className={importantPass ? "btn btn-primary" : "btn"}
              disabled={loading || !items.length}
              title="Re-walk the queue from sample 1. ★ tags you marked elsewhere are prefilled and pinned to the front."
              onClick={() => {
                setPassMode("important");
                setIdx(0);
              }}
            >
              ★ Important pass
              {importantCoverage.missing ? ` (${importantCoverage.missing} need ★)` : " (all have ★)"}
            </button>
            <button
              type="button"
              className={missingPass ? "btn btn-primary" : "btn"}
              disabled={loading || !items.length}
              title="For ★ important tags absent from this sample: mark those that should have been emitted."
              onClick={() => {
                setPassMode("missing");
                setIdx(0);
              }}
            >
              Missing pass
              {missingCoverage.withMiss ? ` (${missingCoverage.withMiss} have FN)` : ""}
            </button>
            {passMode !== "normal" ? (
              <button type="button" className="btn" onClick={() => setPassMode("normal")}>
                Exit pass
              </button>
            ) : null}
            <button
              type="button"
              className="btn"
              disabled={loading}
              onClick={() => {
                setLoading(true);
                void fetchVisionTagJudgment()
                  .then(applyPayload)
                  .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
                  .finally(() => setLoading(false));
              }}
            >
              Refresh
            </button>
            {doneCount >= minScore ? (
              <button
                type="button"
                className="btn btn-primary"
                disabled={saving}
                onClick={() => void persist({ advance: false })}
              >
                Score now
              </button>
            ) : null}
          </>
        }
      />

      {importantPass ? (
        <div className="tag-judge-banner">
          Important pass — walking 1→{importantCoverage.total}. Your important vocabulary (
          {(data?.important_vocabulary || []).join(", ") || "none yet"}) is prefilled and ★ chips are
          pinned to the front when they appear. <kbd>i</kbd> toggle · <kbd>n</kbd> save+next ·{" "}
          <kbd>p</kbd> prev. Coverage: {importantCoverage.withStar}/{importantCoverage.total} samples
          already had ★ saved.
        </div>
      ) : null}
      {missingPass ? (
        <div className="tag-judge-banner tag-judge-banner--missing">
          Missing pass — only ★ <em>important</em> tags. For each important tag absent from this
          sample’s union, mark it if it <em>should have been</em> emitted (gold FN). Leave unmarked
          if it correctly does not belong here. <kbd>n</kbd> save+next · <kbd>p</kbd> prev. Samples
          with FN marks: {missingCoverage.withMiss}/{missingCoverage.total}.
        </div>
      ) : null}
      {error ? <div className="tag-judge-error">{error}</div> : null}
      {loading && !data ? <div className="tag-judge-empty">Loading…</div> : null}
      {!loading && data && !items.length ? (
        <div className="tag-judge-empty">
          No judgment queue. Build one with{" "}
          <code>python3 workspace/scripts/vision_tag_judgment_queue.py</code>
        </div>
      ) : null}

      {item || board ? (
        <div className="tag-judge-split">
          <div className="tag-judge-body">
            {item ? (
              <>
                <div className="tag-judge-media">
                  {videoSrc ? (
                    <video
                      key={videoSrc}
                      ref={videoRef}
                      className="tag-judge-video"
                      src={videoSrc}
                      controls
                      muted
                      autoPlay
                      playsInline
                      loop
                    />
                  ) : (
                    <div className="tag-judge-empty">No video URL</div>
                  )}
                  {item.frame_url ? (
                    <img className="tag-judge-frame" src={item.frame_url} alt="" />
                  ) : null}
                  <div className="tag-judge-meta">
                    <div className="tag-judge-meta__name" title={item.asset_relpath}>
                      {item.basename || item.asset_relpath}
                    </div>
                    <div className="tag-judge-meta__range">
                      {typeof item.t0 === "number" ? item.t0.toFixed(1) : "?"}–
                      {typeof item.t1 === "number" ? item.t1.toFixed(1) : "?"}s · {item.slice || "window"}
                    </div>
                    <div className="tag-judge-progress">
                      Sample {idx + 1}/{items.length} · done {doneCount}/{data?.total_count ?? items.length} · this{" "}
                      {labeledCount}/{tags.length} labeled · ★{important.size}
                      {vocabOnSample.length ? ` · vocab-here ${vocabOnSample.length}` : ""}
                      {(data?.label_priors?.default_bad_tags || []).length
                        ? ` · ${data?.label_priors?.default_bad_tags?.length} prior-bad`
                        : ""}
                      {(data?.label_priors?.default_good_tags || []).length
                        ? ` · ${data?.label_priors?.default_good_tags?.length} prior-good`
                        : ""}
                    </div>
                    {importantPass && vocabOnSample.length ? (
                      <div className="tag-judge-vocab">
                        Important vocab on this sample:{" "}
                        {vocabOnSample.map((t) => (
                          <button
                            key={t}
                            type="button"
                            className={important.has(t) ? "tag-judge-chip is-important is-good" : "tag-judge-chip"}
                            onClick={() => toggleImportant(t)}
                          >
                            <span className="tag-judge-chip__star" aria-hidden="true">★</span>
                            {t}
                          </button>
                        ))}
                      </div>
                    ) : null}
                    {importantPass && !vocabOnSample.length ? (
                      <div className="tag-judge-vocab tag-judge-vocab--empty">
                        None of your important vocabulary appears in this sample’s tag union — ★ something
                        new with <kbd>i</kbd>, or <kbd>n</kbd> to continue.
                      </div>
                    ) : null}
                    {missingPass ? (
                      <div className="tag-judge-missing">
                        <div className="tag-judge-missing__title">
                          ★ Important tags absent here — mark if they should have been added (
                          {missing.size} FN)
                        </div>
                        <div className="tag-judge-missing__candidates">
                          {missingCandidates.length ? (
                            missingCandidates.map((t) => (
                              <button
                                key={t}
                                type="button"
                                className={
                                  missing.has(t)
                                    ? "tag-judge-chip is-missing is-selected"
                                    : "tag-judge-chip is-missing-candidate"
                                }
                                onClick={() => toggleMissing(t)}
                                title={
                                  missing.has(t)
                                    ? "Should have been present — click to clear"
                                    : "Should have been present on this sample"
                                }
                              >
                                {t}
                              </button>
                            ))
                          ) : (data?.important_vocabulary || []).length ? (
                            <span className="tag-judge-vocab--empty">
                              Every ★ important tag already appears in this sample’s union —{" "}
                              <kbd>n</kbd> to continue.
                            </span>
                          ) : (
                            <span className="tag-judge-vocab--empty">
                              No ★ important vocabulary yet — finish the Important pass first.
                            </span>
                          )}
                        </div>
                        <form
                          className="tag-judge-missing__add"
                          onSubmit={(e) => {
                            e.preventDefault();
                            addMissingDraft();
                          }}
                        >
                          <input
                            type="text"
                            value={missingDraft}
                            onChange={(e) => setMissingDraft(e.target.value)}
                            placeholder="Rare: add another ★-class miss…"
                            aria-label="Add important missing tag"
                          />
                          <button type="submit" className="btn">
                            Add
                          </button>
                        </form>
                      </div>
                    ) : null}
                    <div className="tag-judge-keys">
                      click / space cycle good·bad · i / Alt+click important · g good · b bad · ←→ focus · n
                      save+next · p prev · s skip · chronic priors prefill good/bad
                    </div>
                  </div>
                </div>

                <div className="tag-judge-tags" role="list">
                  {tagsOrdered.map((tag, i) => {
                    const st = labels[tag] ?? null;
                    const isImp = important.has(tag);
                    const isPriorBad = priorBad.has(tag) && st === "bad";
                    const isPriorGood = priorGood.has(tag) && st === "good";
                    const priorTitle = isPriorBad
                      ? "Defaulted bad from history (override if actually correct)"
                      : isPriorGood
                        ? "Defaulted good from history (override if wrong)"
                        : undefined;
                    return (
                      <button
                        key={tag}
                        type="button"
                        role="listitem"
                        title={priorTitle}
                        className={[
                          "tag-judge-chip",
                          st === "good" ? "is-good" : "",
                          st === "bad" ? "is-bad" : "",
                          isPriorBad ? "is-prior-bad" : "",
                          isPriorGood ? "is-prior-good" : "",
                          isImp ? "is-important" : "",
                          i === focusTag ? "is-focus" : "",
                        ]
                          .filter(Boolean)
                          .join(" ")}
                        onClick={(e) => {
                          setFocusTag(i);
                          if (e.altKey) {
                            toggleImportant(tag);
                            return;
                          }
                          setLabels((prev) => ({ ...prev, [tag]: cycleLabel(prev[tag] ?? null) }));
                          clearPrior(tag);
                        }}
                      >
                        {isImp ? <span className="tag-judge-chip__star" aria-hidden="true">★</span> : null}
                        {tag}
                      </button>
                    );
                  })}
                </div>

                <div className="tag-judge-actions">
                  <button type="button" className="btn" disabled={idx <= 0} onClick={() => setIdx((i) => i - 1)}>
                    Prev
                  </button>
                  <button
                    type="button"
                    className="btn"
                    disabled={saving}
                    onClick={() => void persist({ skipped: true, advance: true })}
                  >
                    Skip
                  </button>
                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={saving}
                    onClick={() => void persist({ advance: true })}
                  >
                    Save & next
                  </button>
                </div>
              </>
            ) : (
              <div className="tag-judge-empty">No sample selected.</div>
            )}
          </div>

          <aside className="tag-judge-side">
            {board ? (
              <LeaderboardTable board={board} />
            ) : (
              <div className="tag-judge-empty">Leaderboard appears after you save a few samples.</div>
            )}
          </aside>
        </div>
      ) : null}
    </div>
  );
}
