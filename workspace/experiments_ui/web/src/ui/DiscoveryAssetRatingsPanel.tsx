import React, { useCallback, useEffect, useState } from "react";
import { fetchDiscoveryAssetRatings, postDiscoveryAssetRatingsVerify } from "./api";
import { AssetJudgmentEditor } from "./AssetJudgmentEditor";
import { APPETITE_ROW_GLYPH, appetiteRowTitle } from "./discoveryRatingsRollup";
import { formatIsoDateTime } from "./locale";
import type {
  DiscoveryAssetRatingsContributor,
  DiscoveryAssetRatingsHumanReview,
  DiscoveryAssetRatingsLensBlock,
  DiscoveryAssetRatingsResponse,
  DiscoveryLibraryItem,
} from "./types";

function str(v: unknown): string {
  return typeof v === "string" ? v : v == null ? "" : String(v);
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function RatingPill({ value, kind }: { value: number; kind: "explicit" | "inferred" | "override" }) {
  const label = kind === "explicit" ? "★" : kind === "override" ? "✎" : "◇";
  const text = Number.isInteger(value) ? String(value) : value.toFixed(1);
  const title =
    kind === "explicit" ? "Hand-tagged (XMP)" : kind === "override" ? "Human override" : "Calculated from downstream";
  return (
    <span className={`drt-pill drt-pill--${kind === "override" ? "explicit" : kind}`} title={title}>
      {label}
      {text}
    </span>
  );
}

function ContributorList({
  rows,
  onOpenRelpath,
  onJumpFailed,
}: {
  rows: DiscoveryAssetRatingsContributor[];
  onOpenRelpath?: (relpath: string) => void | Promise<boolean | string>;
  onJumpFailed?: (relpath: string) => void;
}) {
  if (!rows.length) {
    return <p className="discovery-mock-hint">No contributor rows indexed yet.</p>;
  }
  return (
    <ul className="drt-contrib-list">
      {rows.map((row, i) => {
        const rel = str(row.output_discovery_key);
        const rating = num(row.rating);
        const label = rel.split("/").pop() || rel || "—";
        return (
          <li key={`${rel}-${i}`} className="drt-contrib-li">
            {onOpenRelpath && rel ? (
              <button
                type="button"
                className="drt-contrib-link"
                title={`Open in library: ${rel}`}
                onClick={() => {
                  void Promise.resolve(onOpenRelpath(rel)).then((ok) => {
                    if (ok === false) onJumpFailed?.(rel);
                  });
                }}
              >
                {label} →
              </button>
            ) : (
              <span className="mono">{label}</span>
            )}
            {rating != null ? <RatingPill value={rating} kind="explicit" /> : null}
            {row.via_source ? <span className="drt-muted">via {str(row.via_source).split("/").pop()}</span> : null}
          </li>
        );
      })}
    </ul>
  );
}

function VerifyBanner({ verification }: { verification: Record<string, unknown> | undefined }) {
  if (!verification) return null;
  const match = verification.match === true;
  const disk = num(verification.xmp_on_disk);
  const index = num(verification.index_explicit);
  const err = str(verification.error);
  if (err && err !== "no_output_row") {
    return <p className="drt-verify drt-verify--warn">Verification: {err.replace(/_/g, " ")}</p>;
  }
  if (verification.ok !== true) return null;
  return (
    <p className={"drt-verify" + (match ? " drt-verify--ok" : " drt-verify--bad")}>
      {match
        ? `XMP on disk matches index (${disk ?? "?"}★)`
        : `Mismatch: disk=${disk ?? "—"}★ index=${index ?? "—"}★ — rebuild ratings index`}
    </p>
  );
}

function HumanVerifyRow({
  lens,
  human,
  inferred,
  relpath,
  disabled,
  onUpdated,
}: {
  lens: "as_source" | "workflow" | "recipe";
  human?: DiscoveryAssetRatingsHumanReview;
  inferred?: number | null;
  relpath: string;
  disabled?: boolean;
  onUpdated: (ratings: DiscoveryAssetRatingsResponse) => void;
}) {
  const [verified, setVerified] = useState(Boolean(human?.verified));
  const [override, setOverride] = useState<string>(human?.override_rating != null ? String(human.override_rating) : "");
  const [note, setNote] = useState(str(human?.note));
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState("");

  useEffect(() => {
    setVerified(Boolean(human?.verified));
    setOverride(human?.override_rating != null ? String(human.override_rating) : "");
    setNote(str(human?.note));
  }, [human?.verified, human?.override_rating, human?.note, human?.verified_at]);

  const save = useCallback(async () => {
    setSaving(true);
    setSaveErr("");
    try {
      const overrideNum = override.trim() ? Number(override) : null;
      if (override.trim() && (!Number.isFinite(overrideNum) || overrideNum! < 1 || overrideNum! > 5)) {
        setSaveErr("Override must be 1–5 or empty");
        return;
      }
      const res = await postDiscoveryAssetRatingsVerify({
        relpath,
        lens,
        verified,
        override_rating: overrideNum,
        note: note.trim() || undefined,
      });
      if (res.ratings) onUpdated(res.ratings);
      else if (!res.ok) setSaveErr(res.error || "Save failed");
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }, [relpath, lens, verified, override, note, onUpdated]);

  const overrideNum = num(override.trim() ? Number(override) : null);

  return (
    <div className="drt-human">
      <div className="drt-human-row">
        <label className="drt-human-check">
          <input type="checkbox" checked={verified} disabled={disabled || saving} onChange={(e) => setVerified(e.target.checked)} />
          Reviewed evidence
        </label>
        {verified && human?.verified_at ? <span className="drt-muted">saved {human.verified_at}</span> : null}
      </div>
      <div className="drt-human-row">
        <label className="drt-human-label" htmlFor={`drt-override-${lens}`}>
          Override
        </label>
        <select
          id={`drt-override-${lens}`}
          className="drt-human-select"
          value={override}
          disabled={disabled || saving}
          onChange={(e) => setOverride(e.target.value)}
        >
          <option value="">Use inferred ({inferred != null ? String(inferred) : "—"})</option>
          {[1, 2, 3, 4, 5].map((n) => (
            <option key={n} value={String(n)}>
              {n}★
            </option>
          ))}
        </select>
        {overrideNum != null ? <RatingPill value={overrideNum} kind="override" /> : null}
      </div>
      <div className="drt-human-row">
        <input
          type="text"
          className="drt-human-note"
          placeholder="Optional note"
          value={note}
          disabled={disabled || saving}
          onChange={(e) => setNote(e.target.value)}
        />
        <button type="button" className="drt-btn" disabled={disabled || saving} onClick={() => void save()}>
          {saving ? "Saving…" : "Save review"}
        </button>
      </div>
      {saveErr ? <div className="drt-err">{saveErr}</div> : null}
    </div>
  );
}

function InferredLensSection({
  title,
  lens,
  block,
  relpath,
  lead,
  children,
  onUpdated,
  onOpenRelpath,
  onJumpFailed,
}: {
  title: string;
  lens: "as_source" | "workflow" | "recipe";
  block: DiscoveryAssetRatingsLensBlock;
  relpath: string;
  lead?: React.ReactNode;
  children?: React.ReactNode;
  onUpdated: (ratings: DiscoveryAssetRatingsResponse) => void;
  onOpenRelpath?: (relpath: string) => void | Promise<boolean | string>;
  onJumpFailed?: (relpath: string) => void;
}) {
  const inferred = num(block.inferred);
  const human = block.human;
  const displayRating = num(human?.override_rating) ?? inferred;

  return (
    <section className="drt-section">
      <h4 className="drt-h4">{title}</h4>
      {lead}
      <div className="drt-kv">{children}</div>
      {displayRating != null ? (
        <div className="drt-summary" style={{ marginBottom: 8 }}>
          <RatingPill
            value={displayRating}
            kind={human?.override_rating != null ? "override" : "inferred"}
          />
          {human?.verified ? <span className="drt-human-badge">reviewed</span> : null}
        </div>
      ) : null}
      <HumanVerifyRow lens={lens} human={human} inferred={inferred} relpath={relpath} onUpdated={onUpdated} />
      {block.contributors && block.contributors.length > 0 ? (
        <>
          <h5 className="drt-h5">Contributing outputs</h5>
          <ContributorList rows={block.contributors} onOpenRelpath={onOpenRelpath} onJumpFailed={onJumpFailed} />
        </>
      ) : null}
    </section>
  );
}

export function DiscoveryAssetRatingsPanel({
  seedItem,
  onOpenRelpath,
  onJudgmentSaved,
}: {
  seedItem: DiscoveryLibraryItem | null;
  onOpenRelpath?: (relpath: string) => void | Promise<boolean | string>;
  /** Called after inline quality/appetite saves so the library list can refresh its rollup. */
  onJudgmentSaved?: (relpath: string, ratings: DiscoveryAssetRatingsResponse) => void;
}) {
  const [data, setData] = useState<DiscoveryAssetRatingsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [jumpError, setJumpError] = useState("");

  const onJumpFailed = useCallback((rel: string) => {
    setJumpError(`Could not open ${rel} in the library (not in discovery index).`);
  }, []);

  const handleOpenRelpath = useCallback(
    async (rel: string): Promise<boolean> => {
      if (!onOpenRelpath) return false;
      const ok = await onOpenRelpath(rel);
      if (ok) setJumpError("");
      return Boolean(ok);
    },
    [onOpenRelpath]
  );

  const load = useCallback(async () => {
    if (!seedItem?.relpath) return;
    setLoading(true);
    setError("");
    try {
      const body = await fetchDiscoveryAssetRatings(seedItem.relpath);
      setData(body);
      if (!body.ok && body.error) {
        setError(body.error + (body.detail ? `: ${body.detail}` : ""));
      }
    } catch (e) {
      setData(null);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [seedItem]);

  useEffect(() => {
    setData(null);
    setError("");
    setJumpError("");
    void load();
  }, [load]);

  const onRatingsChanged = useCallback(
    (ratings: DiscoveryAssetRatingsResponse) => {
      setData(ratings);
      if (seedItem?.relpath) onJudgmentSaved?.(seedItem.relpath, ratings);
    },
    [seedItem?.relpath, onJudgmentSaved],
  );

  if (!seedItem) {
    return (
      <p className="discovery-mock-hint" style={{ marginTop: 0 }}>
        Select a library item to explore explicit and calculated ratings.
      </p>
    );
  }

  const effective = num(data?.rating_effective);
  const explicit = data?.explicit;
  const asSource = data?.as_source;
  const workflow = data?.workflow;
  const recipe = data?.recipe;
  const cited = data?.sources_cited ?? [];
  const relpath = seedItem.relpath;

  const effectiveKind =
    explicit?.rating != null
      ? "explicit"
      : workflow?.human?.override_rating != null || asSource?.human?.override_rating != null || recipe?.human?.override_rating != null
        ? "override"
        : "inferred";

  return (
    <div className="drt-wrap" aria-label="Asset ratings explorer">
      <div className="drt-toolbar">
        <button type="button" className="drt-btn" disabled={loading} onClick={() => void load()}>
          {loading ? "Loading…" : "Refresh ratings"}
        </button>
        <a className="drt-btn" href="/discovery/rate" title="Open the batch rate queue for disposition + triage">
          Rate queue
        </a>
        {data?.index_updated_at ? (
          <span className="drt-muted" title={data.index_updated_at}>
            Index: {formatIsoDateTime(data.index_updated_at)}
          </span>
        ) : null}
      </div>

      {loading && !data ? <p className="discovery-mock-hint">Loading ratings…</p> : null}
      {error ? <div className="drt-err">{error}</div> : null}
      {jumpError ? <div className="drt-err">{jumpError}</div> : null}

      {data?.ok ? (
        <>
          <AssetJudgmentEditor
            relpath={relpath}
            seed={data}
            layout="cards"
            onSaved={onRatingsChanged}
          />

          <section className="drt-section">
            <h4 className="drt-h4">Summary</h4>
            <div className="drt-summary">
              {effective != null ? (
                <RatingPill value={effective} kind={effectiveKind} />
              ) : (
                <span className="drt-muted">No rating signal for this asset</span>
              )}
              {data.appetite ? (
                <span
                  className={"discovery-row-appetite discovery-row-appetite--" + data.appetite}
                  title={appetiteRowTitle(data.appetite, data.appetite_facet)}
                >
                  {APPETITE_ROW_GLYPH[data.appetite]}
                </span>
              ) : null}
              <span className="drt-muted mono">{str(seedItem.name) || seedItem.relpath}</span>
            </div>
          </section>

          {explicit?.rating != null ? (
            <section className="drt-section">
              <h4 className="drt-h4">Explicit (hand-tagged)</h4>
              <div className="drt-kv">
                <span className="drt-k">Stars</span>
                <span className="drt-v">
                  <RatingPill value={Number(explicit.rating)} kind="explicit" />
                </span>
                <span className="drt-k">XMP</span>
                <span className="drt-v mono drt-break">{str(explicit.xmp) || "—"}</span>
              </div>
              <VerifyBanner verification={explicit.verification as Record<string, unknown> | undefined} />
            </section>
          ) : (
            <section className="drt-section">
              <h4 className="drt-h4">Explicit (hand-tagged)</h4>
              <p className="discovery-mock-hint">No XMP star rating on this output.</p>
            </section>
          )}

          {asSource?.inferred != null ? (
            <InferredLensSection
              title="As source (calculated)"
              lens="as_source"
              block={asSource}
              relpath={relpath}
              onUpdated={onRatingsChanged}
              onOpenRelpath={handleOpenRelpath}
              onJumpFailed={onJumpFailed}
              lead={
                <p className="drt-lead">
                  Downstream rated outputs cite <span className="mono">{str(asSource.basename)}</span> as an input.
                </p>
              }
            >
              <span className="drt-k">Evidence</span>
              <span className="drt-v">
                n={String(asSource.n ?? "?")}, keepers 4+={String(asSource.keepers_4plus ?? "?")}
              </span>
            </InferredLensSection>
          ) : null}

          {workflow?.graph_hash ? (
            <InferredLensSection
              title="Workflow topology (calculated)"
              lens="workflow"
              block={workflow}
              relpath={relpath}
              onUpdated={onRatingsChanged}
              onOpenRelpath={handleOpenRelpath}
              onJumpFailed={onJumpFailed}
            >
              <span className="drt-k">graph_hash</span>
              <span className="drt-v mono drt-break">{workflow.graph_hash}</span>
              {workflow.catalog_slug ? (
                <>
                  <span className="drt-k">Catalog</span>
                  <span className="drt-v">{str(workflow.catalog_slug)}</span>
                </>
              ) : null}
              {workflow.inferred != null ? (
                <>
                  <span className="drt-k">Inferred</span>
                  <span className="drt-v">n={String(workflow.n ?? "?")}</span>
                </>
              ) : null}
            </InferredLensSection>
          ) : null}

          {recipe?.shape_recipe ? (
            <InferredLensSection
              title="Factory recipe"
              lens="recipe"
              block={recipe}
              relpath={relpath}
              onUpdated={onRatingsChanged}
              onOpenRelpath={handleOpenRelpath}
              onJumpFailed={onJumpFailed}
            >
              <span className="drt-k">Recipe</span>
              <span className="drt-v mono">{str(recipe.shape_recipe)}</span>
            </InferredLensSection>
          ) : null}

          {cited.length > 0 ? (
            <section className="drt-section">
              <h4 className="drt-h4">Sources cited in this output</h4>
              <ul className="drt-cited-list">
                {cited.map((c, i) => (
                  <li key={`${str(c.basename)}-${i}`} className="drt-cited-li">
                    <span className="mono">{str(c.basename)}</span>
                    {c.source_inferred != null ? (
                      <RatingPill value={Number(c.source_inferred)} kind="inferred" />
                    ) : (
                      <span className="drt-muted">no downstream signal</span>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
