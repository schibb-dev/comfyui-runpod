import React from "react";
import { AppetitePreviewBadge } from "./AppetitePreviewBadge";

export type MediaAssetCardProps = {
  name: string;
  path: string;
  mediaType?: string;
  role?: string;
  status?: string;
  thumbUrl?: string | null;
  videoUrl?: string | null;
  badge?: string;
  badgeClassName?: string;
  detail?: string;
  showPath?: boolean;
  showVideoThumb?: boolean;
  className?: string;
  onClick?: () => void;
};

export function MediaAssetCard({
  name,
  path,
  mediaType,
  role,
  status,
  thumbUrl,
  videoUrl,
  badge,
  badgeClassName = "",
  detail,
  showPath = true,
  showVideoThumb = false,
  className = "",
  onClick,
}: MediaAssetCardProps) {
  const interactive = Boolean(onClick);
  const rootClassName = `media-asset-card${interactive ? " media-asset-card--button" : ""}${
    className ? ` ${className}` : ""
  }`;
  const content = (
    <>
      <div className="media-asset-card__thumb" aria-hidden>
        {thumbUrl ? (
          <img src={thumbUrl} alt="" loading="lazy" decoding="async" />
        ) : videoUrl && showVideoThumb ? (
          <video src={videoUrl} muted playsInline preload="metadata" />
        ) : videoUrl || mediaType === "video" ? (
          <span className="media-asset-card__thumb-placeholder">▶ Video</span>
        ) : mediaType === "image" ? (
          <span className="media-asset-card__thumb-placeholder">Image</span>
        ) : (
          <span className="media-asset-card__thumb-placeholder">Asset</span>
        )}
        <AppetitePreviewBadge relpath={path} size="sm" />
      </div>
      <div className="media-asset-card__body">
        <div className="media-asset-card__title">{name}</div>
        <div className="media-asset-card__meta">
          {badge ? (
            <span className={`media-asset-card__badge${badgeClassName ? ` ${badgeClassName}` : ""}`}>
              {badge}
            </span>
          ) : null}
          {mediaType ? <span>{mediaType}</span> : null}
          {role ? <span>{role}</span> : null}
          {status ? <span>{status}</span> : null}
        </div>
        {detail ? <div className="media-asset-card__detail">{detail}</div> : null}
        {showPath ? <div className="media-asset-card__path">{path}</div> : null}
      </div>
    </>
  );

  if (interactive) {
    return (
      <button type="button" className={rootClassName} onClick={onClick}>
        {content}
      </button>
    );
  }

  return <div className={rootClassName}>{content}</div>;
}
