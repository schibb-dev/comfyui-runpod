import React, { useEffect, useMemo, useState } from "react";
import { comfyCancel, comfyClear, fetchComfyHistory, fetchQueue, saveQueueItemForLater } from "./api";
import { discoveryLibraryHref } from "./discoveryDeepLink";
import { MediaAssetCard } from "./MediaAssetCard";
import { PageHeader } from "./PageHeader";
import type { ComfyHistoryItem, QueueComfyItem, QueueResponse } from "./types";

function basename(rel?: string | null): string {
  const p = (rel || "").replace(/\\/g, "/");
  return p.split("/").pop() || p || "";
}

function shortId(pid?: string | null, n = 10): string {
  const s = (pid || "").trim();
  if (!s) return "(no id)";
  return s.length <= n ? s : `${s.slice(0, n)}…`;
}

function formatKeyParams(params?: Record<string, unknown> | null): string {
  if (!params || typeof params !== "object") return "";
  const order = ["seed", "noise_seed", "steps", "cfg", "sampler_name", "scheduler", "denoise", "model"];
  const parts: string[] = [];
  const seen = new Set<string>();
  for (const k of order) {
    if (!(k in params)) continue;
    seen.add(k);
    const v = params[k];
    if (v == null || v === "") continue;
    parts.push(`${k}=${String(v)}`);
  }
  for (const [k, v] of Object.entries(params)) {
    if (seen.has(k) || v == null || v === "") continue;
    parts.push(`${k}=${String(v)}`);
    if (parts.length >= 8) break;
  }
  return parts.join(" · ");
}

function queueThumb(item: QueueComfyItem): string | null {
  if (item.input_thumb_url) return item.input_thumb_url;
  if (item.input_media_kind === "image" && item.input_media_url) return item.input_media_url;
  // Companion PNG guess for video inputs (same convention as discovery / home).
  if (item.input_media_relpath && /\.(mp4|webm|mov|mkv)$/i.test(item.input_media_relpath)) {
    return "/files/" + encodeURIComponent(item.input_media_relpath.replace(/\.(mp4|webm|mov|mkv)$/i, ".png"));
  }
  return null;
}

function historyThumb(item: ComfyHistoryItem): string | null {
  if (item.output_thumb_url) return item.output_thumb_url;
  if (item.primary_image_url) return item.primary_image_url;
  if (item.input_thumb_url) return item.input_thumb_url;
  if (item.primary_video_relpath && /\.mp4$/i.test(item.primary_video_relpath)) {
    return "/files/" + encodeURIComponent(item.primary_video_relpath.replace(/\.mp4$/i, ".png"));
  }
  return null;
}

function QueueItemRow({
  item,
  kind,
  onRefresh,
}: {
  item: QueueComfyItem;
  kind: "running" | "pending";
  onRefresh: () => void;
}) {
  const pid = item.prompt_id ?? "";
  const thumb = queueThumb(item);
  const videoUrl = item.input_media_kind === "video" ? item.input_media_url : null;
  const title = item.workflow_name || basename(item.input_media_relpath) || shortId(pid, 16);
  const detailParts = [
    item.input_media_relpath ? basename(item.input_media_relpath) : null,
    formatKeyParams(item.key_params),
  ].filter(Boolean);
  const mediaType = item.input_media_kind === "video" ? "video" : item.input_media_kind === "image" ? "image" : undefined;

  return (
    <div className="queue-monitor-row">
      <MediaAssetCard
        name={title}
        path={item.input_media_relpath || pid || ""}
        mediaType={mediaType}
        thumbUrl={thumb}
        videoUrl={videoUrl}
        showVideoThumb={!thumb && Boolean(videoUrl)}
        badge={kind === "running" ? "running" : item.external ? "external" : "pending"}
        badgeClassName={
          kind === "running"
            ? "media-asset-card__badge--running"
            : item.external
              ? "media-asset-card__badge--external"
              : "media-asset-card__badge--pending"
        }
        status={!item.external && item.exp_id ? `${item.exp_id}/${item.run_id ?? ""}` : undefined}
        detail={detailParts.join(" · ") || undefined}
        showPath={false}
        className="discovery-list-asset-card"
      />
      <div className="queue-monitor-row__actions">
        <code className="mono queue-monitor-row__pid" title={pid || undefined}>
          {shortId(pid)}
        </code>
        <button
          type="button"
          disabled={!pid}
          title={kind === "running" ? "Interrupt current ComfyUI execution" : "Remove from pending queue"}
          onClick={() => {
            void (async () => {
              if (!pid) return;
              await comfyCancel({ prompt_id: pid, kind });
              onRefresh();
            })();
          }}
        >
          {kind === "running" ? "Interrupt" : "Cancel"}
        </button>
        <button
          type="button"
          title="Save this queue item for later"
          onClick={() => {
            void saveQueueItemForLater({
              title: item.workflow_name || `Saved ${pid || "queue item"}`,
              prompt_id: pid || undefined,
              tags: ["comfy-queue"],
              payload: {
                workflow_name: item.workflow_name ?? null,
                input_media_relpath: item.input_media_relpath ?? null,
                key_params: item.key_params ?? {},
                source: "comfy-queue-monitor",
              },
            });
          }}
        >
          Save
        </button>
      </div>
    </div>
  );
}

function historyWorkbenchRelpath(item: ComfyHistoryItem): string | null {
  for (const cand of [item.primary_video_relpath, item.primary_image_relpath, item.input_media_relpath]) {
    const rel = String(cand || "")
      .trim()
      .replace(/^\/+/, "")
      .replace(/\\/g, "/");
    if (rel) return rel;
  }
  return null;
}

function HistoryItemRow({ item }: { item: ComfyHistoryItem }) {
  const thumb = historyThumb(item);
  const videoUrl = item.primary_video_url || null;
  const title =
    item.workflow_name ||
    basename(item.primary_video_relpath) ||
    basename(item.primary_image_relpath) ||
    shortId(item.prompt_id, 16);
  const path = item.primary_video_relpath || item.primary_image_relpath || item.input_media_relpath || item.prompt_id;
  const workbenchRel = historyWorkbenchRelpath(item);
  const detailParts = [
    item.input_media_relpath ? `in ${basename(item.input_media_relpath)}` : null,
    formatKeyParams(item.key_params),
  ].filter(Boolean);
  const mediaType = videoUrl ? "video" : item.primary_image_url ? "image" : undefined;

  return (
    <div className="queue-monitor-row">
      <MediaAssetCard
        name={title}
        path={path}
        mediaType={mediaType}
        thumbUrl={thumb}
        videoUrl={videoUrl}
        showVideoThumb={!thumb && Boolean(videoUrl)}
        badge={item.status || "done"}
        badgeClassName="media-asset-card__badge--history"
        detail={detailParts.join(" · ") || undefined}
        showPath={Boolean(item.primary_video_relpath || item.primary_image_relpath)}
        className="discovery-list-asset-card"
        onClick={
          workbenchRel
            ? () => {
                window.location.assign(discoveryLibraryHref(workbenchRel));
              }
            : undefined
        }
      />
      <div className="queue-monitor-row__actions">
        <code className="mono queue-monitor-row__pid" title={item.prompt_id}>
          {shortId(item.prompt_id)}
        </code>
        {workbenchRel ? (
          <a className="queue-monitor-row__open" href={discoveryLibraryHref(workbenchRel)} title="Open in workbench">
            Workbench
          </a>
        ) : null}
      </div>
    </div>
  );
}

function QueueSection({
  title,
  count,
  empty,
  children,
}: {
  title: string;
  count: number;
  empty: string;
  children: React.ReactNode;
}) {
  return (
    <section className="queue-monitor-section panel">
      <div className="queue-monitor-section__head">
        <h2 className="queue-monitor-section__title">{title}</h2>
        <span className="queue-monitor-section__count mono">{count}</span>
      </div>
      <div className="queue-monitor-section__list">
        {count ? children : <div className="queue-monitor-empty">{empty}</div>}
      </div>
    </section>
  );
}

export function ComfyQueueMonitorApp() {
  const [data, setData] = useState<QueueResponse | null>(null);
  const [history, setHistory] = useState<ComfyHistoryItem[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const refresh = async () => {
    setLoading(true);
    setError("");
    try {
      const [q, h] = await Promise.all([fetchQueue(), fetchComfyHistory(30)]);
      setData(q);
      setHistory(h.items ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
    const t = window.setInterval(() => {
      void refresh();
    }, 5000);
    return () => window.clearInterval(t);
  }, []);

  const running = data?.comfyui?.running ?? [];
  const pending = data?.comfyui?.pending ?? [];
  const subtitle = useMemo(
    () => `running ${running.length} · pending ${pending.length} · history ${history.length}`,
    [running.length, pending.length, history.length],
  );

  return (
    <div className="queue-monitor layout">
      <PageHeader
        title="Comfy Queue"
        subtitle={subtitle}
        actions={
          <>
            <button type="button" onClick={() => void refresh()} disabled={loading}>
              Refresh
            </button>
            <button
              type="button"
              onClick={() => {
                void (async () => {
                  await comfyClear();
                  await refresh();
                })();
              }}
            >
              Clear pending
            </button>
          </>
        }
      />
      {error ? <div className="queue-monitor-error">{error}</div> : null}

      <div className="queue-monitor-grid">
        <QueueSection title="Running" count={running.length} empty="(idle)">
          {running.map((item, i) => (
            <QueueItemRow
              key={`${item.prompt_id ?? "run"}:${i}`}
              item={item}
              kind="running"
              onRefresh={() => void refresh()}
            />
          ))}
        </QueueSection>

        <QueueSection title="Pending" count={pending.length} empty="(none)">
          {pending.map((item, i) => (
            <QueueItemRow
              key={`${item.prompt_id ?? "pend"}:${i}`}
              item={item}
              kind="pending"
              onRefresh={() => void refresh()}
            />
          ))}
        </QueueSection>

        <QueueSection title="History" count={history.length} empty="(no history)">
          {history.map((h) => (
            <HistoryItemRow key={h.prompt_id} item={h} />
          ))}
        </QueueSection>
      </div>
    </div>
  );
}
