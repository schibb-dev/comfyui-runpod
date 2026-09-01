import React, { useEffect, useMemo, useState } from "react";
import {
  fetchFamilyDiscoveryIndex,
  fetchFamilyDiscoveryProp,
  updateFamilyDiscoveryProp,
} from "./api";
import type {
  FamilyDiscoveryIndexResponse,
  FamilyDiscoveryIndexRow,
  FamilyDiscoveryProp,
  FamilyDiscoveryStatus,
} from "./types";

const STATUS_OPTIONS: { value: FamilyDiscoveryStatus; label: string }[] = [
  { value: "pending_review", label: "Pending review" },
  { value: "new_family", label: "New family" },
  { value: "merge", label: "Merge into enrolled" },
  { value: "skip", label: "Skip" },
  { value: "enrolled", label: "Enrolled (CLI)" },
];

function statusClass(status: string | null | undefined): string {
  const s = String(status || "pending_review").toLowerCase();
  if (s === "new_family") return "family-review-status--new";
  if (s === "merge") return "family-review-status--merge";
  if (s === "skip") return "family-review-status--skip";
  if (s === "enrolled") return "family-review-status--enrolled";
  return "family-review-status--pending";
}

function formatOutputDateRange(
  first?: string | null,
  last?: string | null,
  days?: number | null
): string {
  const a = String(first || "").trim();
  const b = String(last || "").trim();
  if (!a && !b) return "";
  if (a && b && a !== b) {
    const dayBit = typeof days === "number" && days > 0 ? ` (${days}d)` : "";
    return `${a} → ${b}${dayBit}`;
  }
  return a || b;
}

export function FamilyDiscoveryReview() {
  const [index, setIndex] = useState<FamilyDiscoveryIndexResponse | null>(null);
  const [selectedId, setSelectedId] = useState<string>("");
  const [prop, setProp] = useState<FamilyDiscoveryProp | null>(null);
  const [enrolled, setEnrolled] = useState<string[]>([]);
  const [statusFilter, setStatusFilter] = useState<string>("pending_review");
  const [loadingList, setLoadingList] = useState(false);
  const [loadingProp, setLoadingProp] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const [status, setStatus] = useState<FamilyDiscoveryStatus>("pending_review");
  const [slug, setSlug] = useState("");
  const [nearest, setNearest] = useState("");
  const [notes, setNotes] = useState("");

  const [viewerUrl, setViewerUrl] = useState<string | null>(null);
  const [viewerTitle, setViewerTitle] = useState("");

  const reloadIndex = async (preferId?: string) => {
    setLoadingList(true);
    setError("");
    try {
      const next = await fetchFamilyDiscoveryIndex();
      setIndex(next);
      if (next.enrolled_families?.length) setEnrolled(next.enrolled_families);
      const rows = next.proposals || [];
      const pick =
        (preferId && rows.some((r) => r.id === preferId) && preferId) ||
        rows.find((r) => String(r.status || "") === "pending_review")?.id ||
        rows[0]?.id ||
        "";
      if (pick) setSelectedId(pick);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingList(false);
    }
  };

  useEffect(() => {
    void reloadIndex();
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setProp(null);
      return;
    }
    let cancelled = false;
    setLoadingProp(true);
    setError("");
    setNotice("");
    void (async () => {
      try {
        const res = await fetchFamilyDiscoveryProp(selectedId);
        if (cancelled) return;
        const p = res.prop || null;
        setProp(p);
        if (res.enrolled_families?.length) setEnrolled(res.enrolled_families);
        setStatus((String(p?.status || "pending_review") as FamilyDiscoveryStatus) || "pending_review");
        setSlug(String(p?.proposed_family_slug || ""));
        setNearest(String(p?.nearest_enrolled || ""));
        setNotes(String(p?.operator_notes || ""));
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setLoadingProp(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  const filtered = useMemo(() => {
    const rows = index?.proposals || [];
    if (statusFilter === "all") return rows;
    return rows.filter((r) => String(r.status || "pending_review") === statusFilter);
  }, [index, statusFilter]);

  const counts = useMemo(() => {
    const map: Record<string, number> = { all: 0 };
    for (const r of index?.proposals || []) {
      map.all += 1;
      const s = String(r.status || "pending_review");
      map[s] = (map[s] || 0) + 1;
    }
    return map;
  }, [index]);

  const save = async () => {
    if (!selectedId) return;
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const res = await updateFamilyDiscoveryProp(selectedId, {
        status,
        proposed_family_slug: slug.trim() || null,
        nearest_enrolled: nearest.trim() || null,
        operator_notes: notes.trim() || null,
        operator_decision: status === "pending_review" ? null : status,
      });
      setProp(res.prop || null);
      setNotice("Saved");
      await reloadIndex(selectedId);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const rep = prop?.representative;
  const samples = prop?.sample_videos || [];

  return (
    <div className="family-review">
      <div className="family-review__intro factory-muted">
        Cluster proposals from <code>docs/family_discovery/</code>. Sample clips are matched by{" "}
        <strong>graph fingerprint</strong> (not output names). Decide new family / merge / skip;
        enroll stays a CLI step (<code>shape_factory_family_discovery.py enroll</code>).
        {index?.generated_at ? <> · generated {index.generated_at}</> : null}
        {typeof index?.covered_clusters === "number" ? (
          <>
            {" "}
            · covered {index.covered_clusters} / uncovered {index.uncovered_clusters ?? "?"}
          </>
        ) : null}
      </div>

      <div className="family-review__toolbar">
        <label className="family-review__filter">
          Status
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="pending_review">Pending ({counts.pending_review || 0})</option>
            <option value="new_family">New family ({counts.new_family || 0})</option>
            <option value="merge">Merge ({counts.merge || 0})</option>
            <option value="skip">Skip ({counts.skip || 0})</option>
            <option value="enrolled">Enrolled ({counts.enrolled || 0})</option>
            <option value="all">All ({counts.all || 0})</option>
          </select>
        </label>
        <button type="button" disabled={loadingList} onClick={() => void reloadIndex(selectedId)}>
          {loadingList ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {error ? <div className="factory-error">{error}</div> : null}
      {notice ? <div className="family-review__notice">{notice}</div> : null}

      <div className="family-review__layout">
        <aside className="family-review__list" aria-label="Proposals">
          {filtered.map((row: FamilyDiscoveryIndexRow) => (
            <button
              key={row.id}
              type="button"
              className={
                "family-review__row" + (row.id === selectedId ? " family-review__row--active" : "")
              }
              onClick={() => setSelectedId(row.id)}
            >
              <span className="family-review__row-id">{row.id}</span>
              <span className={"family-review-status " + statusClass(row.status)}>{row.status || "pending_review"}</span>
              <span className="family-review__row-meta">
                {row.io_guess || "—"} · {row.members ?? "?"} mem ·{" "}
                <span
                  className={
                    typeof row.sample_count === "number" && row.sample_count > 0
                      ? ""
                      : "factory-muted"
                  }
                  title="Fingerprint-matched exemplar clips"
                >
                  {typeof row.sample_count === "number"
                    ? `${row.sample_count}${
                        typeof row.sample_target === "number" ? `/${row.sample_target}` : ""
                      } ex`
                    : "? ex"}
                </span>
                {" · "}
                {row.representative || "—"}
              </span>
              {formatOutputDateRange(row.output_date_first, row.output_date_last, row.output_date_days) ? (
                <span className="family-review__row-dates mono">
                  {formatOutputDateRange(row.output_date_first, row.output_date_last, row.output_date_days)}
                </span>
              ) : (
                <span className="family-review__row-dates factory-muted">no dated outputs</span>
              )}
            </button>
          ))}
          {!filtered.length ? <div className="factory-empty">No proposals in this filter.</div> : null}
        </aside>

        <section className="family-review__detail">
          {loadingProp ? <div className="factory-muted">Loading…</div> : null}
          {!loadingProp && prop ? (
            <>
              <header className="family-review__detail-head">
                <h2>{prop.id}</h2>
                <div className="factory-muted">
                  {prop.io_guess || "—"} · {prop.input_profile_guess || "—"} · {prop.chain_role_guess || "—"} ·{" "}
                  {prop.member_count ?? prop.members?.length ?? 0} members
                </div>
                <div className="family-review__dates">
                  <strong>Output dates</strong>
                  {formatOutputDateRange(prop.output_date_first, prop.output_date_last, prop.output_date_days) ? (
                    <span className="mono">
                      {" "}
                      {formatOutputDateRange(prop.output_date_first, prop.output_date_last, prop.output_date_days)}
                    </span>
                  ) : (
                    <span className="factory-muted"> — none matched</span>
                  )}
                  {prop.match_stems?.length ? (
                    <div className="factory-muted mono family-review__stems">
                      stems: {prop.match_stems.join(", ")}
                    </div>
                  ) : null}
                </div>
              </header>

              <div className="family-review__block">
                <h3>Representative workflow</h3>
                <div className="mono family-review__path" title={rep?.path || ""}>
                  {rep?.path || "—"}
                  {rep?.exists === false ? <span className="family-review__missing"> (missing)</span> : null}
                </div>
                {rep?.name ? <div className="factory-muted">{rep.name}</div> : null}
              </div>

              <div className="family-review__block">
                <h3>Sample videos ({samples.length}{prop.sample_target ? ` / ${prop.sample_target}` : ""})</h3>
                <div className="factory-muted" style={{ marginBottom: 8 }}>
                  Matched by <strong>graph fingerprint</strong> from PNG embeds under{" "}
                  <code>output/og</code> — not by output basename or brand tokens (those overlap).
                  Target is ~10–20 clips per candidate variation.
                </div>
                {samples.length ? (
                  <div className="family-review__samples">
                    {samples.map((s, i) => (
                      <div key={`${s.path || s.name || i}`} className="family-review__sample">
                        <div className="family-review__sample-cap mono" title={s.path || s.name || ""}>
                          {s.name || s.path || "video"}
                        </div>
                        {s.url ? (
                          <>
                            <button
                              type="button"
                              className="family-review__video-btn"
                              onClick={() => {
                                setViewerUrl(s.url || null);
                                setViewerTitle(s.name || s.path || "Sample video");
                              }}
                              aria-label={`Open viewer: ${s.name || "sample"}`}
                            >
                              <video
                                className="family-review__video"
                                src={s.url}
                                muted
                                playsInline
                                preload="metadata"
                              />
                              <span className="family-review__video-play">Play</span>
                            </button>
                            <a className="family-review__sample-link" href={s.url} target="_blank" rel="noreferrer">
                              Open file
                            </a>
                          </>
                        ) : (
                          <div className="factory-muted">No preview URL (path not under output/workflows roots)</div>
                        )}
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="factory-muted">
                    No fingerprint exemplars yet. Build the index:
                    <pre style={{ marginTop: 8, whiteSpace: "pre-wrap" }}>
                      {`python3 workspace/scripts/shape_factory_family_discovery.py index-exemplars`}
                    </pre>
                  </div>
                )}
              </div>

              <div className="family-review__block">
                <h3>Members</h3>
                <ul className="family-review__members">
                  {(prop.members || []).map((m) => (
                    <li key={m.path || m.name} className="mono" title={m.path || ""}>
                      [{m.source || "?"}] {m.name || m.path}
                    </li>
                  ))}
                </ul>
              </div>

              <div className="family-review__block family-review__form">
                <h3>Operator decision</h3>
                <label>
                  Status
                  <select
                    value={status}
                    onChange={(e) => setStatus(e.target.value as FamilyDiscoveryStatus)}
                    disabled={saving}
                  >
                    {STATUS_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Proposed family slug
                  <input
                    value={slug}
                    onChange={(e) => setSlug(e.target.value)}
                    placeholder="e.g. FB8VA5_LAYING"
                    disabled={saving || status === "skip"}
                  />
                </label>
                <label>
                  Merge into enrolled
                  <select
                    value={nearest}
                    onChange={(e) => setNearest(e.target.value)}
                    disabled={saving || status !== "merge"}
                  >
                    <option value="">—</option>
                    {enrolled.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Notes
                  <textarea
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                    rows={4}
                    disabled={saving}
                    placeholder="Why new / merge / skip…"
                  />
                </label>
                <div className="family-review__form-actions">
                  <button type="button" className="drt-btn" disabled={saving} onClick={() => void save()}>
                    {saving ? "Saving…" : "Save decision"}
                  </button>
                  <span className="factory-muted">
                    Writes <code>{selectedId}.json</code> (+ INDEX status). Enroll via CLI when ready.
                  </span>
                </div>
              </div>
            </>
          ) : null}
          {!loadingProp && !prop ? <div className="factory-empty">Select a proposal.</div> : null}
        </section>
      </div>

      {viewerUrl ? (
        <div
          className="family-review__lightbox"
          role="dialog"
          aria-modal="true"
          aria-label={viewerTitle || "Sample video"}
          onClick={() => setViewerUrl(null)}
          onKeyDown={(e) => {
            if (e.key === "Escape") setViewerUrl(null);
          }}
        >
          <div
            className="family-review__lightbox-panel"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="family-review__lightbox-head">
              <div className="mono family-review__lightbox-title" title={viewerTitle}>
                {viewerTitle}
              </div>
              <button type="button" className="drt-btn" onClick={() => setViewerUrl(null)}>
                Close
              </button>
            </div>
            <video className="family-review__lightbox-video" src={viewerUrl} controls autoPlay playsInline />
          </div>
        </div>
      ) : null}
    </div>
  );
}
