import React, { useEffect, useMemo, useState } from "react";
import { fetchOrchestratorState, saveOrchestratorState } from "./api";
import type { OrchestratorState } from "./types";

const EMPTY_STATE: OrchestratorState = {
  projects: [],
  collections: [],
  workflows: [],
  pipelines: [],
  queues: [],
  saved_items: [],
};

export function OrchestratorApp() {
  const [state, setState] = useState<OrchestratorState>(EMPTY_STATE);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [projectName, setProjectName] = useState("");
  const [queueName, setQueueName] = useState("");

  const reload = async () => {
    setLoading(true);
    setError("");
    try {
      const s = await fetchOrchestratorState();
      setState(s);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void reload();
  }, []);

  const save = async (next: OrchestratorState) => {
    setLoading(true);
    setError("");
    try {
      const saved = await saveOrchestratorState(next);
      setState(saved);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  const summary = useMemo(
    () => ({
      projects: state.projects.length,
      collections: state.collections.length,
      workflows: state.workflows.length,
      pipelines: state.pipelines.length,
      queues: state.queues.length,
      saved: state.saved_items.length,
    }),
    [state],
  );

  return (
    <div className="layout" style={{ display: "grid", gap: 12 }}>
      <div className="panel" style={{ padding: 12, display: "grid", gap: 8 }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
          <h1 className="title" style={{ margin: 0 }}>
            Orchestrator
          </h1>
          <div style={{ display: "flex", gap: 8 }}>
            <button type="button" disabled={loading} onClick={() => void reload()}>
              Refresh
            </button>
            <a href="/comfy-queue">Open Comfy Queue Monitor</a>
          </div>
        </div>
        {error ? <div style={{ color: "var(--bad)", fontSize: 12 }}>{error}</div> : null}
        <div style={{ color: "var(--muted)", fontSize: 12 }}>
          projects <span className="mono">{summary.projects}</span> · collections <span className="mono">{summary.collections}</span> · pipelines{" "}
          <span className="mono">{summary.pipelines}</span> · queues <span className="mono">{summary.queues}</span> · saved{" "}
          <span className="mono">{summary.saved}</span>
        </div>
      </div>

      <div className="panel" style={{ padding: 12, display: "grid", gap: 8 }}>
        <h2 className="title" style={{ margin: 0 }}>
          Projects
        </h2>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <input value={projectName} onChange={(e) => setProjectName(e.target.value)} placeholder="Project name" />
          <button
            type="button"
            onClick={() => {
              const name = projectName.trim();
              if (!name) return;
              const id = `project_${Date.now()}`;
              const next: OrchestratorState = {
                ...state,
                projects: [...state.projects, { id, name, workflowIds: [], collectionIds: [], pipelineIds: [] }],
              };
              setProjectName("");
              void save(next);
            }}
          >
            Add project
          </button>
        </div>
        <div className="axis-list">
          {state.projects.map((p) => (
            <div key={p.id} className="axis-item" style={{ justifyContent: "space-between" }}>
              <div>
                <strong>{p.name}</strong>
                <div style={{ color: "var(--muted)", fontSize: 12 }}>{p.id}</div>
              </div>
            </div>
          ))}
          {!state.projects.length ? <div style={{ color: "var(--muted)" }}>(none)</div> : null}
        </div>
      </div>

      <div className="panel" style={{ padding: 12, display: "grid", gap: 8 }}>
        <h2 className="title" style={{ margin: 0 }}>
          Queues
        </h2>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <input value={queueName} onChange={(e) => setQueueName(e.target.value)} placeholder="Queue name" />
          <button
            type="button"
            onClick={() => {
              const name = queueName.trim();
              if (!name) return;
              const id = `queue_${Date.now()}`;
              const next: OrchestratorState = {
                ...state,
                queues: [...state.queues, { id, name, rules: [{ type: "immediate", config: {} }] }],
              };
              setQueueName("");
              void save(next);
            }}
          >
            Add queue
          </button>
        </div>
        <div className="axis-list">
          {state.queues.map((q) => (
            <div key={q.id} className="axis-item" style={{ justifyContent: "space-between" }}>
              <div>
                <strong>{q.name}</strong>
                <div style={{ color: "var(--muted)", fontSize: 12 }}>
                  {q.id} · rules <span className="mono">{q.rules.length}</span>
                </div>
              </div>
            </div>
          ))}
          {!state.queues.length ? <div style={{ color: "var(--muted)" }}>(none)</div> : null}
        </div>
      </div>

      <div className="panel" style={{ padding: 12 }}>
        <h2 className="title" style={{ marginTop: 0 }}>
          Saved Queue Items
        </h2>
        <div className="axis-list">
          {state.saved_items.map((s) => (
            <div key={s.id} className="axis-item" style={{ justifyContent: "space-between" }}>
              <div>
                <strong>{s.title}</strong>
                <div style={{ color: "var(--muted)", fontSize: 12 }}>
                  {s.id} · prompt <span className="mono">{s.prompt_id || "-"}</span>
                </div>
              </div>
            </div>
          ))}
          {!state.saved_items.length ? <div style={{ color: "var(--muted)" }}>(none)</div> : null}
        </div>
      </div>
    </div>
  );
}

