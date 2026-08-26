import React, { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { fetchShapeFactoryJsonPeek } from "./api";
import type { WorkProductPromptProfile, WorkProductPromptRow } from "./types";

type PeekPos = { top: number; left: number; maxHeight: number };

const JSON_CACHE = new Map<string, { text: string; basename?: string; truncated?: boolean; error?: string }>();

export function JsonPeekButton({ path, label }: { path: string; label: string }) {
  const btnRef = useRef<HTMLButtonElement>(null);
  const popRef = useRef<HTMLDivElement>(null);
  const hoverTimer = useRef<number | null>(null);
  const leaveTimer = useRef<number | null>(null);
  const panelId = useId();
  const [open, setOpen] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [pos, setPos] = useState<PeekPos>({ top: 0, left: 0, maxHeight: 360 });
  const [loading, setLoading] = useState(false);
  const [text, setText] = useState<string | null>(null);
  const [meta, setMeta] = useState<{ basename?: string; truncated?: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);

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
    const width = Math.min(520, window.innerWidth - pad * 2);
    let left = r.left;
    if (left + width > window.innerWidth - pad) left = Math.max(pad, window.innerWidth - pad - width);
    const spaceBelow = window.innerHeight - r.bottom - pad;
    const spaceAbove = r.top - pad;
    const preferBelow = spaceBelow >= 180 || spaceBelow >= spaceAbove;
    const maxHeight = Math.max(160, Math.min(480, preferBelow ? spaceBelow : spaceAbove));
    const top = preferBelow ? r.bottom + 6 : Math.max(pad, r.top - 6 - maxHeight);
    setPos({ top, left, maxHeight });
  };

  const load = async () => {
    const cached = JSON_CACHE.get(path);
    if (cached) {
      setText(cached.text);
      setMeta({ basename: cached.basename, truncated: cached.truncated });
      setError(cached.error || null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await fetchShapeFactoryJsonPeek(path);
      const body = res.text || "";
      JSON_CACHE.set(path, { text: body, basename: res.basename, truncated: res.truncated });
      setText(body);
      setMeta({ basename: res.basename, truncated: res.truncated });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      JSON_CACHE.set(path, { text: "", error: msg });
      setError(msg);
      setText(null);
    } finally {
      setLoading(false);
    }
  };

  const openPeek = (pin: boolean) => {
    clearTimers();
    setPinned(pin);
    setOpen(true);
    place();
    void load();
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
    const onResize = () => place();
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onResize);
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

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        className="work-product-json-link"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        title={`${path}\nHover to peek · click to pin`}
        onMouseEnter={() => {
          clearTimers();
          hoverTimer.current = window.setTimeout(() => openPeek(false), 180);
        }}
        onMouseLeave={() => {
          clearTimers();
          if (!pinned) {
            leaveTimer.current = window.setTimeout(() => setOpen(false), 160);
          }
        }}
        onFocus={() => openPeek(false)}
        onClick={(e) => {
          e.preventDefault();
          if (open && pinned) closePeek();
          else openPeek(true);
        }}
      >
        {label}
        <span className="work-product-json-link__tag">json</span>
      </button>
      {open
        ? createPortal(
            <div
              ref={popRef}
              id={panelId}
              role="dialog"
              aria-label={`JSON: ${meta?.basename || label}`}
              className={`work-product-json-pop${pinned ? " work-product-json-pop--pinned" : ""}`}
              style={{ top: pos.top, left: pos.left, maxHeight: pos.maxHeight }}
              onMouseEnter={() => clearTimers()}
              onMouseLeave={() => {
                if (!pinned) {
                  leaveTimer.current = window.setTimeout(() => setOpen(false), 160);
                }
              }}
            >
              <div className="work-product-json-pop__head">
                <strong className="work-product-json-pop__title">{meta?.basename || label}</strong>
                <div className="work-product-json-pop__actions">
                  {meta?.truncated ? <span className="work-product-json-pop__note">truncated</span> : null}
                  {pinned ? <span className="work-product-json-pop__note">pinned</span> : null}
                  <button type="button" className="work-product-json-pop__close" onClick={closePeek} aria-label="Close">
                    ×
                  </button>
                </div>
              </div>
              <div className="work-product-json-pop__path" title={path}>
                {path}
              </div>
              <pre className="work-product-json-pop__body">
                {loading ? "Loading…" : error ? error : text || "(empty)"}
              </pre>
            </div>,
            document.body,
          )
        : null}
    </>
  );
}

export function PromptMarkupTable({
  title,
  rows,
}: {
  title: string;
  rows: WorkProductPromptRow[];
}) {
  if (!rows.length) {
    return (
      <div className="work-product-prompt-table-wrap">
        <div className="work-product-prompt-table__title">{title}</div>
        <div className="work-product-prompt-table__empty">—</div>
      </div>
    );
  }
  return (
    <div className="work-product-prompt-table-wrap">
      <div className="work-product-prompt-table__title">{title}</div>
      <table className="work-product-prompt-table">
        <thead>
          <tr>
            <th scope="col" className="work-product-prompt-table__w">
              Weight
            </th>
            <th scope="col">Text</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const w = Number(row.weight);
            const emphasis = !Number.isFinite(w) ? 0 : Math.max(0, Math.min(1, (w - 1) / 1.2));
            return (
              <tr key={`${i}:${row.text.slice(0, 24)}`} title={row.raw || undefined}>
                <td className="work-product-prompt-table__w">
                  <span
                    className="work-product-prompt-weight"
                    style={{ ["--wp-emphasis" as string]: String(emphasis) }}
                  >
                    {Number.isFinite(w) ? (Math.round(w * 100) / 100).toString() : "—"}
                  </span>
                </td>
                <td className="work-product-prompt-table__text">{row.text}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Workbench-style hover/pin prompt viewer (positive/negative markup tables). */
export function PromptPeekButton({ prompt, label }: { prompt: WorkProductPromptProfile; label: string }) {
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
    const width = Math.min(560, window.innerWidth - pad * 2);
    let left = r.left;
    if (left + width > window.innerWidth - pad) left = Math.max(pad, window.innerWidth - pad - width);
    const spaceBelow = window.innerHeight - r.bottom - pad;
    const spaceAbove = r.top - pad;
    const preferBelow = spaceBelow >= 180 || spaceBelow >= spaceAbove;
    const maxHeight = Math.max(160, Math.min(520, preferBelow ? spaceBelow : spaceAbove));
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
    const onResize = () => place();
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onResize);
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

  const title = prompt.label || prompt.basename || label;
  const posRows = prompt.positive_rows || [];
  const negRows = prompt.negative_rows || [];

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        className="work-product-json-link"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        title={`${prompt.path || label}\nHover to peek decoded prompt · click to pin`}
        onMouseEnter={() => {
          clearTimers();
          hoverTimer.current = window.setTimeout(() => openPeek(false), 180);
        }}
        onMouseLeave={() => {
          clearTimers();
          if (!pinned) {
            leaveTimer.current = window.setTimeout(() => setOpen(false), 160);
          }
        }}
        onFocus={() => openPeek(false)}
        onClick={(e) => {
          e.preventDefault();
          if (open && pinned) closePeek();
          else openPeek(true);
        }}
      >
        {label}
        <span className="work-product-json-link__tag">prompt</span>
      </button>
      {open
        ? createPortal(
            <div
              ref={popRef}
              id={panelId}
              role="dialog"
              aria-label={`Prompt: ${title}`}
              className={`work-product-json-pop work-product-json-pop--prompt${pinned ? " work-product-json-pop--pinned" : ""}`}
              style={{ top: pos.top, left: pos.left, maxHeight: pos.maxHeight, width: Math.min(560, window.innerWidth - 16) }}
              onMouseEnter={() => clearTimers()}
              onMouseLeave={() => {
                if (!pinned) {
                  leaveTimer.current = window.setTimeout(() => setOpen(false), 160);
                }
              }}
            >
              <div className="work-product-json-pop__head">
                <strong className="work-product-json-pop__title">{title}</strong>
                <div className="work-product-json-pop__actions">
                  {pinned ? <span className="work-product-json-pop__note">pinned</span> : null}
                  {prompt.path ? <JsonPeekButton path={prompt.path} label="raw json" /> : null}
                  <button type="button" className="work-product-json-pop__close" onClick={closePeek} aria-label="Close">
                    ×
                  </button>
                </div>
              </div>
              {prompt.path ? (
                <div className="work-product-json-pop__path" title={prompt.path}>
                  {prompt.path}
                </div>
              ) : null}
              <div className="work-product-json-pop__body work-product-json-pop__body--prompt">
                {prompt.missing ? (
                  <div className="work-product-prompt-table__empty">Prompt file missing</div>
                ) : prompt.error ? (
                  <div className="work-product-prompt-table__empty">{prompt.error}</div>
                ) : (
                  <>
                    <PromptMarkupTable title="Positive" rows={posRows} />
                    {negRows.length > 0 || (prompt.negative && prompt.negative.trim()) ? (
                      <PromptMarkupTable title="Negative" rows={negRows} />
                    ) : null}
                  </>
                )}
              </div>
            </div>,
            document.body,
          )
        : null}
    </>
  );
}
