import React from "react";
import type { ExperimentSummary, ExperimentsRelations } from "./types";

export type ExperimentDetailPanelProps = {
  experiment: ExperimentSummary | null;
  manifest: Record<string, unknown> | null;
  relations: ExperimentsRelations | null;
  onSelectExperiment: (expId: string) => void;
};

function normalizeBaseMp4(s: string | undefined): string {
  return (s ?? "").trim().replace(/\\/g, "/");
}

export function ExperimentDetailPanel({ experiment, manifest, relations, onSelectExperiment }: ExperimentDetailPanelProps) {
  if (!experiment) {
    return (
      <div style={{ padding: 16, color: "var(--muted)", fontSize: 14 }}>
        Select an experiment to see its details and source & related.
      </div>
    );
  }

  const baseMp4 = experiment.base_mp4 ?? (manifest?.base_mp4 as string | undefined);
  const baseKey = typeof baseMp4 === "string" && baseMp4.trim() ? normalizeBaseMp4(baseMp4) : "";
  const relatedExpIds = baseKey && relations?.by_base_mp4 ? relations.by_base_mp4[baseKey] ?? [] : [];

  return (
    <div className="experiment-detail-panel" style={{ display: "flex", flexDirection: "column", gap: 16, padding: 16, fontSize: 13 }}>
      <section>
        <h3 style={{ margin: "0 0 8px", fontSize: 14, fontWeight: 600 }}>Experiment metadata</h3>
        <dl style={{ margin: 0, display: "grid", gridTemplateColumns: "auto 1fr", gap: "4px 16px", alignItems: "baseline" }}>
          <dt style={{ color: "var(--muted)", margin: 0 }}>exp_id</dt>
          <dd style={{ margin: 0 }}>
            <code className="mono">{experiment.exp_id}</code>
          </dd>
          {experiment.created_at != null && (
            <>
              <dt style={{ color: "var(--muted)", margin: 0 }}>created_at</dt>
              <dd style={{ margin: 0 }}>{String(experiment.created_at)}</dd>
            </>
          )}
          {experiment.fixed_seed != null && (
            <>
              <dt style={{ color: "var(--muted)", margin: 0 }}>fixed_seed</dt>
              <dd style={{ margin: 0 }}>{experiment.fixed_seed}</dd>
            </>
          )}
          {experiment.fixed_duration_sec != null && (
            <>
              <dt style={{ color: "var(--muted)", margin: 0 }}>fixed_duration_sec</dt>
              <dd style={{ margin: 0 }}>{experiment.fixed_duration_sec}</dd>
            </>
          )}
          {experiment.run_counts && (
            <>
              <dt style={{ color: "var(--muted)", margin: 0 }}>runs</dt>
              <dd style={{ margin: 0 }}>
                total {experiment.run_counts.total}, complete {experiment.run_counts.complete}, submitted{" "}
                {experiment.run_counts.submitted}, not_submitted {experiment.run_counts.not_submitted}
              </dd>
            </>
          )}
        </dl>
        {experiment.sweep && Object.keys(experiment.sweep).length > 0 && (
          <div style={{ marginTop: 8 }}>
            <dt style={{ color: "var(--muted)", marginBottom: 4 }}>sweep</dt>
            <pre style={{ margin: 0, fontSize: 11, background: "var(--bg-2)", padding: 8, borderRadius: 4, overflow: "auto" }}>
              {JSON.stringify(experiment.sweep, null, 2)}
            </pre>
          </div>
        )}
      </section>

      <section>
        <h3 style={{ margin: "0 0 8px", fontSize: 14, fontWeight: 600 }}>Source & related</h3>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div>
            <span style={{ color: "var(--muted)" }}>Source video: </span>
            {baseMp4 ? (
              <code className="mono" style={{ wordBreak: "break-all" }} title={baseMp4}>
                {baseMp4.replace(/^.*[/\\]/, "")}
              </code>
            ) : (
              <span style={{ color: "var(--muted)" }}>—</span>
            )}
          </div>
          <div>
            <span style={{ color: "var(--muted)" }}>Source image: </span>
            <span style={{ color: "var(--muted)" }}>—</span>
          </div>
          <div>
            <span style={{ color: "var(--muted)" }}>Related experiments (same source):</span>
            {relatedExpIds.length === 0 ? (
              <span style={{ color: "var(--muted)", marginLeft: 4 }}>None</span>
            ) : (
              <ul style={{ margin: "4px 0 0", paddingLeft: 18, listStyle: "disc" }}>
                {relatedExpIds.map((expId) => (
                  <li key={expId}>
                    <button
                      type="button"
                      className="link-like"
                      style={{
                        fontWeight: expId === experiment.exp_id ? "bold" : undefined,
                        textDecoration: expId === experiment.exp_id ? "underline" : undefined,
                      }}
                      onClick={() => onSelectExperiment(expId)}
                    >
                      <code className="mono">{expId}</code>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
