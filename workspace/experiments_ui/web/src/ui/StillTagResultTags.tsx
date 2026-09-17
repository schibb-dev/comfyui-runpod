import React, { useEffect, useState } from "react";
import { AppetitePreviewFrame } from "./AppetitePreviewBadge";
import { discoveryLibraryHref, stillsHref } from "./discoveryDeepLink";
import {
  stillTagResultStatusLabel,
  stillTagResultStatusTone,
  stillTagResultTagGroups,
  type StillTagResultItem,
} from "./stillTagResults";

async function copyText(text: string): Promise<boolean> {
  const value = String(text || "").trim();
  if (!value) return false;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch {
    /* fall through */
  }
  return false;
}

function TagChipList({ tags, emptyLabel }: { tags: string[]; emptyLabel?: string }) {
  if (!tags.length) {
    return emptyLabel ? <p className="factory-muted still-tag-tags__empty">{emptyLabel}</p> : null;
  }
  return (
    <ul className="still-tag-tags__chips">
      {tags.map((tag) => (
        <li key={tag}>
          <span className="still-tag-tags__chip">{tag}</span>
        </li>
      ))}
    </ul>
  );
}

export function StillTagTagsPanel({
  item,
  layout = "sections",
}: {
  item: StillTagResultItem;
  layout?: "sections" | "effective_only";
}) {
  const groups = stillTagResultTagGroups(item);
  const [copied, setCopied] = useState<string | null>(null);

  const onCopy = async (label: string, tags: string[]) => {
    const ok = await copyText(tags.join(", "));
    setCopied(ok ? label : null);
    if (ok) window.setTimeout(() => setCopied((v) => (v === label ? null : v)), 1600);
  };

  if (layout === "effective_only") {
    return (
      <div className="still-tag-tags still-tag-tags--compact">
        <div className="still-tag-tags__head">
          <span className="still-tag-tags__count factory-muted">{groups.effective.length} tags</span>
          {groups.effective.length ? (
            <button type="button" className="drt-btn still-tag-tags__copy" onClick={() => void onCopy("all", groups.effective)}>
              {copied === "all" ? "Copied" : "Copy all"}
            </button>
          ) : null}
        </div>
        <TagChipList tags={groups.effective} emptyLabel="No tags yet" />
      </div>
    );
  }

  return (
    <div className="still-tag-tags">
      <div className="still-tag-tags__head">
        <span className="still-tag-tags__count factory-muted">
          {groups.effective.length} effective
          {groups.auto.length !== groups.effective.length || groups.editorial.length
            ? ` · ${groups.auto.length} auto · ${groups.editorial.length} editorial`
            : ""}
        </span>
        {groups.effective.length ? (
          <button type="button" className="drt-btn still-tag-tags__copy" onClick={() => void onCopy("all", groups.effective)}>
            {copied === "all" ? "Copied" : "Copy all"}
          </button>
        ) : null}
      </div>

      <section className="still-tag-tags__section" aria-label="Effective tags">
        <h4 className="still-tag-tags__section-title">Effective tags</h4>
        <TagChipList tags={groups.effective} emptyLabel="No tags yet" />
      </section>

      {groups.auto.length ? (
        <section className="still-tag-tags__section" aria-label="Auto tags">
          <div className="still-tag-tags__section-head">
            <h4 className="still-tag-tags__section-title">Auto (Florence)</h4>
            <button type="button" className="drt-btn still-tag-tags__copy" onClick={() => void onCopy("auto", groups.auto)}>
              {copied === "auto" ? "Copied" : "Copy"}
            </button>
          </div>
          <TagChipList tags={groups.auto} />
        </section>
      ) : null}

      {groups.editorial.length ? (
        <section className="still-tag-tags__section" aria-label="Editorial tags">
          <div className="still-tag-tags__section-head">
            <h4 className="still-tag-tags__section-title">Editorial</h4>
            <button type="button" className="drt-btn still-tag-tags__copy" onClick={() => void onCopy("editorial", groups.editorial)}>
              {copied === "editorial" ? "Copied" : "Copy"}
            </button>
          </div>
          <TagChipList tags={groups.editorial} />
        </section>
      ) : null}
    </div>
  );
}

export function StillTagResultDetailPanel({
  item,
  compact,
}: {
  item: StillTagResultItem;
  compact?: boolean;
}) {
  const tone = stillTagResultStatusTone(item.status);
  const groups = stillTagResultTagGroups(item);

  return (
    <div className={`still-tag-detail-panel${compact ? " still-tag-detail-panel--compact" : ""}`}>
      <div className="still-tag-detail-panel__media">
        {item.url ? (
          <AppetitePreviewFrame relpath={item.relpath}>
            <img className="still-tag-detail-panel__img" src={item.url} alt="" />
          </AppetitePreviewFrame>
        ) : (
          <div className="still-tag-detail-panel__empty">No preview</div>
        )}
        <span className={`still-tag-result-card__status still-tag-result-card__status--${tone}`}>
          {stillTagResultStatusLabel(item.status)}
        </span>
      </div>
      <div className="still-tag-detail-panel__body">
        <div className="still-tag-detail-panel__head">
          <code className="still-tag-detail-panel__cid" title={item.content_id}>
            {item.content_id}
          </code>
          <div className="still-tag-detail-panel__links">
            {item.relpath ? (
              <a className="drt-btn still-tag-tags__copy" href={discoveryLibraryHref(item.relpath)}>
                Open file
              </a>
            ) : null}
            <a
              className="drt-btn still-tag-tags__copy"
              href={stillsHref({ contentId: item.content_id, relpath: item.relpath })}
            >
              Stills gallery
            </a>
          </div>
        </div>
        {item.error_message ? <p className="still-tag-result-card__error">{item.error_message}</p> : null}
        {item.warning && !item.error_message ? <p className="still-tag-result-card__warn">{item.warning}</p> : null}
        <StillTagTagsPanel item={item} />
        {!groups.effective.length && item.status === "pending" ? (
          <p className="factory-muted">Tags will appear here after Florence finishes this still.</p>
        ) : null}
      </div>
    </div>
  );
}

export function StillTagResultDetailModal({
  item,
  onClose,
}: {
  item: StillTagResultItem;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="modal-overlay still-tag-detail-overlay"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal still-tag-detail-modal" role="dialog" aria-modal="true" aria-labelledby="still-tag-detail-title">
        <header className="modal-header">
          <div className="still-tag-detail-modal__title-wrap">
            <h2 id="still-tag-detail-title" className="still-tag-detail-modal__title">
              Still tags
            </h2>
            <code className="still-tag-detail-modal__cid" title={item.content_id}>
              {item.content_id}
            </code>
          </div>
          <div className="modal-actions">
            <button type="button" className="drt-btn" onClick={onClose}>
              Close
            </button>
          </div>
        </header>
        <div className="still-tag-detail-modal__body still-tag-detail-modal__body--panel">
          <StillTagResultDetailPanel item={item} />
        </div>
      </div>
    </div>
  );
}
