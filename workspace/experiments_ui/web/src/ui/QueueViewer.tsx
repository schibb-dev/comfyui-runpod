import React, { useMemo, useState } from "react";
import type { QueueComfyItem, QueueResponse, RunsItem } from "./types";
import { comfyCancel, comfyClear, requeueRun } from "./api";

function safeStr(x: unknown): string {
  if (typeof x === "string") return x;
  if (x == null) return "";
  return String(x);
}

function phaseFromStatusLive(r: RunsItem): string {
  const sl = r.status_live;
  if (!sl || typeof sl !== "object") return "";
  const p = (sl as Record<string, unknown>)["phase"];
  return typeof p === "string" ? p : "";
}

type QueueViewerProps = {
  data: QueueResponse | null;
  loading: boolean;
  error: string;
  onRefresh: () => void;
};

function SectionHeader({
  title,
  right,
}: {
  title: React.ReactNode;
  right?: React.ReactNode;
}) {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, marginBottom: 10 }}>
      <h2 className="title" style={{ margin: 0 }}>
        {title}
      </h2>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>{right}</div>
    </div>
  );
}

function ComfyRow({ item, kind, onDidAction }: { item: QueueComfyItem; kind: "pending" | "running"; onDidAction: () => void }) {
  const pid = item.prompt_id ?? "";
  const canCancel = Boolean(pid && pid.trim());
  const title = item.external ? "external" : `${item.exp_id ?? ""}/${item.run_id ?? ""}`;
  return (
    <div className="axis-item" style={{ justifyContent: "space-between" }}>
      <div style={{ display: "flex", gap: 10, alignItems: "center", minWidth: 0 }}>
        <code className="mono" style={{ opacity: item.external ? 0.85 : 1 }} title={title}>
          {pid || "(missing prompt_id)"}
        </code>
        <span style={{ color: "var(--muted)", fontSize: 12, whiteSpace: "nowrap" }}>{item.external ? "external" : "experiment"}</span>
        {!item.external ? (
          <span style={{ color: "var(--muted)", fontSize: 12, whiteSpace: "nowrap" }}>
            <span className="mono">{item.exp_id}</span> / <span className="mono">{item.run_id}</span>
          </span>
        ) : null}
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <button
          type="button"
          disabled={!canCancel}
          title={kind === "running" ? "Interrupt current ComfyUI execution" : "Remove from ComfyUI pending queue"}
          onClick={() => {
            void (async () => {
              if (!pid) return;
              await comfyCancel({ prompt_id: pid, kind });
              onDidAction();
            })();
          }}
        >
          {kind === "running" ? "Interrupt" : "Cancel"}
        </button>
      </div>
    </div>
  );
}

export function QueueViewer({ data, loading, error, onRefresh }: QueueViewerProps) {
  const [expFilter, setExpFilter] = useState<string>("");

  const expRuns = useMemo(() => {
    const xs = (data?.experiments ?? []) as RunsItem[];
    const f = expFilter.trim().toLowerCase();
    if (!f) return xs;
    return xs.filter((r) => (r.exp_id ?? "").toLowerCase().includes(f));
  }, [data, expFilter]);

  const comfyRunning = data?.comfyui?.running ?? [];
  const comfyPending = data?.comfyui?.pending ?? [];

  const expCounts = useMemo(() => {
    let queued = 0;
    let running = 0;
    let submitted = 0;
    let notQueued = 0;
    for (const r of expRuns) {
      const phase = phaseFromStatusLive(r);
      if (phase === "queued") queued++;
      else if (phase === "running") running++;
      else if (phase === "submitted") submitted++;
      else if (r.status === "submitted") submitted++;
      else if (r.status === "not_submitted") notQueued++;
    }
    return { queued, running, submitted, notQueued, total: expRuns.length };
  }, [expRuns]);

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <SectionHeader
        title="Queue"
        right={
          <>
            <button type="button" onClick={onRefresh} disabled={loading} title="Refresh queue">
              Refresh
            </button>
            <span style={{ color: "var(--muted)", fontSize: 12 }}>
              Experiments: <span className="mono">{expRuns.length}</span> · ComfyUI:{" "}
              <span className="mono">
                {comfyRunning.length} running / {comfyPending.length} pending
              </span>
            </span>
          </>
        }
      />

      {error ? <div style={{ color: "var(--bad)", fontFamily: "var(--mono)", fontSize: 12 }}>{error}</div> : null}

      <div className="panel" style={{ padding: 12 }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "flex-end", flexWrap: "wrap" }}>
          <div>
            <div style={{ color: "var(--muted)", fontSize: 12, marginBottom: 6 }}>ExperimentRuns feed</div>
            <div style={{ color: "var(--muted)", fontSize: 12 }}>
              total <span className="mono">{expCounts.total}</span> · queued <span className="mono">{expCounts.queued}</span> · running{" "}
              <span className="mono">{expCounts.running}</span> · submitted <span className="mono">{expCounts.submitted}</span> · not_queued{" "}
              <span className="mono">{expCounts.notQueued}</span>
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <label style={{ color: "var(--muted)", fontSize: 12 }}>Filter exp_id</label>
            <input value={expFilter} onChange={(e) => setExpFilter(e.target.value)} placeholder="FB8VA5L…" style={{ minWidth: 220 }} />
          </div>
        </div>

        <div style={{ height: 10 }} />

        <div className="axis-list" style={{ maxHeight: "45vh", overflow: "auto" }}>
          {expRuns.length ? (
            expRuns.map((r) => {
              const phase = phaseFromStatusLive(r);
              const pid = safeStr(r.prompt_id ?? "");
              return (
                <div className="axis-item" key={`${r.exp_id}::${r.run_id}`} style={{ justifyContent: "space-between" }}>
                  <div style={{ display: "flex", gap: 10, alignItems: "center", minWidth: 0 }}>
                    <code className="mono" title={`${r.exp_id}/${r.run_id}`}>
                      {r.exp_id}/{r.run_id}
                    </code>
                    <span className={`status ${r.status}`}>{r.status}</span>
                    {phase ? (
                      <span style={{ color: "var(--muted)", fontSize: 12, whiteSpace: "nowrap" }}>
                        phase <span className="mono">{phase}</span>
                      </span>
                    ) : null}
                    {pid ? (
                      <span style={{ color: "var(--muted)", fontSize: 12, whiteSpace: "nowrap" }}>
                        pid <span className="mono">{pid}</span>
                      </span>
                    ) : null}
                  </div>
                  <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                    <button
                      type="button"
                      onClick={() => {
                        void (async () => {
                          await requeueRun({ exp_id: r.exp_id, run_id: r.run_id, front: false });
                          onRefresh();
                        })();
                      }}
                      title="Resubmit this run to ComfyUI"
                    >
                      Requeue
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        void (async () => {
                          await requeueRun({ exp_id: r.exp_id, run_id: r.run_id, front: true });
                          onRefresh();
                        })();
                      }}
                      title="Resubmit to the front of ComfyUI pending queue"
                    >
                      Prioritize
                    </button>
                  </div>
                </div>
              );
            })
          ) : (
            <div style={{ color: "var(--muted)", fontSize: 12, fontFamily: "var(--mono)" }}>(no experiment runs to show)</div>
          )}
        </div>
      </div>

      <div className="panel" style={{ padding: 12 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", flexWrap: "wrap", gap: 12 }}>
          <div>
            <div style={{ color: "var(--muted)", fontSize: 12, marginBottom: 6 }}>ComfyUI queue</div>
            <div style={{ color: "var(--muted)", fontSize: 12 }}>
              running <span className="mono">{comfyRunning.length}</span> · pending <span className="mono">{comfyPending.length}</span>
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <button
              type="button"
              onClick={() => {
                void (async () => {
                  await comfyClear();
                  onRefresh();
                })();
              }}
              title="Clear ComfyUI pending queue"
            >
              Clear pending
            </button>
          </div>
        </div>

        <div style={{ height: 10 }} />

        <div style={{ display: "grid", gap: 10 }}>
          <div>
            <div style={{ color: "var(--muted)", fontSize: 12, marginBottom: 6 }}>Running</div>
            <div className="axis-list">
              {comfyRunning.length ? (
                comfyRunning.map((it, idx) => <ComfyRow key={`${it.prompt_id ?? "run"}:${idx}`} item={it} kind="running" onDidAction={onRefresh} />)
              ) : (
                <div style={{ color: "var(--muted)", fontSize: 12, fontFamily: "var(--mono)" }}>(none)</div>
              )}
            </div>
          </div>

          <div>
            <div style={{ color: "var(--muted)", fontSize: 12, marginBottom: 6 }}>Pending</div>
            <div className="axis-list">
              {comfyPending.length ? (
                comfyPending.map((it, idx) => <ComfyRow key={`${it.prompt_id ?? "pend"}:${idx}`} item={it} kind="pending" onDidAction={onRefresh} />)
              ) : (
                <div style={{ color: "var(--muted)", fontSize: 12, fontFamily: "var(--mono)" }}>(none)</div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

