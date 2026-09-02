import React from "react";
import type { WorkProductParamsProfile, WorkProductLorasProfile, WorkProductPromptProfile, WorkProductPromptRow } from "./types";

export function clonePromptRows(
  rows: WorkProductPromptRow[] | undefined,
  fallbackText?: string,
): WorkProductPromptRow[] {
  if (rows && rows.length) {
    return rows.map((r) => ({
      text: String(r.text || ""),
      weight: Number.isFinite(Number(r.weight)) ? Number(r.weight) : 1,
      raw: r.raw,
    }));
  }
  const t = String(fallbackText || "").trim();
  return t ? [{ text: t, weight: 1 }] : [];
}

/** Client-side canonical encode (mirrors Python ``encode_prompt_markup``). */
export function encodePromptRowsClient(rows: WorkProductPromptRow[]): string {
  const lines: string[] = [];
  for (const row of rows) {
    const text = String(row.text || "")
      .split(/\r?\n/)
      .join(" ")
      .trim();
    if (!text) continue;
    const w = Number(row.weight);
    if (!Number.isFinite(w) || Math.abs(w - 1) < 1e-6) lines.push(text);
    else {
      const rounded = Math.round(w * 10000) / 10000;
      lines.push(`(${text}:${rounded})`);
    }
  }
  return lines.join("\n");
}

/** Lightweight decode for leaving Raw mode (line-oriented). */
export function rowsFromRawText(text: string): WorkProductPromptRow[] {
  const out: WorkProductPromptRow[] = [];
  for (const raw of String(text || "").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    const m = /^\(+(.+):([0-9]+(?:\.[0-9]+)?)\)+$/.exec(line);
    if (m) {
      out.push({ text: m[1].trim(), weight: Number(m[2]), raw: line });
    } else {
      out.push({ text: line, weight: 1, raw: line });
    }
  }
  return out;
}

export function PromptSnowflakeChip({
  prompt,
  params,
  loras,
  className,
}: {
  prompt?: WorkProductPromptProfile | null;
  params?: WorkProductParamsProfile | null;
  loras?: WorkProductLorasProfile | null;
  className?: string;
}) {
  const promptFlake = Boolean(prompt?.snowflake);
  const paramsFlake = Boolean(params?.snowflake);
  const lorasFlake = Boolean(loras?.snowflake);
  if (!promptFlake && !paramsFlake && !lorasFlake) return null;
  const seedName = prompt?.seed?.label || prompt?.seed?.basename || prompt?.basename || "template";
  const jobHash = String(prompt?.content_hash || "").slice(0, 10);
  const seedHash = String(prompt?.seed?.content_hash || "").slice(0, 10);
  const paramDiffs = Object.keys(params?.diffs || {}).filter((k) => k !== "seed");
  const title = [
    promptFlake ? `prompt edited from ${seedName}` : null,
    promptFlake && jobHash ? `job ${jobHash}` : null,
    promptFlake && seedHash ? `seed ${seedHash}` : null,
    paramsFlake
      ? paramDiffs.length
        ? `params differ: ${paramDiffs.join(", ")}`
        : "params differ from template"
      : null,
    lorasFlake ? "lora stack differs from template" : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <span
      className={`work-product-badge work-product-badge--snowflake${className ? ` ${className}` : ""}`}
      title={title || "Structurally edited from template (prompt, params, or loras)"}
    >
      snowflake
    </span>
  );
}

/** Editable Weight/Text chunk table (canonical save path). */
export function PromptChunkEditor({
  rows,
  onChange,
  disabled,
}: {
  rows: WorkProductPromptRow[];
  onChange: (next: WorkProductPromptRow[]) => void;
  disabled?: boolean;
}) {
  const update = (idx: number, patch: Partial<WorkProductPromptRow>) => {
    onChange(rows.map((r, i) => (i === idx ? { ...r, ...patch } : r)));
  };
  const move = (idx: number, dir: -1 | 1) => {
    const j = idx + dir;
    if (j < 0 || j >= rows.length) return;
    const next = rows.slice();
    const [row] = next.splice(idx, 1);
    next.splice(j, 0, row);
    onChange(next);
  };
  return (
    <div className="work-product-prompt-chunk-editor">
      <table className="work-product-prompt-table work-product-prompt-table--edit">
        <thead>
          <tr>
            <th scope="col" className="work-product-prompt-table__w">
              Weight
            </th>
            <th scope="col">Text</th>
            <th scope="col" className="work-product-prompt-table__ops">
              <span className="visually-hidden">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.length ? (
            rows.map((row, i) => (
              <tr key={`chunk-${i}`}>
                <td className="work-product-prompt-table__w">
                  <input
                    type="number"
                    className="work-product-prompt-chunk-editor__weight"
                    step={0.05}
                    min={0}
                    value={Number.isFinite(Number(row.weight)) ? Number(row.weight) : 1}
                    disabled={disabled}
                    onChange={(e) => update(i, { weight: Number(e.target.value) || 1 })}
                  />
                </td>
                <td className="work-product-prompt-table__text">
                  <textarea
                    className="work-product-prompt-chunk-editor__text"
                    rows={2}
                    value={row.text}
                    disabled={disabled}
                    onChange={(e) => update(i, { text: e.target.value })}
                  />
                </td>
                <td className="work-product-prompt-table__ops">
                  <div className="work-product-prompt-chunk-editor__ops">
                    <button type="button" className="drt-btn" disabled={disabled || i === 0} onClick={() => move(i, -1)} title="Move up">
                      ↑
                    </button>
                    <button
                      type="button"
                      className="drt-btn"
                      disabled={disabled || i >= rows.length - 1}
                      onClick={() => move(i, 1)}
                      title="Move down"
                    >
                      ↓
                    </button>
                    <button
                      type="button"
                      className="drt-btn"
                      disabled={disabled}
                      onClick={() => onChange(rows.filter((_, j) => j !== i))}
                      title="Remove row"
                    >
                      ×
                    </button>
                  </div>
                </td>
              </tr>
            ))
          ) : (
            <tr>
              <td colSpan={3} className="work-product-prompt-table__empty">
                No chunks — add a row
              </td>
            </tr>
          )}
        </tbody>
      </table>
      <button
        type="button"
        className="drt-btn work-product-prompt-chunk-editor__add"
        disabled={disabled}
        onClick={() => onChange([...rows, { text: "", weight: 1 }])}
      >
        Add row
      </button>
    </div>
  );
}

type ChunkDiffOp =
  | { kind: "equal"; job: WorkProductPromptRow; seed: WorkProductPromptRow }
  | { kind: "add"; job: WorkProductPromptRow }
  | { kind: "remove"; seed: WorkProductPromptRow }
  | { kind: "change"; job: WorkProductPromptRow; seed: WorkProductPromptRow };

function normChunkKey(row: WorkProductPromptRow): string {
  return String(row.text || "")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

function alignPromptChunks(seed: WorkProductPromptRow[], job: WorkProductPromptRow[]): ChunkDiffOp[] {
  const a = seed;
  const b = job;
  const n = a.length;
  const m = b.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () => Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      if (normChunkKey(a[i]) === normChunkKey(b[j])) dp[i][j] = dp[i + 1][j + 1] + 1;
      else dp[i][j] = Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const ops: ChunkDiffOp[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (normChunkKey(a[i]) === normChunkKey(b[j])) {
      const weightChanged = Math.abs(Number(a[i].weight) - Number(b[j].weight)) > 1e-6;
      ops.push(
        weightChanged
          ? { kind: "change", seed: a[i], job: b[j] }
          : { kind: "equal", seed: a[i], job: b[j] },
      );
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      ops.push({ kind: "remove", seed: a[i] });
      i++;
    } else {
      ops.push({ kind: "add", job: b[j] });
      j++;
    }
  }
  while (i < n) ops.push({ kind: "remove", seed: a[i++] });
  while (j < m) ops.push({ kind: "add", job: b[j++] });
  return ops;
}

function wordDiffNodes(seedText: string, jobText: string): React.ReactNode {
  const a = String(seedText || "").split(/(\s+)/).filter((t) => t.length);
  const b = String(jobText || "").split(/(\s+)/).filter((t) => t.length);
  const n = a.length;
  const m = b.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () => Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      if (a[i] === b[j]) dp[i][j] = dp[i + 1][j + 1] + 1;
      else dp[i][j] = Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const nodes: React.ReactNode[] = [];
  let i = 0;
  let j = 0;
  let k = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      nodes.push(<span key={`eq-${k++}`}>{b[j]}</span>);
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      nodes.push(
        <span key={`rm-${k++}`} className="prompt-chunk-diff__del">
          {a[i]}
        </span>,
      );
      i++;
    } else {
      nodes.push(
        <span key={`ad-${k++}`} className="prompt-chunk-diff__ins">
          {b[j]}
        </span>,
      );
      j++;
    }
  }
  while (i < n) {
    nodes.push(
      <span key={`rm-${k++}`} className="prompt-chunk-diff__del">
        {a[i++]}
      </span>,
    );
  }
  while (j < m) {
    nodes.push(
      <span key={`ad-${k++}`} className="prompt-chunk-diff__ins">
        {b[j++]}
      </span>,
    );
  }
  return nodes;
}

export function PromptChunkDiff({
  title,
  seedRows,
  jobRows,
}: {
  title: string;
  seedRows: WorkProductPromptRow[];
  jobRows: WorkProductPromptRow[];
}) {
  const ops = alignPromptChunks(seedRows, jobRows);
  if (!ops.length) {
    return (
      <div className="work-product-prompt-table-wrap">
        {title ? <div className="work-product-prompt-table__title">{title}</div> : null}
        <div className="work-product-prompt-table__empty">No differences</div>
      </div>
    );
  }
  return (
    <div className="work-product-prompt-table-wrap prompt-chunk-diff">
      {title ? <div className="work-product-prompt-table__title">{title} · diff vs seed</div> : null}
      <table className="work-product-prompt-table">
        <thead>
          <tr>
            <th scope="col" className="work-product-prompt-table__w">
              Weight
            </th>
            <th scope="col">Text</th>
          </tr>
        </thead>
        <tbody>
          {ops.map((op, idx) => {
            if (op.kind === "equal") {
              const w = Number(op.job.weight);
              return (
                <tr key={`eq-${idx}`} className="prompt-chunk-diff__row--equal">
                  <td className="work-product-prompt-table__w">
                    <span className="work-product-prompt-weight">{Number.isFinite(w) ? w : "—"}</span>
                  </td>
                  <td className="work-product-prompt-table__text">{op.job.text}</td>
                </tr>
              );
            }
            if (op.kind === "add") {
              const w = Number(op.job.weight);
              return (
                <tr key={`ad-${idx}`} className="prompt-chunk-diff__row--add">
                  <td className="work-product-prompt-table__w">
                    <span className="work-product-prompt-weight">{Number.isFinite(w) ? w : "—"}</span>
                  </td>
                  <td className="work-product-prompt-table__text">
                    <span className="prompt-chunk-diff__ins">{op.job.text}</span>
                  </td>
                </tr>
              );
            }
            if (op.kind === "remove") {
              const w = Number(op.seed.weight);
              return (
                <tr key={`rm-${idx}`} className="prompt-chunk-diff__row--remove">
                  <td className="work-product-prompt-table__w">
                    <span className="work-product-prompt-weight">{Number.isFinite(w) ? w : "—"}</span>
                  </td>
                  <td className="work-product-prompt-table__text">
                    <span className="prompt-chunk-diff__del">{op.seed.text}</span>
                  </td>
                </tr>
              );
            }
            const sw = Number(op.seed.weight);
            const jw = Number(op.job.weight);
            const weightLabel =
              Math.abs(sw - jw) > 1e-6
                ? `${Number.isFinite(sw) ? sw : "—"} → ${Number.isFinite(jw) ? jw : "—"}`
                : Number.isFinite(jw)
                  ? String(jw)
                  : "—";
            return (
              <tr key={`ch-${idx}`} className="prompt-chunk-diff__row--change">
                <td className="work-product-prompt-table__w">
                  <span className="work-product-prompt-weight">{weightLabel}</span>
                </td>
                <td className="work-product-prompt-table__text">{wordDiffNodes(op.seed.text, op.job.text)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
