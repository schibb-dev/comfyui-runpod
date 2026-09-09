import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { fetchHomeSummary, fetchHourlyChainBacklogs, fetchHourlySchedule, setHourlySchedule } from "./api";
import { workbenchHref } from "./discoveryDeepLink";
import {
  backlogItemByKey,
  backlogNavItems,
  backlogNextPicks,
  stepBacklogNav,
} from "./hourlyBacklogNav";
import { PipelineMediaPlayer } from "./PipelineMediaPlayer";
import { routeHref } from "./routes";
import { comfyHealthIsBackoff, comfyHealthSummary } from "./comfyHealth";
import type {
  HomeSummaryResponse,
  HourlyChainBacklog,
  HourlyChainBacklogItem,
  HourlyChainBacklogsResponse,
  HourlyScheduleStatus,
  HourlySubmitMode,
} from "./types";

function fileUrlFromRel(relpath?: string | null): string {
  if (!relpath) return "";
  return "/files/" + encodeURIComponent(relpath.replace(/\\/g, "/"));
}

function num(n?: number | null): string {
  return typeof n === "number" && Number.isFinite(n) ? String(n) : "—";
}

function formatDue(iso?: string | null): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

const INTERVAL_PRESETS = [15, 20, 30, 45, 60, 90, 120];

function HourlyScheduleControls({
  initial,
  onSaved,
}: {
  initial?: HourlyScheduleStatus | null;
  onSaved: (s: HourlyScheduleStatus) => void;
}) {
  const sch = initial?.schedule;
  const [interval, setIntervalMin] = useState(sch?.interval_minutes ?? 20);
  const [enabled, setEnabled] = useState(sch?.enabled !== false);
  const [mode, setMode] = useState<HourlySubmitMode>(
    (sch?.submit_mode as HourlySubmitMode) || "auto",
  );
  const [comfyMax, setComfyMax] = useState(sch?.comfy_queue_max ?? 3);
  const [pendingMax, setPendingMax] = useState(sch?.pending_queue_max ?? 10);
  const [hourlyMin, setHourlyMin] = useState(sch?.pending_hourly_min ?? 5);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!sch) return;
    setIntervalMin(sch.interval_minutes ?? 20);
    setEnabled(sch.enabled !== false);
    setMode((sch.submit_mode as HourlySubmitMode) || "auto");
    setComfyMax(sch.comfy_queue_max ?? 3);
    setPendingMax(sch.pending_queue_max ?? 10);
    setHourlyMin(sch.pending_hourly_min ?? 5);
  }, [sch]);

  const apply = async () => {
    setBusy(true);
    setErr("");
    try {
      const res = await setHourlySchedule({
        interval_minutes: Number(interval),
        enabled,
        submit_mode: mode,
        comfy_queue_max: Number(comfyMax),
        pending_queue_max: Number(pendingMax),
        pending_hourly_min: Number(hourlyMin),
      });
      onSaved(res);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const ruleHint =
    mode === "comfy"
      ? "Generate only when Comfy waiting is below max; otherwise skip. No hourly pending floor."
      : mode === "pending"
        ? "Keep this many hourly jobs on the pending FIFO; refill if the count dips. Drain takes overflow. Submit Queue/Next can still add and reorder."
        : "Keep this many hourly jobs on the pending FIFO; refill if the count dips. Extra hourlies and Submit jobs drain into Comfy.";

  return (
    <div className="home-hourly-controls">
      <div className="home-hourly-controls__row">
        <label className="home-hourly-controls__field">
          <span>Interval</span>
          <select value={interval} disabled={busy} onChange={(e) => setIntervalMin(Number(e.target.value))}>
            {(initial?.interval_presets?.length ? initial.interval_presets : INTERVAL_PRESETS).map((m) => (
              <option key={m} value={m}>
                {m} min
              </option>
            ))}
          </select>
        </label>
        <label className="home-hourly-controls__field">
          <span>Mode</span>
          <select value={mode} disabled={busy} onChange={(e) => setMode(e.target.value as HourlySubmitMode)}>
            <option value="auto">Auto</option>
            <option value="comfy">Comfy</option>
            <option value="pending">Pending</option>
          </select>
        </label>
        <label className="home-hourly-controls__field home-hourly-controls__field--num">
          <span>Comfy max</span>
          <input
            type="number"
            min={0}
            max={20}
            value={comfyMax}
            disabled={busy}
            onChange={(e) => setComfyMax(Number(e.target.value))}
          />
        </label>
        <label className="home-hourly-controls__field home-hourly-controls__field--num">
          <span>Pending max</span>
          <input
            type="number"
            min={0}
            max={50}
            value={pendingMax}
            disabled={busy}
            onChange={(e) => setPendingMax(Number(e.target.value))}
          />
        </label>
        <label
          className="home-hourly-controls__field home-hourly-controls__field--num"
          title="Hourly jobs kept on the pending queue. Drain refills if the count falls below this."
        >
          <span>Hourly min</span>
          <input
            type="number"
            min={0}
            max={50}
            value={hourlyMin}
            disabled={busy}
            onChange={(e) => setHourlyMin(Number(e.target.value))}
          />
        </label>
        <label className="home-hourly-controls__toggle">
          <input type="checkbox" checked={enabled} disabled={busy} onChange={(e) => setEnabled(e.target.checked)} />
          <span>Enabled</span>
        </label>
        <button type="button" className="drt-btn" disabled={busy} onClick={() => void apply()}>
          {busy ? "Saving…" : "Apply"}
        </button>
      </div>
      <p className="home-hourly-controls__meta factory-muted">
        Next due {formatDue(initial?.next_due_at)}
        {initial?.due ? " · due now" : ""}
        {" · "}
        waiting {num(initial?.comfy_waiting)} · running {num(initial?.comfy_running)} · pending{" "}
        {num(initial?.factory_pending)}
        {" · "}
        hourlies {num(initial?.factory_hourly_pending)}/{num(sch?.pending_hourly_min ?? hourlyMin)}
        {comfyHealthIsBackoff(initial?.comfy_health)
          ? ` · ${comfyHealthSummary(initial?.comfy_health, initial?.comfy_health?.retry_in_sec)}`
          : ""}
        {initial?.still_promo?.until ? ` · image starters promoted until ${formatDue(initial.still_promo.until)}` : ""}
        {initial?.faceblast_promo?.until
          ? ` · FaceBlast-extend prompts promoted until ${formatDue(initial.faceblast_promo.until)}`
          : ""}
      </p>
      <p className="home-hourly-controls__hint factory-muted">{ruleHint}</p>
      {err ? <p className="home-hourly-controls__err">{err}</p> : null}
    </div>
  );
}

function FreshThumb({ item }: { item: HomeSummaryFreshOutput }) {
  // Prefer an explicit thumb; fall back to a companion .png next to an mp4, then the file itself.
  const guessThumb =
    item.thumb_url ||
    (item.relpath && /\.mp4$/i.test(item.relpath)
      ? fileUrlFromRel(item.relpath.replace(/\.mp4$/i, ".png"))
      : "") ||
    item.url ||
    "";
  const rating = item.ratings?.rating_effective;
  return (
    <a className="home-thumb" href={discoveryLibraryHref(item.relpath)} title={basename(item.relpath)}>
      <img
        className="home-thumb__img"
        src={guessThumb}
        alt=""
        loading="lazy"
        onError={(e) => {
          const img = e.currentTarget;
          if (img.dataset.fallback !== "1" && item.url && img.src !== item.url) {
            img.dataset.fallback = "1";
            img.src = item.url;
          }
        }}
      />
      {typeof rating === "number" && rating > 0 ? (
        <span className="home-thumb__rating">★ {rating.toFixed(rating >= 1 ? 1 : 2)}</span>
      ) : null}
    </a>
  );
}

function backlogThumbUrl(item: HourlyChainBacklogItem): string {
  if (item.thumb_url) return item.thumb_url;
  const rel = item.video_relpath || "";
  if (rel && /\.mp4$/i.test(rel)) return fileUrlFromRel(rel.replace(/\.mp4$/i, ".png"));
  if (item.video_url) return item.video_url;
  return "";
}

function BacklogThumb({ item }: { item: HourlyChainBacklogItem }) {
  const src = backlogThumbUrl(item);
  if (!src) return <span className="home-backlog-thumb home-backlog-thumb--empty" aria-hidden />;
  return (
    <img
      className="home-backlog-thumb"
      src={src}
      alt=""
      loading="lazy"
      onError={(e) => {
        const img = e.currentTarget;
        if (img.dataset.fallback !== "1" && item.video_url && img.src !== item.video_url) {
          img.dataset.fallback = "1";
          img.src = item.video_url;
          return;
        }
        img.dataset.fallback = "1";
        img.classList.add("home-backlog-thumb--empty");
        img.removeAttribute("src");
      }}
    />
  );
}

function HourlyBacklogPendingModal({
  chain,
  item,
  index,
  count,
  onClose,
  onStep,
}: {
  chain: HourlyChainBacklog;
  item: HourlyChainBacklogItem;
  index: number;
  count: number;
  onClose: () => void;
  onStep: (delta: number) => void;
}) {
  const preview = item.pending_preview || {};
  const family = preview.family || item.consumer_family || "child";
  const promptName = preview.prompt_name || preview.prompt_label || preview.prompt_slug || "default";
  const rank = typeof item.next_rank === "number" ? item.next_rank : item.next ? 1 : 0;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopImmediatePropagation();
        onClose();
        return;
      }
      if (e.key === "ArrowDown" || e.key === "ArrowRight") {
        e.preventDefault();
        e.stopImmediatePropagation();
        onStep(1);
        return;
      }
      if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
        e.preventDefault();
        e.stopImmediatePropagation();
        onStep(-1);
        return;
      }
      if (e.code === "Space" || e.key === " ") {
        e.preventDefault();
        e.stopImmediatePropagation();
      }
    };
    window.addEventListener("keydown", onKey, { capture: true });
    return () => window.removeEventListener("keydown", onKey, { capture: true });
  }, [onClose, onStep]);

  return createPortal(
    <div
      className="modal-overlay home-backlog-modal-overlay"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={`Pending preview ${family}`}
    >
      <div className="modal home-backlog-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title">
            Pending preview · {family}
            {rank ? ` · pick ${rank}` : ""}
            {count > 0 ? ` · ${index + 1}/${count}` : ""}
          </div>
          <div className="modal-actions">
            <button type="button" className="drt-btn" disabled={count < 2} onClick={() => onStep(-1)}>
              ←
            </button>
            <button type="button" className="drt-btn" disabled={count < 2} onClick={() => onStep(1)}>
              →
            </button>
            <a className="drt-btn" href={workbenchHref({ jobKey: item.job_key })}>
              Parent on Workbench
            </a>
            <button type="button" onClick={onClose}>
              Close
            </button>
          </div>
        </div>
        <div className="home-backlog-modal__body">
          <PipelineMediaPlayer
            videoUrl={item.video_url}
            thumbUrl={backlogThumbUrl(item)}
            mediaKey={item.job_key || item.video_name}
            alt={item.video_name || "source clip"}
            className="home-backlog-modal__player"
          />
          <div className="home-backlog-modal__meta">
            <p>
              Hourly would enqueue a <strong>{family}</strong> job on the pending FIFO, using this
              {item.producer_family ? ` ${item.producer_family}` : ""} clip as <code>source_video</code>.
            </p>
            <dl>
              <div>
                <dt>Prompt</dt>
                <dd>
                  {promptName}
                  {preview.prompt_slug ? <span className="factory-muted"> · {preview.prompt_slug}</span> : null}
                </dd>
              </div>
              <div>
                <dt>From</dt>
                <dd className="mono">{item.job_key}</dd>
              </div>
              <div>
                <dt>Source</dt>
                <dd className="mono">{item.video_name}</dd>
              </div>
              <div>
                <dt>Chain</dt>
                <dd>
                  {chain.label}
                  {rank === 1 ? " · next drain pick" : rank ? ` · upcoming pick ${rank}` : ""}
                </dd>
              </div>
            </dl>
            {preview.prompt_excerpt ? (
              <blockquote className="home-backlog-modal__excerpt">{preview.prompt_excerpt}</blockquote>
            ) : null}
            <p className="factory-muted">↑↓ or ←→ next item · Esc close</p>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function isTypingTarget(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
}

function focusBacklogKey(root: HTMLElement | null, jobKey: string) {
  if (!root || !jobKey) return;
  const nodes = root.querySelectorAll<HTMLElement>(`[data-backlog-key="${CSS.escape(jobKey)}"]`);
  nodes.forEach((node) => node.scrollIntoView({ block: "nearest" }));
  const prefer = root.querySelector<HTMLElement>(
    `.home-backlog-items [data-backlog-key="${CSS.escape(jobKey)}"]`,
  );
  (prefer || nodes[0])?.focus();
}

function HourlyChainBacklogsCard({ refreshToken }: { refreshToken: number }) {
  const [data, setData] = useState<HourlyChainBacklogsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [openId, setOpenId] = useState<string>("i2v_to_gex");
  const [familyFilter, setFamilyFilter] = useState<Record<string, string>>({});
  const [selectedKey, setSelectedKey] = useState<string>("");
  const [viewerKey, setViewerKey] = useState<string>("");
  const cardRef = useRef<HTMLDivElement | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setData(await fetchHourlyChainBacklogs());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshToken]);

  const chains = data?.chains ?? [];
  const openChain = chains.find((c) => c.id === openId) || null;
  const filter = familyFilter[openId] || "all";
  const nav = useMemo(
    () => (openChain ? backlogNavItems(openChain, filter) : []),
    [openChain, filter],
  );
  const selectedItem = backlogItemByKey(openChain, selectedKey) || nav[0] || null;
  const viewerItem = viewerKey ? backlogItemByKey(openChain, viewerKey) : null;
  const viewerIndex = viewerItem
    ? nav.findIndex((it) => it.job_key === viewerItem.job_key)
    : -1;

  const selectItem = useCallback(
    (item: HourlyChainBacklogItem | null, opts?: { openPreview?: boolean; focus?: boolean }) => {
      const key = String(item?.job_key || "");
      setSelectedKey(key);
      if (opts?.openPreview && key) setViewerKey(key);
      if (opts?.focus && key) {
        requestAnimationFrame(() => focusBacklogKey(cardRef.current, key));
      }
    },
    [],
  );

  const stepNav = useCallback(
    (delta: number, opts?: { openPreview?: boolean }) => {
      const next = stepBacklogNav(nav, selectedKey || viewerKey, delta);
      if (next) selectItem(next, { openPreview: opts?.openPreview ?? Boolean(viewerKey), focus: true });
    },
    [nav, selectItem, selectedKey, viewerKey],
  );

  useEffect(() => {
    if (!openChain) return;
    if (selectedKey && backlogItemByKey(openChain, selectedKey)) return;
    const first = nav[0];
    if (first?.job_key) setSelectedKey(String(first.job_key));
  }, [nav, openChain, selectedKey]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (viewerKey) return;
      if (isTypingTarget(e.target)) return;
      const root = cardRef.current;
      if (!root || !(e.target instanceof Node) || !root.contains(e.target)) return;
      if (e.key === "ArrowDown" || e.key === "ArrowRight") {
        e.preventDefault();
        stepNav(1);
        return;
      }
      if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
        e.preventDefault();
        stepNav(-1);
        return;
      }
      if (e.code === "Space" || e.key === " ") {
        const t = e.target;
        if (t instanceof HTMLElement && t.closest(".home-backlog-fams, .home-backlog-chain__head")) {
          return;
        }
        if (!selectedItem) return;
        e.preventDefault();
        setViewerKey(String(selectedItem.job_key || ""));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectedItem, stepNav, viewerKey]);

  return (
    <Panel
      title="Chain backlogs"
      hint="Parents waiting for a child — not the pending FIFO"
      footer={
        <span className="home-backlog-foot">
          <a className="home-cta" href={routeHref("workbench")}>
            Open Workbench pending →
          </a>
          <a className="home-cta" href={routeHref("queue")}>
            Queue ledger →
          </a>
        </span>
      }
    >
      <div ref={cardRef} className="home-backlog-card" tabIndex={-1}>
        {loading && !data ? <p className="factory-muted">Scanning complete parents…</p> : null}
        {error ? <p className="home-hourly-controls__err">{error}</p> : null}
        {data?.note ? <p className="home-hourly-controls__hint">{data.note}</p> : null}
        {typeof data?.cursor === "number" ? (
          <p className="home-hourly-controls__meta">
            Cursor {data.cursor}
            {loading ? " · refreshing…" : ""}
          </p>
        ) : null}
        <div className="home-backlog-list">
          {chains.map((chain) => (
            <HourlyBacklogChain
              key={chain.id || chain.label}
              chain={chain}
              open={openId === chain.id}
              familyFilter={familyFilter[chain.id || ""] || "all"}
              selectedKey={openId === chain.id ? selectedKey : ""}
              onToggle={() => setOpenId((cur) => (cur === chain.id ? "" : String(chain.id || "")))}
              onFamily={(fam) =>
                setFamilyFilter((prev) => ({ ...prev, [chain.id || ""]: fam }))
              }
              onSelectItem={(item) => selectItem(item, { openPreview: false })}
              onOpenItem={(item) => selectItem(item, { openPreview: true })}
            />
          ))}
        </div>
      </div>
      {openChain && viewerItem ? (
        <HourlyBacklogPendingModal
          chain={openChain}
          item={viewerItem}
          index={Math.max(0, viewerIndex)}
          count={nav.length}
          onClose={() => {
            setViewerKey("");
            requestAnimationFrame(() =>
              focusBacklogKey(cardRef.current, String(viewerItem.job_key || selectedKey)),
            );
          }}
          onStep={(delta) => stepNav(delta, { openPreview: true })}
        />
      ) : null}
    </Panel>
  );
}

function HourlyBacklogChain({
  chain,
  open,
  familyFilter,
  selectedKey,
  onToggle,
  onFamily,
  onSelectItem,
  onOpenItem,
}: {
  chain: HourlyChainBacklog;
  open: boolean;
  familyFilter: string;
  selectedKey: string;
  onToggle: () => void;
  onFamily: (family: string) => void;
  onSelectItem: (item: HourlyChainBacklogItem) => void;
  onOpenItem: (item: HourlyChainBacklogItem) => void;
}) {
  const items = chain.items ?? [];
  const visible =
    familyFilter === "all"
      ? items
      : items.filter((it) => (it.producer_family || "") === familyFilter);
  const nextPicks = backlogNextPicks(chain);
  const families = Object.entries(chain.by_family || {}).sort((a, b) => b[1] - a[1]);
  const lookback =
    typeof chain.lookback_days === "number"
      ? `last ${chain.lookback_days}d`
      : chain.lookback_note
        ? "no age cull"
        : null;

  return (
    <section className="home-backlog-chain">
      <button type="button" className="home-backlog-chain__head" onClick={onToggle} aria-expanded={open}>
        <span className="home-backlog-chain__title">{chain.label}</span>
        <span className="home-backlog-chain__count mono">{chain.count ?? 0}</span>
        <span className="home-backlog-chain__meta">
          every {chain.drain_every ?? "?"} cursor
          {chain.due_this_cursor ? " · due now" : ""}
          {lookback ? ` · ${lookback}` : ""}
        </span>
      </button>
      {open ? (
        <div className="home-backlog-chain__body">
          {chain.lookback_note ? <p className="factory-muted">{chain.lookback_note}</p> : null}
          {nextPicks.length ? (
            <ol className="home-backlog-nexts" aria-label="Next hourly picks">
              {nextPicks.map((it, i) => {
                const selected = it.job_key === selectedKey;
                return (
                  <li
                    key={it.job_key || i}
                    className={[it.next || i === 0 ? "is-next" : "", selected ? "is-selected" : ""]
                      .filter(Boolean)
                      .join(" ")}
                  >
                    <button
                      type="button"
                      className="home-backlog-row"
                      data-backlog-key={it.job_key}
                      aria-selected={selected}
                      title={it.job_key || it.video_name || ""}
                      onClick={() => onOpenItem(it)}
                      onFocus={() => onSelectItem(it)}
                    >
                      <span className="home-backlog-nexts__n">{i + 1}</span>
                      <BacklogThumb item={it} />
                      <span className="home-backlog-row__text">
                        {it.producer_family ? <strong>{it.producer_family}</strong> : null}
                        {i === 0 ? <em>next</em> : <em>pick {i + 1}</em>}
                        <span className="factory-muted">{it.video_name || it.job_key}</span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ol>
          ) : (
            <p className="factory-muted">Nothing waiting.</p>
          )}
          {nextPicks.length ? (
            <p className="home-backlog-keys factory-muted">↑↓ select · Space preview</p>
          ) : null}
          {families.length > 1 ? (
            <div className="home-backlog-fams" role="group" aria-label="Filter by producer family">
              <button
                type="button"
                className={familyFilter === "all" ? "is-on" : undefined}
                onClick={() => onFamily("all")}
              >
                all {chain.count ?? 0}
              </button>
              {families.map(([fam, n]) => (
                <button
                  key={fam}
                  type="button"
                  className={familyFilter === fam ? "is-on" : undefined}
                  onClick={() => onFamily(fam)}
                >
                  {fam} {n}
                </button>
              ))}
            </div>
          ) : null}
          {visible.length > 0 ? (
            <ul className="home-backlog-items">
              {visible.map((it) => {
                const selected = it.job_key === selectedKey;
                const rank = typeof it.next_rank === "number" ? it.next_rank : it.next ? 1 : 0;
                return (
                  <li
                    key={it.job_key}
                    className={[rank ? "is-next" : "", selected ? "is-selected" : ""]
                      .filter(Boolean)
                      .join(" ") || undefined}
                  >
                    <button
                      type="button"
                      className="home-backlog-row"
                      data-backlog-key={it.job_key}
                      aria-selected={selected}
                      title={it.job_key || it.video_name || ""}
                      onClick={() => onOpenItem(it)}
                      onFocus={() => onSelectItem(it)}
                    >
                      <BacklogThumb item={it} />
                      <span className="home-backlog-row__text">
                        {it.producer_family ? <strong>{it.producer_family}</strong> : null}
                        {rank ? <em>{rank === 1 ? "next" : `#${rank}`}</em> : null}
                        <span className="factory-muted">{it.video_name || it.job_key}</span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function Panel({
  title,
  hint,
  children,
  footer,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  return (
    <section className="home-card sfmap-hourlies-card">
      <div className="home-card__head">
        <h2 className="home-card__title">{title}</h2>
        {hint ? <span className="home-card__hint factory-muted">{hint}</span> : null}
      </div>
      <div className="home-card__body">{children}</div>
      {footer ? <div className="home-card__foot">{footer}</div> : null}
    </section>
  );
}

type HourlyNextSampleInfo = NonNullable<HomeSummaryResponse["hourly"]>["next_sample"];

function HourlyNextSample({ sample }: { sample?: HourlyNextSampleInfo }) {
  if (!sample) {
    return <p className="factory-muted">No hourly sample queued.</p>;
  }
  return (
    <dl className="home-hourly">
      {sample.sample_id ? (
        <div>
          <dt>sample</dt>
          <dd className="mono">{sample.sample_id}</dd>
        </div>
      ) : null}
      {typeof sample.pick_index === "number" ? (
        <div>
          <dt>pick</dt>
          <dd className="mono">#{sample.pick_index}</dd>
        </div>
      ) : null}
      {sample.gex2_prompt ? (
        <div>
          <dt>prompt</dt>
          <dd>{sample.gex2_prompt}</dd>
        </div>
      ) : null}
      {sample.note ? (
        <div>
          <dt>note</dt>
          <dd className="factory-muted">{sample.note}</dd>
        </div>
      ) : null}
    </dl>
  );
}

export function HourlyFactoryPanel({ refreshToken = 0 }: { refreshToken?: number }) {
  const [summary, setSummary] = useState<HomeSummaryResponse | null>(null);
  const [schedule, setSchedule] = useState<HourlyScheduleStatus | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const sch = await fetchHourlySchedule();
      setSchedule(sch);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
    try {
      setSummary(await fetchHomeSummary());
    } catch {
      /* schedule already loaded; next-sample is optional */
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshToken]);

  return (
    <div className="sfmap-hourlies">
      {error ? <p className="home-hourly-controls__err">{error}</p> : null}
      <Panel
        title="Schedule"
        hint="Cadence, Comfy caps, and the hourly pending floor"
        footer={
          <span className="home-backlog-foot">
            <a className="home-cta" href={routeHref("queue")}>
              Queue ledger →
            </a>
            <a className="home-cta" href={routeHref("workbench")}>
              Workbench pending →
            </a>
          </span>
        }
      >
        <HourlyScheduleControls
          initial={schedule ?? summary?.hourly?.schedule ?? null}
          onSaved={(s) => setSchedule(s)}
        />
        <HourlyNextSample sample={summary?.hourly?.next_sample} />
      </Panel>
      <HourlyChainBacklogsCard refreshToken={refreshToken} />
    </div>
  );
}

export function HourlyHomeTeaser({
  schedule,
  nextSample,
}: {
  schedule?: HourlyScheduleStatus | null;
  nextSample?: HourlyNextSampleInfo;
}) {
  const sch = schedule?.schedule;
  return (
    <>
      <p className="home-hourly-controls__meta factory-muted">
        Next due {formatDue(schedule?.next_due_at)}
        {schedule?.due ? " · due now" : ""}
        {" · "}
        hourlies {num(schedule?.factory_hourly_pending)}/{num(sch?.pending_hourly_min ?? 5)}
        {" · "}
        mode {sch?.submit_mode || "auto"}
      </p>
      <HourlyNextSample sample={nextSample} />
    </>
  );
}

