import React, { useEffect, useMemo, useState } from "react";
import { comfyCancel, comfyClear, fetchComfyHistory, fetchQueue, saveQueueItemForLater } from "./api";
import type { ComfyHistoryItem, QueueComfyItem, QueueResponse } from "./types";

function mediaUrl(item: QueueComfyItem): string | null {
  return typeof item.input_media_url === "string" && item.input_media_url ? item.input_media_url : null;
}

function ItemCard({ item, kind, onRefresh }: { item: QueueComfyItem; kind: "running" | "pending"; onRefresh: () => void }) {
  const pid = item.prompt_id ?? "";
  const inputUrl = mediaUrl(item);
  const isVideo = item.input_media_kind === "video";
  return (
    <div className="panel" style={{ padding: 10, display: "grid", gap: 8 }}>
      {inputUrl ? (
        isVideo ? (
          <video src={inputUrl} controls preload="metadata" style={{ width: "100%", maxHeight: 240, borderRadius: 8 }} />
        ) : (
          <img src={inputUrl} alt="input" style={{ width: "100%", maxHeight: 240, objectFit: "cover", borderRadius: 8 }} />
        )
      ) : (
        <div style={{ color: "var(--muted)", fontSize: 12 }}>(no input thumbnail)</div>
      )}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center" }}>
        <code className="mono">{pid || "(no prompt_id)"}</code>
        <span style={{ color: "var(--muted)", fontSize: 12 }}>{item.workflow_name || "workflow unknown"}</span>
      </div>
      <div style={{ color: "var(--muted)", fontSize: 12, overflowX: "auto" }}>
        key params: <code className="mono">{JSON.stringify(item.key_params ?? {})}</code>
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <button
          type="button"
          disabled={!pid}
          onClick={() => {
            void (async () => {
              await comfyCancel({ prompt_id: pid, kind });
              onRefresh();
            })();
          }}
        >
          {kind === "running" ? "Interrupt running" : "Cancel pending"}
        </button>
        <button
          type="button"
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
          Save for later
        </button>
      </div>
    </div>
  );
}

function HistoryCard({ item }: { item: ComfyHistoryItem }) {
  const video = item.primary_video_url || null;
  const image = item.primary_image_url || null;
  return (
    <div className="panel" style={{ padding: 10, display: "grid", gap: 8 }}>
      <code className="mono">{item.prompt_id}</code>
      <div style={{ color: "var(--muted)", fontSize: 12 }}>status: {item.status}</div>
      {video ? (
        <video src={video} controls preload="metadata" style={{ width: "100%", maxHeight: 220, borderRadius: 8 }} />
      ) : image ? (
        <img src={image} alt="history output" style={{ width: "100%", maxHeight: 220, objectFit: "cover", borderRadius: 8 }} />
      ) : (
        <div style={{ color: "var(--muted)", fontSize: 12 }}>(no media)</div>
      )}
    </div>
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
  const current = useMemo(() => (running.length ? running[0] : null), [running]);

  return (
    <div className="layout" style={{ display: "grid", gap: 12 }}>
      <div className="panel" style={{ padding: 12, display: "grid", gap: 10 }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
          <h1 className="title" style={{ margin: 0 }}>
            Comfy Queue Monitor
          </h1>
          <div style={{ display: "flex", gap: 8 }}>
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
          </div>
        </div>
        <div style={{ color: "var(--muted)", fontSize: 12 }}>
          running <span className="mono">{running.length}</span> · pending <span className="mono">{pending.length}</span>
        </div>
        {error ? <div style={{ color: "var(--bad)", fontSize: 12 }}>{error}</div> : null}
      </div>

      <div className="panel" style={{ padding: 12, display: "grid", gap: 10 }}>
        <h2 className="title" style={{ margin: 0 }}>
          Current state
        </h2>
        {current ? <ItemCard item={current} kind="running" onRefresh={() => void refresh()} /> : <div style={{ color: "var(--muted)" }}>(idle)</div>}
      </div>

      <div style={{ display: "grid", gap: 10 }}>
        <h2 className="title" style={{ margin: 0 }}>
          Pending queue
        </h2>
        {pending.length ? pending.map((item, i) => <ItemCard key={`${item.prompt_id ?? "pending"}:${i}`} item={item} kind="pending" onRefresh={() => void refresh()} />) : <div style={{ color: "var(--muted)" }}>(none)</div>}
      </div>

      <div style={{ display: "grid", gap: 10 }}>
        <h2 className="title" style={{ margin: 0 }}>
          History
        </h2>
        {history.length ? history.map((h) => <HistoryCard key={h.prompt_id} item={h} />) : <div style={{ color: "var(--muted)" }}>(no history)</div>}
      </div>
    </div>
  );
}

