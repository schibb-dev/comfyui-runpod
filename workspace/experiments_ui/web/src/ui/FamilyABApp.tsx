import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchAbExperiment,
  fetchAbExperiments,
  fetchShapeFactoryFamilies,
  fetchShapeFactoryWorkProducts,
  judgeAbExperiment,
  queueAbExperiment,
} from "./api";
import { AppetitePreviewBadge } from "./AppetitePreviewBadge";
import { MediaCompareStage } from "./compare/MediaCompareStage";
import type {
  AbCatalogDisposition,
  AbExperiment,
  WorkProductItem,
} from "./types";

type AbView = "queue" | "status" | "evaluate";

const DISPOSITIONS: { value: AbCatalogDisposition; label: string; hint: string }[] = [
  { value: "no_distinction", label: "No distinction", hint: "Not worth tracking" },
  { value: "keep_as_variant", label: "Keep as variant", hint: "Controllable mode inside base" },
  { value: "improve_base", label: "Improve base", hint: "Absorb into base defaults" },
  { value: "new_family", label: "New family", hint: "Separate enrolled line" },
  { value: "inconclusive", label: "Inconclusive", hint: "Need more pairs" },
];

const DISTINGUISHING = new Set<AbCatalogDisposition>([
  "keep_as_variant",
  "improve_base",
  "new_family",
]);

function firstUrl(side?: AbExperiment["job_a"]): string | null {
  const urls = side?.output_urls || [];
  return urls[0] || null;
}

function exemplarThumb(it: WorkProductItem): string | null {
  return it.output_thumb_url || it.parent_output_thumb_url || null;
}

function exemplarMedia(it: WorkProductItem): string | null {
  return it.output_url || it.parent_output_url || exemplarThumb(it);
}

function isVideoUrl(url: string | null | undefined): boolean {
  return /\.(mp4|webm|mov)(\?|$)/i.test(String(url || ""));
}

function parseView(raw: string | null): AbView {
  if (raw === "status" || raw === "evaluate" || raw === "queue") return raw;
  return "status";
}

function setUrlView(view: AbView, abId?: string | null) {
  const sp = new URLSearchParams(window.location.search);
  sp.set("view", view);
  if (abId) sp.set("ab", abId);
  else sp.delete("ab");
  const next = `${window.location.pathname}?${sp.toString()}`;
  window.history.replaceState({}, "", next);
}

function statusLabel(status?: string | null): string {
  return String(status || "unknown");
}

function canEvaluate(row: AbExperiment | null | undefined): boolean {
  if (!row) return false;
  const a = firstUrl(row.job_a);
  const b = firstUrl(row.job_b);
  return Boolean(a && b) || row.status === "ready" || row.status === "judged";
}

function ExemplarPreview({ item, large }: { item: WorkProductItem | null; large?: boolean }) {
  if (!item) {
    return <div className={`family-ab-exemplar-preview ${large ? "large" : ""} empty`}>No exemplar</div>;
  }
  const media = exemplarMedia(item);
  const thumb = exemplarThumb(item);
  if (!media && !thumb) {
    return (
      <div className={`family-ab-exemplar-preview ${large ? "large" : ""} empty`}>
        <span>{item.family_slug || "?"}</span>
        <small>No preview media</small>
      </div>
    );
  }
  const src = large && isVideoUrl(media) ? media! : thumb || media!;
  if (large && isVideoUrl(src)) {
    return (
      <div className="family-ab-exemplar-preview large">
        <video src={src} controls playsInline muted loop preload="metadata" />
        <AppetitePreviewBadge relpath={item.output_relpath} />
      </div>
    );
  }
  return (
    <div className={`family-ab-exemplar-preview ${large ? "large" : ""}`}>
      {isVideoUrl(src) ? (
        <video src={src} muted playsInline preload="metadata" />
      ) : (
        <img src={src} alt={item.job_key} loading="lazy" />
      )}
      <AppetitePreviewBadge relpath={item.output_relpath} size={large ? "default" : "sm"} />
    </div>
  );
}

function SideStatus({ label, side }: { label: string; side?: AbExperiment["job_a"] }) {
  const urls = side?.output_urls || [];
  return (
    <div className="family-ab-side-status">
      <div className="family-ab-side-status__head">
        <strong>{label}</strong>
        <span className={`family-ab-pill status-${statusLabel(side?.status)}`}>
          {statusLabel(side?.status)}
        </span>
      </div>
      <div className="muted mono">{side?.job_key || "—"}</div>
      {side?.prompt_id ? <div className="muted">prompt {side.prompt_id}</div> : null}
      {side?.error ? <div className="family-ab-error inline">{side.error}</div> : null}
      {urls[0] ? (
        <div className="family-ab-side-status__thumb">
          {isVideoUrl(urls[0]) ? (
            <video src={urls[0]} muted playsInline preload="metadata" />
          ) : (
            <img src={urls[0]} alt={label} />
          )}
        </div>
      ) : (
        <div className="muted">No output yet</div>
      )}
    </div>
  );
}

export function FamilyABApp() {
  const params = useMemo(() => new URLSearchParams(window.location.search), []);
  const [view, setView] = useState<AbView>(() => {
    const explicit = params.get("view");
    if (explicit) return parseView(explicit);
    if (params.get("job") || params.get("job_key")) return "queue";
    if (params.get("ab")) return "status";
    return "status";
  });
  const [families, setFamilies] = useState<string[]>([]);
  const [exemplars, setExemplars] = useState<WorkProductItem[]>([]);
  const [exemplarFilter, setExemplarFilter] = useState("");
  const [jobKey, setJobKey] = useState(params.get("job") || params.get("job_key") || "");
  const [familyA, setFamilyA] = useState(params.get("family_a") || "");
  const [familyB, setFamilyB] = useState(params.get("family_b") || "");
  const [label, setLabel] = useState("");
  const [list, setList] = useState<AbExperiment[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(params.get("ab") || null);
  const [selected, setSelected] = useState<AbExperiment | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [disposition, setDisposition] = useState<AbCatalogDisposition>("keep_as_variant");
  const [observedEffect, setObservedEffect] = useState("");
  const [embodySide, setEmbodySide] = useState<"a" | "b">("b");
  const [notes, setNotes] = useState("");

  const goView = useCallback((next: AbView, abId?: string | null) => {
    setView(next);
    if (abId !== undefined) setSelectedId(abId);
    setUrlView(next, abId === undefined ? selectedId : abId);
  }, [selectedId]);

  const refreshList = useCallback(async () => {
    const res = await fetchAbExperiments({ limit: 60 });
    setList(res.experiments || []);
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [fam, wp] = await Promise.all([
          fetchShapeFactoryFamilies().catch(() => ({ ok: false as const, families: [] })),
          fetchShapeFactoryWorkProducts({ limit: 80 }).catch(() => ({ ok: false as const, items: [] })),
        ]);
        if (cancelled) return;
        const slugs = (fam.families || [])
          .map((f) => String(f?.slug || "").trim())
          .filter(Boolean);
        setFamilies(Array.from(new Set(slugs)).sort());
        const items = ((wp.items || []) as WorkProductItem[]).filter((it) => it?.job_key);
        items.sort((a, b) => {
          const score = (it: WorkProductItem) =>
            (it.output_url ? 4 : 0) + (it.output_thumb_url ? 2 : 0) + (it.parent_output_url ? 1 : 0);
          return score(b) - score(a);
        });
        setExemplars(items);
        await refreshList();
      } catch (e: any) {
        if (!cancelled) setError(String(e?.message || e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [refreshList]);

  useEffect(() => {
    if (!selectedId) {
      setSelected(null);
      return;
    }
    let cancelled = false;
    const tick = async () => {
      try {
        const res = await fetchAbExperiment(selectedId);
        if (cancelled) return;
        setSelected(res.ab || null);
        const j = res.ab?.judgment;
        if (j) {
          setDisposition((j.catalog_disposition as AbCatalogDisposition) || "inconclusive");
          setObservedEffect(String(j.observed_effect || ""));
          setEmbodySide((j.embody_side as "a" | "b") || "b");
          setNotes(String(j.notes || ""));
        }
        await refreshList();
      } catch (e: any) {
        if (!cancelled) setError(String(e?.message || e));
      }
    };
    void tick();
    const poll =
      view === "status" ||
      (view === "evaluate" && selected?.status !== "ready" && selected?.status !== "judged");
    if (!poll) return () => {
      cancelled = true;
    };
    const id = window.setInterval(() => {
      void tick();
    }, 4000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [selectedId, selected?.status, view, refreshList]);

  useEffect(() => {
    if (!familyA && jobKey) {
      const hit = exemplars.find((e) => e.job_key === jobKey);
      if (hit?.family_slug) setFamilyA(String(hit.family_slug));
    }
  }, [jobKey, exemplars, familyA]);

  const selectedExemplar = useMemo(
    () => exemplars.find((e) => e.job_key === jobKey) || null,
    [exemplars, jobKey],
  );

  const filteredExemplars = useMemo(() => {
    const q = exemplarFilter.trim().toLowerCase();
    if (!q) return exemplars;
    return exemplars.filter((it) => {
      const hay = `${it.family_slug || ""} ${it.job_key} ${it.output_relpath || ""}`.toLowerCase();
      return hay.includes(q);
    });
  }, [exemplars, exemplarFilter]);

  const evaluateQueue = useMemo(
    () => list.filter((row) => canEvaluate(row) || row.status === "ready" || row.status === "judged"),
    [list],
  );

  function pickExemplar(it: WorkProductItem) {
    setJobKey(it.job_key);
    if (it.family_slug) setFamilyA(String(it.family_slug));
  }

  async function onQueue() {
    setBusy(true);
    setError(null);
    try {
      if (!jobKey.trim()) throw new Error("Pick an exemplar job");
      if (!familyB.trim()) throw new Error("family_b is required");
      const res = await queueAbExperiment({
        job_key: jobKey.trim(),
        family_a: familyA.trim() || undefined,
        family_b: familyB.trim(),
        label: label.trim() || undefined,
        seed_mode: "same",
      });
      const id = res.ab?.ab_id || null;
      await refreshList();
      goView("status", id);
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  }

  async function onJudge() {
    if (!selectedId) return;
    setBusy(true);
    setError(null);
    try {
      if (DISTINGUISHING.has(disposition) && !observedEffect.trim()) {
        throw new Error("Name the observed effect (e.g. more frenetic)");
      }
      const res = await judgeAbExperiment(selectedId, {
        catalog_disposition: disposition,
        observed_effect: observedEffect.trim() || undefined,
        embody_side: DISTINGUISHING.has(disposition) ? embodySide : undefined,
        notes: notes.trim() || undefined,
      });
      setSelected(res.ab || null);
      await refreshList();
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  }

  const urlA = firstUrl(selected?.job_a);
  const urlB = firstUrl(selected?.job_b);
  const ready = Boolean(urlA && urlB);

  const viewHints: Record<AbView, string> = {
    queue: "Pick an exemplar and queue a locked A/B pair.",
    status: "Track experiment progress and side job status.",
    evaluate: "Compare outputs and record a catalog distinction.",
  };

  return (
    <div className="layout family-ab-app">
      <header className="family-ab-header">
        <h1>Family A/B</h1>
        <p className="muted">{viewHints[view]}</p>
        <div className="segmented family-ab-views" role="tablist" aria-label="Family A/B views">
          <button
            type="button"
            role="tab"
            aria-selected={view === "queue"}
            className={`seg-btn ${view === "queue" ? "active" : ""}`}
            onClick={() => goView("queue", selectedId)}
          >
            Queue
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={view === "status"}
            className={`seg-btn ${view === "status" ? "active" : ""}`}
            onClick={() => goView("status", selectedId)}
          >
            Status
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={view === "evaluate"}
            className={`seg-btn ${view === "evaluate" ? "active" : ""}`}
            onClick={() => goView("evaluate", selectedId)}
          >
            Evaluate
          </button>
        </div>
      </header>

      {error ? <div className="family-ab-error">{error}</div> : null}

      {view === "queue" ? (
        <section className="family-ab-compose">
          <h2>Queue from exemplar</h2>
          <div className="family-ab-exemplar-picker">
            <div className="family-ab-exemplar-picker__head">
              <label className="family-ab-exemplar-filter">
                Filter
                <input
                  type="search"
                  value={exemplarFilter}
                  onChange={(e) => setExemplarFilter(e.target.value)}
                  placeholder="Family or job key…"
                />
              </label>
              <span className="muted">
                {filteredExemplars.length}/{exemplars.length} recent work products
              </span>
            </div>
            <div className="family-ab-exemplar-grid" role="listbox" aria-label="Exemplar work products">
              {filteredExemplars.map((it) => {
                const active = it.job_key === jobKey;
                return (
                  <button
                    key={it.job_key}
                    type="button"
                    role="option"
                    aria-selected={active}
                    className={`family-ab-exemplar-card ${active ? "active" : ""}`}
                    onClick={() => pickExemplar(it)}
                    title={it.job_key}
                  >
                    <ExemplarPreview item={it} />
                    <div className="family-ab-exemplar-card__meta">
                      <strong>{it.family_slug || "?"}</strong>
                      <span>{it.job_key}</span>
                    </div>
                  </button>
                );
              })}
              {!filteredExemplars.length ? (
                <p className="muted">No work products match this filter.</p>
              ) : null}
            </div>
            <div className="family-ab-exemplar-selected">
              <ExemplarPreview item={selectedExemplar} large />
              <div className="family-ab-exemplar-selected__meta">
                {selectedExemplar ? (
                  <>
                    <div>
                      <strong>{selectedExemplar.family_slug}</strong>
                    </div>
                    <div className="muted mono">{selectedExemplar.job_key}</div>
                    {selectedExemplar.output_relpath ? (
                      <div className="muted">{selectedExemplar.output_relpath}</div>
                    ) : null}
                  </>
                ) : (
                  <p className="muted">Pick an exemplar above to lock shared inputs.</p>
                )}
              </div>
            </div>
          </div>
          <div className="family-ab-form">
            <label>
              Family A (base)
              <input
                list="family-ab-slugs"
                value={familyA}
                onChange={(e) => setFamilyA(e.target.value)}
                placeholder="defaults to exemplar family"
              />
            </label>
            <label>
              Family B (candidate)
              <input
                list="family-ab-slugs"
                value={familyB}
                onChange={(e) => setFamilyB(e.target.value)}
                placeholder="required"
              />
            </label>
            <label>
              Label
              <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="optional" />
            </label>
            <datalist id="family-ab-slugs">
              {families.map((s) => (
                <option key={s} value={s} />
              ))}
            </datalist>
            <button type="button" className="primary" disabled={busy || !jobKey} onClick={() => void onQueue()}>
              Queue A/B pair
            </button>
          </div>
        </section>
      ) : null}

      {view === "status" ? (
        <div className="family-ab-main">
          <aside className="family-ab-list">
            <div className="family-ab-list__head">
              <h2>Experiments</h2>
              <button type="button" className="icon-btn" onClick={() => void refreshList()} disabled={busy}>
                Refresh
              </button>
            </div>
            <ul>
              {list.map((row) => (
                <li key={row.ab_id}>
                  <button
                    type="button"
                    className={selectedId === row.ab_id ? "active" : ""}
                    onClick={() => goView("status", row.ab_id)}
                  >
                    <strong>
                      {row.family_a} vs {row.family_b}
                    </strong>
                    <span>
                      <span className={`family-ab-pill status-${statusLabel(row.status)}`}>
                        {statusLabel(row.status)}
                      </span>{" "}
                      · {row.ab_id}
                    </span>
                  </button>
                </li>
              ))}
              {!list.length ? <li className="muted">No experiments yet — queue one first.</li> : null}
            </ul>
          </aside>

          <section className="family-ab-detail">
            {!selected ? (
              <p className="muted">Select an experiment to see status.</p>
            ) : (
              <>
                <div className="family-ab-meta">
                  <div>
                    <strong>{selected.ab_id}</strong>{" "}
                    <span className={`family-ab-pill status-${statusLabel(selected.status)}`}>
                      {statusLabel(selected.status)}
                    </span>
                  </div>
                  {selected.label ? <div>{selected.label}</div> : null}
                  <div className="muted">
                    exemplar {selected.exemplar?.job_key || "—"}
                    {selected.exemplar?.family_slug ? ` · ${selected.exemplar.family_slug}` : ""}
                  </div>
                </div>

                <div className="family-ab-status-grid">
                  <SideStatus label={`A · ${selected.family_a}`} side={selected.job_a} />
                  <SideStatus label={`B · ${selected.family_b}`} side={selected.job_b} />
                </div>

                {selected.notes_engine?.length ? (
                  <ul className="family-ab-notes">
                    {selected.notes_engine.map((n, i) => (
                      <li key={i}>{n}</li>
                    ))}
                  </ul>
                ) : null}

                {selected.judgment ? (
                  <div className="family-ab-judgment-summary">
                    <h3>Judgment</h3>
                    <div>
                      {String(selected.judgment.catalog_disposition)}
                      {selected.judgment.observed_effect
                        ? ` · “${selected.judgment.observed_effect}”`
                        : ""}
                      {selected.judgment.embody_side
                        ? ` · embody ${String(selected.judgment.embody_side).toUpperCase()}`
                        : ""}
                    </div>
                  </div>
                ) : null}

                <div className="family-ab-status-actions">
                  <button
                    type="button"
                    className="primary"
                    disabled={!canEvaluate(selected)}
                    onClick={() => goView("evaluate", selected.ab_id)}
                  >
                    Open evaluate
                  </button>
                  {!canEvaluate(selected) ? (
                    <span className="muted">Waiting for both sides to finish outputs.</span>
                  ) : null}
                </div>
              </>
            )}
          </section>
        </div>
      ) : null}

      {view === "evaluate" ? (
        <div className="family-ab-main">
          <aside className="family-ab-list">
            <h2>Ready to evaluate</h2>
            <ul>
              {evaluateQueue.map((row) => (
                <li key={row.ab_id}>
                  <button
                    type="button"
                    className={selectedId === row.ab_id ? "active" : ""}
                    onClick={() => goView("evaluate", row.ab_id)}
                  >
                    <strong>
                      {row.family_a} vs {row.family_b}
                    </strong>
                    <span>
                      <span className={`family-ab-pill status-${statusLabel(row.status)}`}>
                        {statusLabel(row.status)}
                      </span>
                      {row.judgment?.observed_effect ? ` · ${row.judgment.observed_effect}` : ""}
                    </span>
                  </button>
                </li>
              ))}
              {!evaluateQueue.length ? (
                <li className="muted">Nothing ready yet — check Status while jobs run.</li>
              ) : null}
            </ul>
          </aside>

          <section className="family-ab-detail">
            {!selected ? (
              <p className="muted">Select an experiment to evaluate.</p>
            ) : !ready ? (
              <div>
                <p className="muted">Both outputs are not ready yet.</p>
                <button type="button" onClick={() => goView("status", selected.ab_id)}>
                  Back to status
                </button>
              </div>
            ) : (
              <>
                <div className="family-ab-meta">
                  <div>
                    <strong>
                      {selected.family_a} vs {selected.family_b}
                    </strong>{" "}
                    · {selected.ab_id}
                  </div>
                </div>

                <MediaCompareStage
                  sideA={{ id: "a", label: selected.family_a || "A", url: urlA }}
                  sideB={{ id: "b", label: selected.family_b || "B", url: urlB }}
                />

                <div className="family-ab-judgment">
                  <h2>Does this warrant a distinction?</h2>
                  <p className="muted">
                    Name the controllable quality — not which side won. e.g. more frenetic → keep as
                    variant.
                  </p>
                  <div className="family-ab-disp">
                    {DISPOSITIONS.map((d) => (
                      <label key={d.value} className={disposition === d.value ? "active" : ""}>
                        <input
                          type="radio"
                          name="disposition"
                          checked={disposition === d.value}
                          onChange={() => setDisposition(d.value)}
                        />
                        <span>
                          {d.label}
                          <small>{d.hint}</small>
                        </span>
                      </label>
                    ))}
                  </div>
                  {DISTINGUISHING.has(disposition) ? (
                    <div className="family-ab-form">
                      <label>
                        Observed effect
                        <input
                          value={observedEffect}
                          onChange={(e) => setObservedEffect(e.target.value)}
                          placeholder='e.g. "more frenetic"'
                        />
                      </label>
                      <label>
                        Embodies effect
                        <select
                          value={embodySide}
                          onChange={(e) => setEmbodySide(e.target.value as "a" | "b")}
                        >
                          <option value="a">A · {selected.family_a}</option>
                          <option value="b">B · {selected.family_b}</option>
                        </select>
                      </label>
                    </div>
                  ) : null}
                  <label>
                    Notes
                    <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={2} />
                  </label>
                  <button
                    type="button"
                    className="primary"
                    disabled={busy || !ready}
                    onClick={() => void onJudge()}
                  >
                    Save judgment
                  </button>
                  {selected.judgment ? (
                    <pre className="family-ab-judgment-preview">
                      {JSON.stringify(selected.judgment, null, 2)}
                    </pre>
                  ) : null}
                </div>
              </>
            )}
          </section>
        </div>
      ) : null}
    </div>
  );
}
