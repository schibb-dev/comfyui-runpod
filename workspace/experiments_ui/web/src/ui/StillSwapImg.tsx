import React, { useEffect, useRef, useState } from "react";

/**
 * Replace src without keeping the previous bitmap on screen.
 * Until the new image has loaded (or failed), show an explicit loading state
 * so next/prev is visibly acknowledged even when the file is slow.
 */
export function StillSwapImg({
  src,
  className,
  alt = "",
  loading = "eager",
  fetchPriority,
}: {
  src: string;
  className?: string;
  alt?: string;
  loading?: "lazy" | "eager";
  fetchPriority?: "high" | "low" | "auto";
}) {
  const srcRef = useRef(src);
  srcRef.current = src;
  const imgRef = useRef<HTMLImageElement | null>(null);
  const [loadedSrc, setLoadedSrc] = useState<string | null>(null);
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  const ready = loadedSrc === src;
  const failed = failedSrc === src;

  useEffect(() => {
    const el = imgRef.current;
    if (!el?.complete) return;
    if (el.naturalWidth > 0) {
      setLoadedSrc(src);
      return;
    }
    if (el.naturalHeight === 0) setFailedSrc(src);
  }, [src]);

  return (
    <div className="still-gallery__swap" aria-busy={!ready && !failed}>
      {!ready ? (
        <div className="still-gallery__swap-loading" role="status">
          {failed ? (
            "Preview failed"
          ) : (
            <>
              <span className="spinner-ring" aria-hidden="true" />
              <span>Loading…</span>
            </>
          )}
        </div>
      ) : null}
      <img
        key={src}
        ref={imgRef}
        className={[className, ready ? null : "is-pending"].filter(Boolean).join(" ")}
        src={src}
        alt={alt}
        loading={loading}
        fetchPriority={fetchPriority}
        decoding="async"
        onLoad={() => {
          if (srcRef.current === src) setLoadedSrc(src);
        }}
        onError={() => {
          if (srcRef.current === src) setFailedSrc(src);
        }}
      />
    </div>
  );
}
