import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchShapeFactoryStillSimilar } from "./api";
import { stillsHref } from "./discoveryDeepLink";
import { queryKeys } from "./queryKeys";
import type { StillSimilarHit } from "./types";

const PROVIDER_KEY = "still-gallery.similarProvider";
export type SimilarProvider = "clip" | "tags" | "blend";

function readStoredProvider(): SimilarProvider {
  try {
    const raw = String(localStorage.getItem(PROVIDER_KEY) || "").trim().toLowerCase();
    if (raw === "clip" || raw === "tags" || raw === "blend") return raw;
  } catch {
    /* ignore */
  }
  return "clip";
}

function persistProvider(p: SimilarProvider) {
  try {
    localStorage.setItem(PROVIDER_KEY, p);
  } catch {
    /* ignore */
  }
}

export function StillSimilarPanel({
  contentId,
  onFilterTag,
}: {
  contentId: string | null;
  onFilterTag: (tag: string) => void;
}) {
  const cid = String(contentId || "").trim().toLowerCase();
  const [provider, setProvider] = useState<SimilarProvider>(() => readStoredProvider());

  const q = useQuery({
    queryKey: queryKeys.shapeFactory.stillSimilar(cid, provider),
    queryFn: () => fetchShapeFactoryStillSimilar({ contentId: cid, provider, limit: 24 }),
    enabled: Boolean(cid),
    staleTime: 20_000,
  });

  if (!cid) {
    return (
      <section className="still-gallery__panel still-gallery__similar" aria-label="Similar stills">
        <h2>Similar stills</h2>
        <p className="factory-muted">Select a still with a content hash in its name.</p>
      </section>
    );
  }

  const items = q.data?.items || [];
  const notes = q.data?.notes || [];
  const missingClip = notes.includes("clip_index_missing_query");
  const indexCount = q.data?.index?.count ?? 0;

  return (
    <section className="still-gallery__panel still-gallery__similar" aria-label="Similar stills">
      <div className="still-gallery__similar-head">
        <h2>Similar stills</h2>
        <div className="still-gallery__similar-providers" role="group" aria-label="Similarity provider">
          {(["clip", "tags", "blend"] as const).map((p) => (
            <button
              key={p}
              type="button"
              className={p === provider ? "drt-btn still-gallery__similar-on" : "drt-btn"}
              aria-pressed={p === provider}
              onClick={() => {
                setProvider(p);
                persistProvider(p);
              }}
            >
              {p === "clip" ? "CLIP" : p === "tags" ? "Tags" : "Blend"}
            </button>
          ))}
        </div>
      </div>
      <p className="factory-muted still-gallery__similar-meta">
        {provider === "clip"
          ? "Perceptual neighbors (CLIP embeddings)"
          : provider === "tags"
            ? "Coarse tag overlap (bootstrap)"
            : "CLIP rank with a small tag boost"}
        {indexCount ? ` · index ${indexCount}` : ""}
      </p>
      {q.isLoading ? <p className="factory-muted">Loading neighbors…</p> : null}
      {q.isError ? (
        <p className="factory-muted">{q.error instanceof Error ? q.error.message : "Similar search failed"}</p>
      ) : null}
      {missingClip && provider !== "tags" ? (
        <p className="factory-muted">
          This still is not in the CLIP index yet. Switch to Tags, or run{" "}
          <code>still_embed_index.py backfill</code>.
        </p>
      ) : null}
      {!q.isLoading && !q.isError && !items.length && !missingClip ? (
        <p className="factory-muted">No neighbors for this provider.</p>
      ) : null}
      {items.length ? (
        <ul className="still-gallery__similar-grid">
          {items.map((hit) => (
            <SimilarTile key={hit.content_id} hit={hit} onFilterTag={onFilterTag} />
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function SimilarTile({
  hit,
  onFilterTag,
}: {
  hit: StillSimilarHit;
  onFilterTag: (tag: string) => void;
}) {
  const href = stillsHref({ contentId: hit.content_id, relpath: hit.relpath || null });
  const score = Number.isFinite(hit.score) ? hit.score.toFixed(3) : "";
  const src = hit.thumb_url || hit.url;
  return (
    <li className="still-gallery__similar-tile">
      <a href={href} title={hit.basename || hit.content_id}>
        {src ? (
          <img src={src} alt={hit.basename || hit.content_id.slice(0, 8)} />
        ) : (
          <span className="still-gallery__similar-placeholder">{hit.content_id.slice(0, 8)}</span>
        )}
      </a>
      <div className="still-gallery__similar-score">{score}</div>
      {hit.shared_tags && hit.shared_tags.length ? (
        <ul className="still-gallery__similar-tags">
          {hit.shared_tags.slice(0, 4).map((tag) => (
            <li key={tag}>
              <button
                type="button"
                className="still-gallery__similar-tag"
                title={`Filter gallery by ${tag}`}
                onClick={() => onFilterTag(tag)}
              >
                {tag}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}
