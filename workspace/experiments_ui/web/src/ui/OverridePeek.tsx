import React, { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { workbenchHref } from "./discoveryDeepLink";
import {
  formatOverrideLoraLine,
  formatOverrideParamLine,
  jobOverrideDetail,
  jobOverrideKinds,
  jobOverrideTitle,
  type JobOverrideSource,
} from "./jobOverrides";

type PeekPos = { top: number; left: number; maxHeight: number };

export function OverridePeekButton({
  item,
  label,
  jobKey,
  promptId,
}: {
  item: JobOverrideSource | null | undefined;
  label: string;
  jobKey?: string | null;
  promptId?: string | null;
}) {
  const kinds = jobOverrideKinds(item);
  const detail = jobOverrideDetail(item);
  const btnRef = useRef<HTMLButtonElement>(null);
  const popRef = useRef<HTMLDivElement>(null);
  const hoverTimer = useRef<number | null>(null);
  const leaveTimer = useRef<number | null>(null);
  const panelId = useId();
  const [open, setOpen] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [pos, setPos] = useState<PeekPos>({ top: 0, left: 0, maxHeight: 360 });

  const clearTimers = () => {
    if (hoverTimer.current != null) window.clearTimeout(hoverTimer.current);
    if (leaveTimer.current != null) window.clearTimeout(leaveTimer.current);
    hoverTimer.current = null;
    leaveTimer.current = null;
  };

  const place = () => {
    const btn = btnRef.current;
    if (!btn) return;
    const r = btn.getBoundingClientRect();
    const pad = 8;
    const width = Math.min(420, window.innerWidth - pad * 2);
    let left = r.left;
    if (left + width > window.innerWidth - pad) left = Math.max(pad, window.innerWidth - pad - width);
    const spaceBelow = window.innerHeight - r.bottom - pad;
    const spaceAbove = r.top - pad;
    const preferBelow = spaceBelow >= 160 || spaceBelow >= spaceAbove;
    const maxHeight = Math.max(140, Math.min(420, preferBelow ? spaceBelow : spaceAbove));
    const top = preferBelow ? r.bottom + 6 : Math.max(pad, r.top - 6 - maxHeight);
    setPos({ top, left, maxHeight });
  };

  const openPeek = (pin: boolean) => {
    clearTimers();
    setPinned(pin);
    setOpen(true);
    place();
  };

  const closePeek = () => {
    clearTimers();
    setPinned(false);
    setOpen(false);
  };

  useLayoutEffect(() => {
    if (!open) return;
    place();
    const onScroll = () => {
      if (!pinned) closePeek();
      else place();
    };
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", place);
    };
  }, [open, pinned]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closePeek();
    };
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node | null;
      if (!t) return;
      if (btnRef.current?.contains(t)) return;
      if (popRef.current?.contains(t)) return;
      closePeek();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  useEffect(() => () => clearTimers(), []);

  if (!kinds.length) return <span>{label}</span>;

  const workbenchUrl = workbenchHref({ jobKey: jobKey || null, promptId: promptId || null });
  const stackName = detail?.stack || item?.glance?.stack_id || "";
  const loras = detail?.loras || [];
  const params = detail?.params || {};
  const paramKeys = Object.keys(params);

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        className="work-product-json-link"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        title={`${jobOverrideTitle(item)}\nHover to peek · click to pin`}
        onMouseEnter={() => {
          clearTimers();
          hoverTimer.current = window.setTimeout(() => openPeek(false), 180);
        }}
        onMouseLeave={() => {
          clearTimers();
          if (!pinned) leaveTimer.current = window.setTimeout(() => setOpen(false), 160);
        }}
        onFocus={() => openPeek(false)}
        onClick={(e) => {
          e.preventDefault();
          if (open && pinned) closePeek();
          else openPeek(true);
        }}
      >
        {label}
        <span className="work-product-json-link__tag work-product-json-link__tag--snowflake">ovr</span>
      </button>
      {open
        ? createPortal(
            <div
              ref={popRef}
              id={panelId}
              role="dialog"
              aria-label={jobOverrideTitle(item)}
              className={`work-product-json-pop${pinned ? " work-product-json-pop--pinned" : ""}`}
              style={{ top: pos.top, left: pos.left, maxHeight: pos.maxHeight, width: Math.min(420, window.innerWidth - 16) }}
              onMouseEnter={() => clearTimers()}
              onMouseLeave={() => {
                if (!pinned) leaveTimer.current = window.setTimeout(() => setOpen(false), 160);
              }}
            >
              <header className="work-product-json-pop__head">
                <strong className="work-product-json-pop__title">Overrides</strong>
                <div className="work-product-json-pop__actions">
                  {pinned ? <span className="work-product-json-pop__note">pinned</span> : null}
                  <a className="work-product-json-link" href={workbenchUrl}>
                    Workbench
                  </a>
                  <button type="button" className="work-product-json-pop__close" onClick={closePeek} aria-label="Close">
                    ×
                  </button>
                </div>
              </header>
              <div className="work-product-json-pop__body override-peek__body">
                {kinds.includes("prompt") ? (
                  <p>
                    <strong>prompt</strong> {detail?.prompt || "catalog text edited"}
                  </p>
                ) : null}
                {kinds.includes("stack") ? (
                  <p>
                    <strong>stack</strong> {stackName || "named UNet stack set"}
                  </p>
                ) : null}
                {kinds.includes("params") ? (
                  <div>
                    <strong>params</strong>
                    {paramKeys.length ? (
                      <ul>
                        {paramKeys.map((key) => (
                          <li key={key}>{formatOverrideParamLine(key, params[key] || {})}</li>
                        ))}
                      </ul>
                    ) : (
                      <p className="factory-muted">non-seed knobs differ from the template</p>
                    )}
                  </div>
                ) : null}
                {kinds.includes("loras") ? (
                  <div>
                    <strong>loras</strong>
                    {loras.length ? (
                      <ul>
                        {loras.map((row) => (
                          <li key={row.lora}>{formatOverrideLoraLine(row)}</li>
                        ))}
                      </ul>
                    ) : (
                      <p className="factory-muted">LoRA on/off or strength differs from the template</p>
                    )}
                  </div>
                ) : null}
              </div>
            </div>,
            document.body,
          )
        : null}
    </>
  );
}
