import React, { useCallback, useEffect, useState } from "react";
import { fetchHomeSummary, setHourlySchedule } from "./api";
import { PageHeader } from "./PageHeader";
import { discoveryLibraryHref } from "./discoveryDeepLink";
import { factoryMapFamilyHref, factoryMapIndexHref } from "./factoryMapRoute";
import { routeHref } from "./routes";
import type {
  HomeSummaryFreshOutput,
  HomeSummaryResponse,
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

const INTERVAL_PRESETS = [15, 30, 45, 60, 90, 120];

function HourlyScheduleControls({
  initial,
  onSaved,
}: {
  initial?: HourlyScheduleStatus | null;
  onSaved: (s: HourlyScheduleStatus) => void;
}) {
  const sch = initial?.schedule;
  const [interval, setIntervalMin] = useState(sch?.interval_minutes ?? 30);
  const [enabled, setEnabled] = useState(sch?.enabled !== false);
  const [mode, setMode] = useState<HourlySubmitMode>(
    (sch?.submit_mode as HourlySubmitMode) || "auto",
  );
  const [comfyMax, setComfyMax] = useState(sch?.comfy_queue_max ?? 3);
  const [pendingMax, setPendingMax] = useState(sch?.pending_queue_max ?? 4);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!sch) return;
    setIntervalMin(sch.interval_minutes ?? 30);
    setEnabled(sch.enabled !== false);
    setMode((sch.submit_mode as HourlySubmitMode) || "auto");
    setComfyMax(sch.comfy_queue_max ?? 3);
    setPendingMax(sch.pending_queue_max ?? 4);
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
      ? "Generate only when Comfy waiting is below max; otherwise skip."
      : mode === "pending"
        ? "Always leave new hourlies pending (up to pending max); drain feeds Comfy."
        : "Comfy if waiting < max; else pending if under pending max; else skip.";

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
          <button type="button" className="drt-btn" disabled={loading} onClick={() => void load()}>
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
