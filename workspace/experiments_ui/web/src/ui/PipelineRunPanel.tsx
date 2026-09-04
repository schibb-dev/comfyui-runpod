import React, { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchShapeFactoryPipelineRun, runShapeFactoryPipeline } from "./api";
import { queryKeys } from "./queryKeys";
import type { ShapeFactoryMapPipeline, ShapeFactoryPipelineRunStep } from "./types";

type Props = {
  pipeline: ShapeFactoryMapPipeline;
  onRan?: () => void;
};

function stepSummary(step: ShapeFactoryPipelineRunStep): string {
  const jobs = step.jobs || [];
  if (!jobs.length) return step.error || (step.ok ? "ok" : "—");
  return jobs
    .map((j) => {
      const bits = [j.job_key, j.status, j.prompt_id ? `prompt ${j.prompt_id}` : null].filter(Boolean);
      return bits.join(" · ");
    })
    .join("; ");
}

export function PipelineRunPanel({ pipeline, onRan }: Props) {
  const pipelineId = String(pipeline.pipeline_id || "").trim();
  const pipelinePath = String(pipeline.path || "").trim();
  const queryClient = useQueryClient();
  const completedHandledRef = useRef<string | null>(null);

  const [limit, setLimit] = useState(1);
  const [wait, setWait] = useState(true);
  const [dev, setDev] = useState(false);
  const [dryRun, setDryRun] = useState(false);
  const [runId, setRunId] = useState<string | null>(null);
  const [inlineMsg, setInlineMsg] = useState<string | null>(null);

  const refreshMap = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.mapRoot });
    onRan?.();
  }, [queryClient, onRan]);

  const runMutation = useMutation({
    mutationFn: runShapeFactoryPipeline,
    onSuccess: (res) => {
      setInlineMsg(null);
      completedHandledRef.current = null;
      if (res.mode === "background" && res.run_id) {
        setRunId(res.run_id);
      } else if (res.mode === "inline") {
        setRunId(null);
        const steps = res.result?.steps || [];
        const ok = res.result?.ok !== false;
        setInlineMsg(
          ok
            ? `Pipeline finished · ${steps.length} step(s)`
            : `Pipeline failed · ${steps.find((s) => s.error)?.error || "see steps"}`,
        );
        refreshMap();
      }
    },
    onError: (e) => {
      setInlineMsg(e instanceof Error ? e.message : String(e));
    },
  });

  const statusQuery = useQuery({
    queryKey: queryKeys.shapeFactory.pipelineRun(runId || ""),
    queryFn: () => fetchShapeFactoryPipelineRun(runId || ""),
    enabled: Boolean(runId),
    refetchInterval: (q) => {
      const st = String(q.state.data?.run?.status || "");
      return st === "running" ? 3000 : false;
    },
  });

  useEffect(() => {
    const st = String(statusQuery.data?.run?.status || "");
    if (!runId || st !== "complete") return;
    if (completedHandledRef.current === runId) return;
    completedHandledRef.current = runId;
    refreshMap();
  }, [runId, statusQuery.data?.run?.status, refreshMap]);

  const busy = runMutation.isPending || String(statusQuery.data?.run?.status || "") === "running";

  const startRun = useCallback(() => {
    if (!pipelineId && !pipelinePath) return;
    setInlineMsg(null);
    runMutation.mutate({
      pipeline_id: pipelineId || undefined,
      pipeline_path: pipelinePath || undefined,
      limit,
      wait,
      dev,
      dry_run: dryRun,
    });
  }, [pipelineId, pipelinePath, limit, wait, dev, dryRun, runMutation]);

  const runDoc = statusQuery.data?.run;
  const inlineSteps = runMutation.data?.result?.steps;
  const displaySteps =
    (runDoc?.steps as ShapeFactoryPipelineRunStep[] | undefined) ||
    inlineSteps ||
    [];

  return (
    <section className="sfmap-pipeline-detail__run" aria-label="Run pipeline">
      <h3 className="sfmap-index-section__title">Run</h3>
      <p className="factory-muted sfmap-index-section__hint">
        Runs each step in order: generate → submit → wait (optional) → deposit. Step 2+ uses binds_override to pull
        from earlier deposit pools. Full chains need <strong>wait between steps</strong>.
      </p>

      <div className="sfmap-pipeline-run-form">
        <label className="sfmap-pipeline-run-form__field">
          <span>Jobs per step</span>
          <input
            type="number"
            min={1}
            max={8}
            value={limit}
            disabled={busy}
            onChange={(e) => setLimit(Math.max(1, Math.min(8, Number(e.target.value) || 1)))}
          />
        </label>
        <label className="sfmap-pipeline-run-form__check">
          <input type="checkbox" checked={wait} disabled={busy} onChange={(e) => setWait(e.target.checked)} />
          <span>Wait for each step (recommended)</span>
        </label>
        <label className="sfmap-pipeline-run-form__check">
          <input type="checkbox" checked={dev} disabled={busy} onChange={(e) => setDev(e.target.checked)} />
          <span>Dev tuning (faster)</span>
        </label>
        <label className="sfmap-pipeline-run-form__check">
          <input type="checkbox" checked={dryRun} disabled={busy} onChange={(e) => setDryRun(e.target.checked)} />
          <span>Dry run</span>
        </label>
        <button type="button" className="sfmap-queue-run-btn" disabled={busy} onClick={() => startRun()}>
          {busy ? "Running…" : "Run pipeline"}
        </button>
      </div>

      {runId ? (
        <div className="sfmap-pipeline-run-status">
          <div className="sfmap-pipeline-run-status__head">
            <span className={`sfmap-status sfmap-status--${runDoc?.status || "running"}`}>
              {runDoc?.status || "running"}
            </span>
            <span className="mono factory-muted">run {runId}</span>
          </div>
          {statusQuery.data?.log_tail ? (
            <pre className="sfmap-pipeline-detail__cmd mono sfmap-pipeline-run-log">{statusQuery.data.log_tail}</pre>
          ) : null}
        </div>
      ) : null}

      {inlineMsg ? <div className="sfmap-pipeline-run-msg">{inlineMsg}</div> : null}

      {displaySteps.length ? (
        <ul className="sfmap-pipeline-run-steps">
          {displaySteps.map((step) => (
            <li key={step.step_id || step.family_slug}>
              <strong>{step.step_id || step.family_slug}</strong>
              {step.family_slug ? <span className="factory-muted"> · {step.family_slug}</span> : null}
              <div className="mono factory-muted">{stepSummary(step)}</div>
            </li>
          ))}
        </ul>
      ) : null}

      <details className="sfmap-pipeline-run-cli">
        <summary className="factory-muted">CLI equivalent</summary>
        <pre className="sfmap-pipeline-detail__cmd mono">
          {`python3 shape_factory.py pipeline run --pipeline ${pipelinePath || `.data/pipelines/${pipelineId}.pipeline.yaml`}${wait ? " --wait" : ""}${dev ? " --dev" : ""}${dryRun ? " --dry-run" : ""} --limit ${limit}`}
        </pre>
      </details>
    </section>
  );
}
