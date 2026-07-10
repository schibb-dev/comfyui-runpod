import React, { useMemo } from "react";
import { formatInteger } from "./locale";
import type { DiscoveryWorkflowFacetsResponse } from "./types";

function asRecord(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function Chip({ children, tone }: { children: React.ReactNode; tone?: "muted" | "good" | "warn" }) {
  const cls = `dwf-chip${tone === "good" ? " dwf-chip--good" : ""}${tone === "warn" ? " dwf-chip--warn" : ""}`;
  return <span className={cls}>{children}</span>;
}

export type DiscoveryWorkflowFacetsPanelProps = {
  /** Row to probe (primary `relpath` sent to the API). */
  relpath: string;
  data: DiscoveryWorkflowFacetsResponse | null;
  probedRelpath?: string | null;
  loading: boolean;
  error: string;
  onLoad: () => void;
  loadDisabled?: boolean;
  /** Extra one-line context above the button. */
  intro?: React.ReactNode;
};

export function DiscoveryWorkflowFacetsPanel({
  relpath,
  data,
  probedRelpath,
  loading,
  error,
  onLoad,
  loadDisabled,
  intro,
}: DiscoveryWorkflowFacetsPanelProps) {
  const item = useMemo(() => asRecord(data?.item), [data]);
  const mp4 = useMemo(() => asRecord(data?.mp4), [data]);
  const ffprobe = useMemo(() => asRecord(mp4?.ffprobe), [mp4]);
  const provenance = useMemo(() => asRecord(data?.provenance), [data]);
  const indexed = useMemo(() => asRecord(provenance?.indexed), [provenance]);
  const pngProbes = useMemo(() => (Array.isArray(data?.png_workflow_probes) ? data!.png_workflow_probes! : []), [data]);

  const ok = data?.ok === true;

  return (
    <div className="dwf-panel">
      {intro ? <div className="dwf-intro">{intro}</div> : null}
      <div className="dwf-toolbar">
        <button type="button" className="dwf-load-btn" disabled={Boolean(loadDisabled) || loading || !relpath} onClick={onLoad}>
          {loading ? "Loading…" : data ? "Refresh probe" : "Load workflow probe"}
        </button>
        {probedRelpath ? <span className="dwf-muted">Queried: {probedRelpath}</span> : null}
      </div>
      {error ? <div className="factory-error dwf-error">{error}</div> : null}

      {data && !ok ? (
        <div className="dwf-banner dwf-banner--bad">
          <strong>{data.error || "Probe failed"}</strong>
          {data.detail ? <div className="dwf-muted">{data.detail}</div> : null}
        </div>
      ) : null}

      {ok && data ? (
        <>
          <section className="dwf-section">
            <h3 className="dwf-h3">Summary</h3>
            <div className="dwf-kv">
              <div className="dwf-k">Library</div>
              <div className="dwf-v">{str(item?.library) || "—"}</div>
              <div className="dwf-k">Group</div>
              <div className="dwf-v mono">{str(item?.group_id) || "—"}</div>
              <div className="dwf-k">Video</div>
              <div className="dwf-v mono dwf-break">{str(item?.video_relpath) || "—"}</div>
              <div className="dwf-k">Thumb / PNG</div>
              <div className="dwf-v mono dwf-break">{str(item?.thumb_relpath) || "—"}</div>
              <div className="dwf-k">Index</div>
              <div className="dwf-v mono dwf-break">{data.discovery_index_path || "—"}</div>
            </div>
            <div className="dwf-chip-row">
              {indexed?.has_embedded_prompt ? <Chip tone="good">embedded prompt</Chip> : <Chip>no embedded prompt flag</Chip>}
              {indexed?.workflow_fingerprint_exact ? (
                <Chip>
                  exact fp <span className="mono">{String(indexed.workflow_fingerprint_exact).slice(0, 14)}…</span>
                </Chip>
              ) : (
                <Chip tone="warn">no exact fingerprint</Chip>
              )}
              {typeof data.workflow_ratings?.rating_inferred === "number" ? (
                <Chip tone="good">
                  workflow ◇{String(data.workflow_ratings.rating_inferred)}
                  {data.workflow_ratings.rating_evidence?.n != null ? ` (n=${String(data.workflow_ratings.rating_evidence.n)})` : ""}
                </Chip>
              ) : null}
              {typeof (data.item as { rating_explicit?: number } | undefined)?.rating_explicit === "number" ? (
                <Chip tone="good">output ★{String((data.item as { rating_explicit?: number }).rating_explicit)}</Chip>
              ) : null}
            </div>
          </section>

          <section className="dwf-section">
            <h3 className="dwf-h3">MP4 container</h3>
            {!mp4?.ok ? (
              <div className="dwf-muted">{str(mp4?.error) || "No MP4 probe."}</div>
            ) : (
              <div className="dwf-kv">
                <div className="dwf-k">Path</div>
                <div className="dwf-v mono dwf-break">{str(mp4.relpath)}</div>
                <div className="dwf-k">Size</div>
                <div className="dwf-v">{mp4.size_bytes != null ? `${formatInteger(Number(mp4.size_bytes))} bytes` : "—"}</div>
                <div className="dwf-k">ffprobe tags</div>
                <div className="dwf-v">
                  {ffprobe?.ok ? (
                    <>
                      {Array.isArray(ffprobe.tag_keys) ? `${ffprobe.tag_keys.length} keys` : "—"}
                      {str(ffprobe.extracted_prompt_shape) !== "none_or_unknown" ? (
                        <Chip tone="good">tags→{str(ffprobe.extracted_prompt_shape)}</Chip>
                      ) : null}
                      {str(ffprobe.extracted_workflow_shape) !== "none_or_unknown" ? (
                        <Chip tone="good">tags→{str(ffprobe.extracted_workflow_shape)}</Chip>
                      ) : null}
                    </>
                  ) : (
                    <span className="dwf-muted">{str(ffprobe?.error) || "ffprobe unavailable"}</span>
                  )}
                </div>
              </div>
            )}
          </section>

          <section className="dwf-section">
            <h3 className="dwf-h3">PNG workflow chunks</h3>
            {!pngProbes.length ? <div className="dwf-muted">No PNG members probed.</div> : null}
            {pngProbes.map((raw, idx) => {
              const p = asRecord(raw);
              if (!p) return null;
              const keys = Array.isArray(p.png_text_chunk_keys) ? (p.png_text_chunk_keys as string[]) : [];
              const pr = asRecord(p.prompt_chunk);
              const wf = asRecord(p.workflow_chunk);
              const facets = asRecord(p.facets);
              const api = asRecord(facets?.api_prompt);
              const graph = asRecord(api?.graph);
              const sources = asRecord(api?.sources);
              const loras = asRecord(api?.loras);
              const lg = asRecord(facets?.litegraph_workflow);
              return (
                <div key={`${str(p.relpath)}-${idx}`} className="dwf-png-card">
                  <div className="dwf-png-card__head">
                    <span className="mono dwf-break">{str(p.relpath)}</span>
                    {p.ok ? <Chip tone="good">read OK</Chip> : <Chip tone="warn">{str(p.error) || "failed"}</Chip>}
                  </div>
                  {keys.length ? (
                    <div className="dwf-chip-row">
                      {keys.map((k) => (
                        <Chip key={k}>
                          {k}
                        </Chip>
                      ))}
                    </div>
                  ) : null}
                  <div className="dwf-subgrid">
                    <div>
                      <div className="dwf-sublabel">prompt chunk</div>
                      <div className="dwf-muted">
                        {pr ? `${pr.present ? "present" : "absent"} · ${String(pr.raw_chars || 0)} chars · parsed ${pr.parsed_ok ? "yes" : "no"} · ${str(pr.shape)}` : "—"}
                      </div>
                    </div>
                    <div>
                      <div className="dwf-sublabel">workflow chunk</div>
                      <div className="dwf-muted">
                        {wf ? `${wf.present ? "present" : "absent"} · ${String(wf.raw_chars || 0)} chars · parsed ${wf.parsed_ok ? "yes" : "no"} · ${str(wf.shape)}` : "—"}
                      </div>
                    </div>
                  </div>
                  {api && !str(api.error) ? (
                    <div className="dwf-facet-block">
                      <div className="dwf-sublabel">API prompt facets</div>
                      <div className="dwf-kv dwf-kv--tight">
                        <div className="dwf-k">graph shape</div>
                        <div className="dwf-v mono dwf-break">{str(graph?.api_graph_shape_hash) || "—"}</div>
                        <div className="dwf-k">nodes / links</div>
                        <div className="dwf-v">
                          {String(graph?.node_count ?? "—")} / {String(graph?.edge_link_count ?? "—")}
                        </div>
                        <div className="dwf-k">sources fp</div>
                        <div className="dwf-v mono dwf-break">{str(sources?.source_paths_fingerprint) || "—"}</div>
                        <div className="dwf-k">LoRA stack fp</div>
                        <div className="dwf-v mono dwf-break">{str(loras?.lora_stack_fingerprint) || "—"}</div>
                      </div>
                      {Array.isArray(sources?.source_paths_sample) && (sources!.source_paths_sample as string[]).length ? (
                        <ul className="dwf-path-list">
                          {(sources!.source_paths_sample as string[]).slice(0, 12).map((s) => (
                            <li key={s} className="mono">
                              {s}
                            </li>
                          ))}
                        </ul>
                      ) : null}
                    </div>
                  ) : str(api?.error) ? (
                    <div className="dwf-muted">API facets: {str(api?.error)}</div>
                  ) : null}
                  {lg && !str(lg.error) ? (
                    <div className="dwf-facet-block">
                      <div className="dwf-sublabel">Litegraph workflow</div>
                      <div className="dwf-kv dwf-kv--tight">
                        <div className="dwf-k">graph_hash</div>
                        <div className="dwf-v mono dwf-break">{str(lg.graph_hash) || "—"}</div>
                        <div className="dwf-k">recipe_hash</div>
                        <div className="dwf-v mono dwf-break">{str(lg.recipe_hash) || "—"}</div>
                        <div className="dwf-k">nodes / links</div>
                        <div className="dwf-v">
                          {String(lg.node_count ?? "—")} / {String(lg.link_count ?? "—")}
                        </div>
                      </div>
                    </div>
                  ) : str(lg?.error) ? (
                    <div className="dwf-muted">Litegraph: {str(lg?.error)}</div>
                  ) : null}
                </div>
              );
            })}
          </section>

          <section className="dwf-section">
            <h3 className="dwf-h3">Provenance</h3>
            <div className="dwf-muted">{str(provenance?.kind)} · index v{String(provenance?.index_version ?? "?")}</div>
            {Array.isArray(provenance?.notes) ? (
              <ul className="dwf-notes">
                {(provenance!.notes as string[]).map((n) => (
                  <li key={n}>{n}</li>
                ))}
              </ul>
            ) : null}
          </section>

          <details className="dwf-raw">
            <summary>Raw JSON</summary>
            <pre className="dwf-raw-pre">{JSON.stringify({ probed_relpath: probedRelpath ?? relpath, ...data }, null, 2)}</pre>
          </details>
        </>
      ) : null}
    </div>
  );
}
