import React, { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { fetchHomeSummary, fetchHourlyChainBacklogs, setHourlySchedule } from "./api";
import { PageHeader } from "./PageHeader";
import { discoveryLibraryHref, workbenchHref } from "./discoveryDeepLink";
import { factoryMapFamilyHref, factoryMapIndexHref } from "./factoryMapRoute";
import { PipelineMediaPlayer } from "./PipelineMediaPlayer";
import { routeHref } from "./routes";
import type {
  HomeSummaryFreshOutput,
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

function basename(rel?: string | null): string {
  const p = (rel || "").replace(/\\/g, "/");
  return p.split("/").pop() || p;
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
        {initial?.still_promo?.until ? ` · image starters promoted until ${formatDue(initial.still_promo.until)}` : ""}
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
  onClose,
}: {
  chain: HourlyChainBacklog;
  item: HourlyChainBacklogItem;
  onClose: () => void;
}) {
  const preview = item.pending_preview || {};
  const family = preview.family || item.consumer_family || "child";
  const promptName = preview.prompt_name || preview.prompt_label || preview.prompt_slug || "default";
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopImmediatePropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey, { capture: true });
    return () => window.removeEventListener("keydown", onKey, { capture: true });
  }, [onClose]);

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
            {item.next ? " · next pick" : ""}
          </div>
          <div className="modal-actions">
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
                  {item.next ? " · this is the next drain pick" : ""}
                </dd>
              </div>
            </dl>
            {preview.prompt_excerpt ? (
              <blockquote className="home-backlog-modal__excerpt">{preview.prompt_excerpt}</blockquote>
            ) : null}
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function HourlyChainBacklogsCard({ refreshToken }: { refreshToken: number }) {
  const [data, setData] = useState<HourlyChainBacklogsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [openId, setOpenId] = useState<string>("i2v_to_gex");
  const [familyFilter, setFamilyFilter] = useState<Record<string, string>>({});
  const [viewer, setViewer] = useState<{ chain: HourlyChainBacklog; item: HourlyChainBacklogItem } | null>(
    null,
  );

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

  return (
    <Card
      title="Hourly chain backlogs"
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
            onToggle={() => setOpenId((cur) => (cur === chain.id ? "" : String(chain.id || "")))}
            onFamily={(fam) =>
              setFamilyFilter((prev) => ({ ...prev, [chain.id || ""]: fam }))
            }
            onOpenItem={(item) => setViewer({ chain, item })}
          />
        ))}
      </div>
      {viewer ? (
        <HourlyBacklogPendingModal
          chain={viewer.chain}
          item={viewer.item}
          onClose={() => setViewer(null)}
        />
      ) : null}
    </Card>
  );
}

function HourlyBacklogChain({
  chain,
  open,
  familyFilter,
  onToggle,
  onFamily,
  onOpenItem,
}: {
  chain: HourlyChainBacklog;
  open: boolean;
  familyFilter: string;
  onToggle: () => void;
  onFamily: (family: string) => void;
  onOpenItem: (item: HourlyChainBacklogItem) => void;
}) {
  const items = chain.items ?? [];
  const visible =
    familyFilter === "all"
      ? items
      : items.filter((it) => (it.producer_family || "") === familyFilter);
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
          {chain.next ? (
            <p className="home-backlog-next">
              <BacklogThumb item={chain.next} />
              <span>
                Next pick:{" "}
                <button
                  type="button"
                  className="home-backlog-linkish"
                  onClick={() => onOpenItem(chain.next!)}
                >
                  {chain.next.producer_family ? `${chain.next.producer_family} · ` : ""}
                  {chain.next.video_name || chain.next.job_key}
                </button>
              </span>
            </p>
          ) : (
            <p className="factory-muted">Nothing waiting.</p>
          )}
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
              {visible.map((it) => (
                <li key={it.job_key} className={it.next ? "is-next" : undefined}>
                  <button
                    type="button"
                    className="home-backlog-row"
                    title={it.job_key || it.video_name || ""}
                    onClick={() => onOpenItem(it)}
                  >
                    <BacklogThumb item={it} />
                    <span className="home-backlog-row__text">
                      {it.producer_family ? <strong>{it.producer_family}</strong> : null}
                      {it.next ? <em>next</em> : null}
                      <span className="factory-muted">{it.video_name || it.job_key}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function Card({
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
    <section className="home-card">
      <div className="home-card__head">
        <h2 className="home-card__title">{title}</h2>
        {hint ? <span className="home-card__hint factory-muted">{hint}</span> : null}
      </div>
      <div className="home-card__body">{children}</div>
      {footer ? <div className="home-card__foot">{footer}</div> : null}
    </section>
  );
}

export function HomeDashboard() {
  const [data, setData] = useState<HomeSummaryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [backlogTick, setBacklogTick] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const d = await fetchHomeSummary();
      setData(d);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const rating = data?.rating;
  const fresh = data?.fresh_outputs ?? [];
  const attention = data?.attention;
  const hourly = data?.hourly;
  const missingTotal = attention?.missing_sources_total ?? 0;
  const health = attention?.library_health;
  const healthIssues = health
    ? Object.entries(health).filter(([, v]) => typeof v === "number" && v > 0)
    : [];

  return (
    <div className="layout home-screen">
      <PageHeader
        title="Home"
        subtitle="Resume the loop — rate what's queued, triage fresh output, keep the factory fed."
        actions={
          <button
            type="button"
            className="drt-btn"
            disabled={loading}
            onClick={() => {
              setBacklogTick((n) => n + 1);
              void load();
            }}
          >
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        }
      />

      {error ? <p className="drt-err">{error}</p> : null}
      {loading && !data ? <p className="factory-muted">Loading dashboard…</p> : null}

      <div className="home-grid">
        <Card
          title="Continue rating"
          hint="Bootstrap the heuristics with a few quick calls"
          footer={
            <a className="home-cta" href={routeHref("rate")}>
              Open rate queue →
            </a>
          }
        >
          <div className="home-stat-row">
            <div className="home-stat">
              <span className="home-stat__num">{num(rating?.unrated_videos)}</span>
              <span className="home-stat__label">unrated videos</span>
            </div>
            <div className="home-stat">
              <span className="home-stat__num">{num(rating?.selected)}</span>
              <span className="home-stat__label">in next session</span>
            </div>
          </div>
          <div className="home-buckets" aria-label="Session mix">
            <span className="drq-bucket drq-bucket--down" title="Quick rejects">
              ↓ {num(rating?.buckets?.easy_down)}
            </span>
            <span className="drq-bucket drq-bucket--up" title="Likely keepers">
              ↑ {num(rating?.buckets?.easy_up)}
            </span>
            <span className="drq-bucket drq-bucket--mid" title="Middle band">
              ~ {num(rating?.buckets?.middle)}
            </span>
          </div>
        </Card>

        <Card
          title="Needs attention"
          hint="Fix these so generation + provenance stay clean"
          footer={
            <a className="home-cta" href={factoryMapIndexHref()}>
              Open factory map →
            </a>
          }
        >
          {missingTotal > 0 ? (
            <p className="home-attn home-attn--warn">
              {missingTotal} source{missingTotal === 1 ? "" : "s"} missing across{" "}
              {attention?.families?.length ?? 0} famil
              {(attention?.families?.length ?? 0) === 1 ? "y" : "ies"}.
            </p>
          ) : (
            <p className="home-attn home-attn--ok">No missing sources detected.</p>
          )}
          {attention?.families && attention.families.length > 0 ? (
            <ul className="home-fam-list">
              {attention.families.map((f) => (
                <li key={f.family_slug}>
                  <a href={factoryMapFamilyHref(f.family_slug || "")}>{f.family_slug}</a>
                  <span className="home-fam-count mono">{f.missing}</span>
                </li>
              ))}
            </ul>
          ) : null}
          {healthIssues.length > 0 ? (
            <p className="home-attn factory-muted">
              Index health:{" "}
              {healthIssues.map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(" · ")}
            </p>
          ) : null}
        </Card>

        <Card
          title="Next hourly run"
          hint="Cadence, queue routing, and what runs next"
          footer={
            <a className="home-cta" href={routeHref("queue")}>
              Open queue →
            </a>
          }
        >
          <HourlyScheduleControls
            initial={hourly?.schedule ?? null}
            onSaved={(s) => {
              setData((prev) => {
                if (!prev) return prev;
                return {
                  ...prev,
                  hourly: {
                    ...(prev.hourly || {}),
                    schedule: s,
                  },
                };
              });
            }}
          />
          {hourly?.next_sample ? (
            <dl className="home-hourly">
              {hourly.next_sample.sample_id ? (
                <div>
                  <dt>sample</dt>
                  <dd className="mono">{hourly.next_sample.sample_id}</dd>
                </div>
              ) : null}
              {typeof hourly.next_sample.pick_index === "number" ? (
                <div>
                  <dt>pick</dt>
                  <dd className="mono">#{hourly.next_sample.pick_index}</dd>
                </div>
              ) : null}
              {hourly.next_sample.gex2_prompt ? (
                <div>
                  <dt>prompt</dt>
                  <dd>{hourly.next_sample.gex2_prompt}</dd>
                </div>
              ) : null}
              {hourly.next_sample.note ? (
                <div>
                  <dt>note</dt>
                  <dd className="factory-muted">{hourly.next_sample.note}</dd>
                </div>
              ) : null}
            </dl>
          ) : (
            <p className="factory-muted">No hourly sample queued.</p>
          )}
          {data?.jobs?.summary && Object.keys(data.jobs.summary).length > 0 ? (
            <p className="home-jobs factory-muted">
              Jobs:{" "}
              {Object.entries(data.jobs.summary)
                .map(([k, v]) => `${k} ${v}`)
                .join(" · ")}
            </p>
          ) : null}
        </Card>
      </div>

      <HourlyChainBacklogsCard refreshToken={backlogTick} />

      <Card
        title="Fresh outputs"
        hint="Newest indexed results — click to open in Library"
        footer={
          <a className="home-cta" href={routeHref("library")}>
            Open library →
          </a>
        }
      >
        {fresh.length > 0 ? (
          <div className="home-thumb-strip">
            {fresh.map((it) => (
              <FreshThumb key={it.group_id || it.relpath} item={it} />
            ))}
          </div>
        ) : (
          <p className="factory-muted">No indexed outputs yet.</p>
        )}
      </Card>
    </div>
  );
}
