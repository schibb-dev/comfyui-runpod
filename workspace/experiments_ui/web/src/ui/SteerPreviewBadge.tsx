import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { fetchHourlyBinLookup, steerWorkProductCombos } from "./api";
import type { HourlyBinLookupResponse, HourlyBinLookupTarget } from "./types";

const HOVER_CLOSE_MS = 280;
const STRENGTHS = [
  { id: "pin", label: "Pin" },
  { id: "keep", label: "Keep" },
  { id: "later", label: "Later" },
  { id: "out", label: "Out" },
  { id: "clear", label: "Clear" },
] as const;

type PopoverPos = { top: number; left: number; placeAbove: boolean };

function statusGlyph(status: string | null | undefined): string {
  if (status === "pin") return "P";
  if (status === "keep") return "K";
  if (status === "later") return "L";
  if (status === "out") return "O";
  return "↣";
}

function extractContentId(relpath?: string | null, explicit?: string | null): string {
  const ex = String(explicit || "").trim().toLowerCase();
  if (ex) {
    if (/^[0-9a-f]{64}$/.test(ex) || /^clip_[0-9a-f]{32}$/.test(ex) || ex.startsWith("whole:") || /^seed:[0-9a-f]{32}$/.test(ex)) {
      return ex;
    }
  }
  const m = String(relpath || "").match(/[0-9a-f]{64}/i);
  return m ? m[0].toLowerCase() : "";
}

function seedBasename(relpath?: string | null): string {
  const s = String(relpath || "").replace(/\\/g, "/").trim();
  if (!s) return "";
  const base = s.split("/").pop() || s;
  return base.length > 42 ? `${base.slice(0, 18)}…${base.slice(-18)}` : base;
}

/** Upper-left try-bias badge: human arbiter for seed × workflow × variant combos. */
export function SteerPreviewBadge({
  relpath,
  contentId,
  familySlug,
  variantSlug,
  variantId,
  variantName,
  jobKey,
  assetKind = "still",
  size = "default",
  className,
}: {
  relpath?: string | null;
  contentId?: string | null;
  familySlug?: string | null;
  variantSlug?: string | null;
  variantId?: string | null;
  variantName?: string | null;
  jobKey?: string | null;
  assetKind?: "still" | "video" | string;
  size?: "default" | "sm";
  className?: string;
}) {
  const cid = useMemo(() => extractContentId(relpath, contentId), [relpath, contentId]);
  const kind = assetKind === "video" || assetKind === "clip" ? "video" : "still";
  const [open, setOpen] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [lookup, setLookup] = useState<HourlyBinLookupResponse | null>(null);
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [pos, setPos] = useState<PopoverPos | null>(null);
  const wrapRef = useRef<HTMLSpanElement | null>(null);
  const popRef = useRef<HTMLDivElement | null>(null);
  const closeTimer = useRef<number | null>(null);

  const cancelClose = useCallback(() => {
    if (closeTimer.current != null) {
      window.clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
  }, []);

  const scheduleClose = useCallback(() => {
    cancelClose();
    closeTimer.current = window.setTimeout(() => {
      if (pinned) return;
      setOpen(false);
    }, HOVER_CLOSE_MS);
  }, [cancelClose, pinned]);

  const openNow = useCallback(() => {
    cancelClose();
    setOpen(true);
  }, [cancelClose]);

  useEffect(() => () => cancelClose(), [cancelClose]);

  const refresh = useCallback(async () => {
    const rel = String(relpath || "").trim();
    if (!cid && !rel) return;
    try {
      const res = await fetchHourlyBinLookup({ contentId: cid || null, relpath: rel || null, kind });
      setLookup(res);
      const by = res.by_family || {};
      const next = new Set<string>();
      const fam0 = String(familySlug || "").trim();
      if (fam0) next.add(fam0);
      for (const [fam, row] of Object.entries(by)) {
        if (row?.status) next.add(fam);
      }
      setSelected(next);
      setMsg("");
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  }, [cid, relpath, kind, familySlug]);

  useEffect(() => {
    if (!open) return;
    if (!cid && !String(relpath || "").trim()) return;
    void refresh();
  }, [open, cid, relpath, refresh]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setPinned(false);
        setOpen(false);
      }
    };
    const onPointerDown = (e: PointerEvent) => {
      const t = e.target as Node | null;
      if (wrapRef.current?.contains(t) || popRef.current?.contains(t)) return;
      setPinned(false);
      setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onPointerDown, true);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", onPointerDown, true);
    };
  }, [open]);

  useLayoutEffect(() => {
    if (!open || !wrapRef.current) {
      setPos(null);
      return;
    }
    const place = () => {
      const r = wrapRef.current?.getBoundingClientRect();
      if (!r) return;
      const width = popRef.current?.offsetWidth || 280;
      const height = popRef.current?.offsetHeight || 220;
      const gap = 6;
      const placeAbove = r.bottom + gap + height > window.innerHeight - 8 && r.top > height + gap;
      const top = placeAbove ? r.top - height - gap : r.bottom + gap;
      const left = Math.min(Math.max(8, r.left), window.innerWidth - width - 8);
      setPos({ top, left, placeAbove });
    };
    place();
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [open, lookup, selected, msg, size]);

  const targets: HourlyBinLookupTarget[] = lookup?.targets || [];
  const byFamily = lookup?.by_family || {};
  const steered = Object.entries(byFamily).filter(([, row]) => row?.status);
  const primaryStatus = steered[0]?.[1]?.status || null;
  const glyph = statusGlyph(primaryStatus);
  const title = steered.length
    ? steered.map(([fam, row]) => `${statusGlyph(row.status)} · ${fam}`).join(", ")
    : "Try combos — bias workflows for this seed (you are the arbiter)";

  const toggleFam = (fam: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(fam)) next.delete(fam);
      else next.add(fam);
      return next;
    });
  };

  const apply = async (status: string) => {
    const rel = String(relpath || "").trim();
    if ((!cid && !rel) || busy || selected.size === 0) return;
    setBusy(true);
    setMsg("");
    try {
      const res = await steerWorkProductCombos({
        content_id: cid || undefined,
        relpath: rel || cid,
        status,
        families: [...selected],
        variant_slug: variantSlug || undefined,
        variant_id: variantId || undefined,
        variant_name: variantName || undefined,
        job_key: jobKey || undefined,
        kind,
        surface: "preview_steer",
      });
      if (res.lookup) setLookup(res.lookup);
      else await refresh();
      if (res.errors?.length) {
        setMsg(res.errors.map((e) => `${e.family}: ${e.error}`).join("; "));
      }
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!cid && !String(relpath || "").trim()) return null;

  const variantLabel =
    String(variantName || "").trim() ||
    String(variantSlug || "").trim() ||
    String(variantId || "").trim() ||
    "—";

  const popover = open
    ? createPortal(
        <div
          ref={popRef}
          className={"steer-preview-popover" + (pos?.placeAbove ? " steer-preview-popover--above" : "")}
          role="dialog"
          aria-label="Try combinations"
          style={pos ? { top: pos.top, left: pos.left } : { visibility: "hidden", top: 0, left: 0 }}
          onMouseEnter={openNow}
          onMouseLeave={scheduleClose}
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="steer-preview-popover__head">Try combinations</div>
          <p className="steer-preview-popover__hint">
            Bias hourlies to try these workflows with this seed. You are the arbiter — not a permanent grade.
          </p>
          <div className="steer-preview-popover__meta" title={String(relpath || cid)}>
            <span className="steer-preview-popover__k">Seed</span>
            <span className="mono">{seedBasename(relpath) || cid.slice(0, 12)}</span>
          </div>
          <div className="steer-preview-popover__meta">
            <span className="steer-preview-popover__k">Variant</span>
            <span>{variantLabel}</span>
          </div>
          <div className="steer-preview-popover__fams" role="group" aria-label="Workflows to try">
            {targets.length ? (
              targets.map((t) => {
                const fam = String(t.pool_family || "").trim();
                if (!fam) return null;
                const st = byFamily[fam]?.status || null;
                const on = selected.has(fam);
                return (
                  <label key={fam} className={"steer-preview-popover__fam" + (on ? " is-on" : "")}>
                    <input
                      type="checkbox"
                      checked={on}
                      disabled={busy}
                      onChange={() => toggleFam(fam)}
                    />
                    <span className="steer-preview-popover__fam-name" title={fam}>
                      {t.label || fam}
                    </span>
                    {st ? (
                      <span className={`steer-preview-popover__st steer-preview-popover__st--${st}`}>
                        {statusGlyph(st)}
                      </span>
                    ) : null}
                  </label>
                );
              })
            ) : (
              <p className="factory-muted">Loading workflows…</p>
            )}
          </div>
          <div className="steer-preview-popover__actions" role="group" aria-label="Try strength">
            {STRENGTHS.map((s) => (
              <button
                key={s.id}
                type="button"
                className={
                  "steer-preview-popover__btn" +
                  ` steer-preview-popover__btn--${s.id}` +
                  (s.id !== "clear" && selected.size > 0 && [...selected].every((f) => byFamily[f]?.status === s.id)
                    ? " is-on"
                    : "")
                }
                disabled={busy || selected.size === 0 || (s.id === "clear" && steered.length === 0)}
                onClick={() => void apply(s.id)}
              >
                {s.label}
              </button>
            ))}
          </div>
          {msg ? <p className="steer-preview-popover__msg">{msg}</p> : null}
        </div>,
        document.body,
      )
    : null;

  return (
    <span
      ref={wrapRef}
      className={
        "steer-preview-badge-wrap" +
        (size === "sm" ? " steer-preview-badge-wrap--sm" : "") +
        (open ? " steer-preview-badge-wrap--open" : "")
      }
      onMouseEnter={openNow}
      onMouseLeave={scheduleClose}
      onPointerDown={(e) => e.stopPropagation()}
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        setPinned((p) => {
          const next = !p;
          setOpen(true);
          return next;
        });
      }}
    >
      <span
        className={
          "steer-preview-badge" +
          (primaryStatus ? ` steer-preview-badge--${primaryStatus}` : " steer-preview-badge--unset") +
          (size === "sm" ? " steer-preview-badge--sm" : "") +
          (className ? ` ${className}` : "")
        }
        title={open ? undefined : title}
        aria-label={title}
        aria-haspopup="dialog"
        aria-expanded={open}
      >
        {glyph}
      </span>
      {popover}
    </span>
  );
}
