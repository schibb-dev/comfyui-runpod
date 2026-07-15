import React, { useEffect, useMemo, useRef, useState } from "react";
import { fetchVisionSliceCaptions } from "./api";
import { PageHeader } from "./PageHeader";
import type {
  VisionSliceAsset,
  VisionSliceCaptionRow,
  VisionSliceCaptionsResponse,
  VisionSliceVariantMeta,
} from "./types";

function fmtRange(t0: unknown, t1: unknown): string {
  const a = typeof t0 === "number" ? t0.toFixed(1) : String(t0 ?? "?");
  const b = typeof t1 === "number" ? t1.toFixed(1) : String(t1 ?? "?");
  return `${a}–${b}s`;
}

function shortVariantLabel(v: VisionSliceVariantMeta): string {
  if (v.label) return v.label;
  const parts = [v.id];
  if (v.task) parts.push(v.task);
  return parts.join(" · ");
}

export function VisionSliceReviewApp() {
  const [data, setData] = useState<VisionSliceCaptionsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedRel, setSelectedRel] = useState<string | null>(null);
  const [activeSliceIdx, setActiveSliceIdx] = useState(0);
  const [enabledVariants, setEnabledVariants] = useState<string[]>([]);
  const videoRef = useRef<HTMLVideoElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void fetchVisionSliceCaptions()
      .then((res) => {
        if (cancelled) return;
        setData(res);
        setError(null);
        const first = res.assets?.[0]?.asset_relpath;
        if (first) setSelectedRel(first);
        const ids = (res.variants || []).map((v) => v.id);
        setEnabledVariants(ids);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const variants: VisionSliceVariantMeta[] = data?.variants?.length
    ? data.variants
    : [{ id: "base_caption", label: "base_caption" }];

  const visibleVariants = useMemo(
    () => variants.filter((v) => enabledVariants.includes(v.id)),
    [variants, enabledVariants],
  );

  const asset: VisionSliceAsset | null = useMemo(() => {
    if (!data?.assets?.length || !selectedRel) return null;
    return data.assets.find((a) => a.asset_relpath === selectedRel) ?? null;
  }, [data, selectedRel]);

  const activeSlice: VisionSliceCaptionRow | null =
    asset && asset.slices[activeSliceIdx] ? asset.slices[activeSliceIdx] : null;

  useEffect(() => {
    setActiveSliceIdx(0);
  }, [selectedRel]);

  function seekToSlice(idx: number) {
    setActiveSliceIdx(idx);
    const slice = asset?.slices[idx];
    const v = videoRef.current;
    if (!v || !slice) return;
    const t = typeof slice.frame_t === "number" ? slice.frame_t : Number(slice.t0) || 0;
    try {
      v.currentTime = Math.max(0, t);
      void v.play().catch(() => undefined);
    } catch {
      /* ignore seek errors */
    }
  }

  function toggleVariant(id: string) {
    setEnabledVariants((prev) => {
      if (prev.includes(id)) {
        if (prev.length <= 1) return prev;
        return prev.filter((x) => x !== id);
      }
      return [...prev, id];
    });
  }

  function captionFor(slice: VisionSliceCaptionRow, variantId: string): string {
    const fromMap = slice.captions?.[variantId]?.caption;
    if (fromMap) return fromMap;
    if (variantId === "base_caption" && slice.caption) return slice.caption;
    return "";
  }

  const subtitle = data
    ? `${data.asset_count ?? 0} videos · ${data.slice_count ?? data.caption_count ?? 0} slices · ${(data.variants || []).length} variants`
    : "V1 time-slice caption spike";

  return (
    <div className="vision-slice layout">
      <PageHeader
        title="Vision slices"
        subtitle={subtitle}
        actions={
          <button
            type="button"
            className="btn"
            disabled={loading}
            onClick={() => {
              setLoading(true);
              void fetchVisionSliceCaptions()
                .then((res) => {
                  setData(res);
                  setError(null);
                  if (selectedRel && !res.assets.some((a) => a.asset_relpath === selectedRel)) {
                    setSelectedRel(res.assets[0]?.asset_relpath ?? null);
                  }
                  const ids = (res.variants || []).map((v) => v.id);
                  setEnabledVariants((prev) => {
                    const keep = prev.filter((id) => ids.includes(id));
                    return keep.length ? keep : ids;
                  });
                })
                .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
                .finally(() => setLoading(false));
            }}
          >
            Refresh
          </button>
        }
      />

      {error ? <div className="vision-slice-error">{error}</div> : null}
      {loading && !data ? <div className="vision-slice-empty">Loading…</div> : null}
      {!loading && data && data.asset_count === 0 ? (
        <div className="vision-slice-empty">
          No slice captions found. Expected{" "}
          <code>output/_status/vision_slice_captions__*.ndjson</code> (or legacy{" "}
          <code>vision_slice_captions.ndjson</code>).
        </div>
      ) : null}

      {data && data.asset_count > 0 ? (
        <>
          {variants.length > 1 ? (
            <div className="vision-slice-variants" aria-label="Caption variants">
              {variants.map((v) => (
                <label key={v.id} className="vision-slice-variant-toggle">
                  <input
                    type="checkbox"
                    checked={enabledVariants.includes(v.id)}
                    onChange={() => toggleVariant(v.id)}
                  />
                  <span className="vision-slice-variant-toggle__label">{shortVariantLabel(v)}</span>
                  {typeof v.caption_count === "number" ? (
                    <span className="vision-slice-variant-toggle__count">{v.caption_count}</span>
                  ) : null}
                </label>
              ))}
            </div>
          ) : null}

          <div className="vision-slice-body">
            <aside className="vision-slice-list" aria-label="Assets">
              {data.assets.map((a) => (
                <button
                  key={a.asset_relpath}
                  type="button"
                  className={
                    "vision-slice-list__item" +
                    (a.asset_relpath === selectedRel ? " is-active" : "")
                  }
                  onClick={() => setSelectedRel(a.asset_relpath)}
                  title={a.asset_relpath}
                >
                  <span className="vision-slice-list__name">{a.basename}</span>
                  <span className="vision-slice-list__meta">
                    {a.slice_count} slices{a.has_whole ? " · whole" : ""}
                  </span>
                </button>
              ))}
            </aside>

            <section className="vision-slice-detail">
              {asset ? (
                <>
                  <div className="vision-slice-player">
                    <video
                      key={asset.video_url}
                      ref={videoRef}
                      className="vision-slice-video"
                      src={asset.video_url}
                      controls
                      playsInline
                      preload="metadata"
                    />
                    <div className="vision-slice-path" title={asset.asset_relpath}>
                      {asset.asset_relpath}
                    </div>
                  </div>
                  <ul className="vision-slice-captions">
                    {asset.slices.map((s, idx) => (
                      <li key={`${s.slice}-${s.t0}-${s.t1}-${idx}`}>
                        <button
                          type="button"
                          className={
                            "vision-slice-cap" + (idx === activeSliceIdx ? " is-active" : "")
                          }
                          onClick={() => seekToSlice(idx)}
                        >
                          <span className="vision-slice-cap__when">
                            <span
                              className={"vision-slice-cap__kind kind-" + (s.slice || "window")}
                            >
                              {s.slice || "window"}
                            </span>
                            {fmtRange(s.t0, s.t1)}
                          </span>
                          {visibleVariants.length > 1 ? (
                            <div
                              className="vision-slice-cap__compare"
                              style={
                                {
                                  "--vision-compare-cols": String(visibleVariants.length),
                                } as React.CSSProperties
                              }
                            >
                              {visibleVariants.map((v) => {
                                const text = captionFor(s, v.id);
                                return (
                                  <div key={v.id} className="vision-slice-cap__col">
                                    <span className="vision-slice-cap__col-label">
                                      {v.id}
                                    </span>
                                    <span className="vision-slice-cap__text">
                                      {text || "—"}
                                    </span>
                                  </div>
                                );
                              })}
                            </div>
                          ) : (
                            <span className="vision-slice-cap__text">
                              {captionFor(s, visibleVariants[0]?.id || "base_caption") ||
                                s.caption ||
                                "—"}
                            </span>
                          )}
                        </button>
                      </li>
                    ))}
                  </ul>
                  {activeSlice ? (
                    <p className="vision-slice-hint">
                      Click a slice to seek (~
                      {fmtRange(
                        activeSlice.frame_t ?? activeSlice.t0,
                        activeSlice.frame_t ?? activeSlice.t0,
                      )}{" "}
                      mid-frame). Toggle variants above to A/B captions.
                    </p>
                  ) : null}
                </>
              ) : (
                <div className="vision-slice-empty">Select a video.</div>
              )}
            </section>
          </div>
        </>
      ) : null}
    </div>
  );
}
