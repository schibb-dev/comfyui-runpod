import type {
  ShapeFactoryMapFamily,
  ShapeFactoryMapJob,
  ShapeFactoryMapMediaRef,
  ShapeFactoryMapMember,
  ShapeFactoryMapProjectedPair,
} from "./types";

export type PairPhase = "job" | "future" | "seed";

/** Operator-facing origin for Source→Output chips. */
export type PairJobKind =
  | "hourly"
  | "ui"
  | "pipeline"
  | "factory"
  | "replay"
  | "derive"
  | "extend"
  | "possible"
  | "seed"
  | "orphaned";

export type SourceOutputPairGap = "none" | "source" | "output";

export type SourceOutputPair = {
  pairKey: string;
  jobKey?: string;
  job?: ShapeFactoryMapJob;
  source?: ShapeFactoryMapMediaRef;
  output?: ShapeFactoryMapMember;
  outputMemberKey?: string;
  gap: SourceOutputPairGap;
  gapNote?: string;
  phase?: PairPhase;
  jobKind?: PairJobKind | string;
  comboKey?: string;
  bindings?: Record<string, ShapeFactoryMapMediaRef>;
};

export function classifyPairJobKind(
  jobKey?: string | null,
  job?: ShapeFactoryMapJob | null,
  phase?: PairPhase,
): PairJobKind | string {
  if (phase === "future") return "possible";
  if (phase === "seed") return "seed";
  if (job?.job_kind) return job.job_kind;
  const key = String(jobKey || job?.job_key || "");
  if (key.startsWith("hourly__")) return "hourly";
  const lower = key.toLowerCase();
  if (lower.includes("adhoc_ui") || lower.includes("__ui") || /_ui\d/.test(lower)) return "ui";
  const pick = String(job?.pick_mode || "").toLowerCase();
  if (pick === "replay" || pick === "derive" || pick === "extend") return pick;
  if (jobKey && !job) return "orphaned";
  return "factory";
}

export function mediaIsPresent(media?: ShapeFactoryMapMediaRef | null): boolean {
  return Boolean(media?.url || media?.thumb_url || media?.path);
}

/** Primary input media for a pair (video or still image slot). */
export function primarySourceBinding(
  bindings?: Record<string, ShapeFactoryMapMediaRef> | null,
  pairSource?: ShapeFactoryMapMediaRef | null,
): ShapeFactoryMapMediaRef | undefined {
  if (pairSource && mediaIsPresent(pairSource)) return pairSource;
  if (!bindings) return undefined;
  for (const slot of ["source_video", "source_video_ref", "source_still"]) {
    const b = bindings[slot];
    if (mediaIsPresent(b)) return b;
  }
  for (const b of Object.values(bindings)) {
    if (!mediaIsPresent(b)) continue;
    const hint = (b.relpath || b.path || b.basename || "").toLowerCase();
    if (/\.(mp4|webm|mov|png|jpe?g|webp)$/i.test(hint)) return b;
  }
  return undefined;
}

export function poolMemberKey(poolKey: string, mem: ShapeFactoryMapMediaRef): string {
  return `${poolKey}:${mem.path || mem.basename}`;
}

/** Prompt profile stems that are pool placeholders — omit from chip labels. */
const NON_DATA_PROMPT_LABELS = new Set([
  "catalog-default",
  "pp-catalog-default",
  "default",
  "catalog_default",
]);

function stemFromBasename(name?: string | null): string {
  return String(name || "")
    .replace(/\.(mp4|webm|mov|png|jpe?g|webp|json)$/i, "")
    .trim();
}

/** Compact operator-facing media id (prefer human stem, else short hash tail). */
export function shortMediaLabel(name?: string | null, maxLen = 28): string {
  const stem = stemFromBasename(name);
  if (!stem) return "";
  // Content-addressed stills: SSS/<hash>… — show short prefix + tail
  const hashish = stem.match(/^(?:SSS|XXX|YYY|FAVBBBBB|qqqpp-)?([0-9a-f]{16,})$/i);
  if (hashish) {
    const h = hashish[1];
    return `${h.slice(0, 6)}…${h.slice(-4)}`;
  }
  if (stem.length <= maxLen) return stem;
  // Keep a readable head and a short tail (dates / sequence often at end)
  const tail = stem.split(/[_-]/).filter(Boolean).slice(-2).join("_");
  if (tail && tail.length < maxLen - 1) {
    const headBudget = maxLen - tail.length - 1;
    return `${stem.slice(0, Math.max(6, headBudget))}…${tail}`;
  }
  return `${stem.slice(0, maxLen - 1)}…`;
}

function promptLabelIsUseful(raw?: string | null): boolean {
  const stem = stemFromBasename(raw).toLowerCase();
  if (!stem) return false;
  if (NON_DATA_PROMPT_LABELS.has(stem)) return false;
  if (stem.startsWith("pp-") && NON_DATA_PROMPT_LABELS.has(stem.slice(3))) return false;
  return true;
}

function pairSourceStem(pair: SourceOutputPair): string {
  return (
    shortMediaLabel(pair.source?.basename) ||
    shortMediaLabel(pair.bindings?.source_still?.basename) ||
    shortMediaLabel(pair.bindings?.source_video?.basename) ||
    shortMediaLabel(pair.bindings?.source_video_ref?.basename) ||
    shortMediaLabel(pair.bindings?.identity_anchor?.basename) ||
    ""
  );
}

/**
 * Chip caption: prefer source (or output) identity. Do not lead with
 * catalog-default / other non-data prompt placeholders.
 */
export function shortPairLabel(pair: SourceOutputPair): string {
  const src = pairSourceStem(pair);
  const out = shortMediaLabel(pair.output?.basename);

  const promptFromBinding = stemFromBasename(pair.bindings?.prompt_profile?.basename);
  const jk = pair.jobKey || "";
  const promptFromKey = jk.match(/(?:pp|prompt_profile)-(.+?)__(?:src|source_video|still|id)/)?.[1] || "";
  const prompt = promptLabelIsUseful(promptFromBinding)
    ? stemFromBasename(promptFromBinding)
    : promptLabelIsUseful(promptFromKey)
      ? stemFromBasename(promptFromKey)
      : "";

  if (src && prompt) return `${src} · ${prompt}`;
  if (src) return src;
  if (out && prompt) return `${out} · ${prompt}`;
  if (out) return out;
  if (prompt) return prompt;
  if (jk) return shortMediaLabel(jk.replace(/^hourly__/, "").slice(0, 48)) || "run";
  return "run";
}

function projectedToPair(row: ShapeFactoryMapProjectedPair): SourceOutputPair {
  const bindings = row.bindings;
  return {
    pairKey: row.pair_key || `future:${row.combo_key || "?"}`,
    comboKey: row.combo_key,
    phase: "future",
    jobKind: "possible",
    source: row.source || primarySourceBinding(bindings),
    bindings,
    gap: row.gap || "output",
    gapNote: row.gap_note || "not run",
  };
}

export function buildSourceOutputPairs(
  family: ShapeFactoryMapFamily,
  jobs: ShapeFactoryMapJob[],
): SourceOutputPair[] {
  const depositsByJob = new Map<string, { member: ShapeFactoryMapMember; memberKey: string }>();
  const orphanOutputs: SourceOutputPair[] = [];

  // Outputs already represented by a job (e.g. backfilled seed outputs) so we
  // don't also emit them as sourceless seed orphans.
  const jobOutputRefs = new Set<string>();
  for (const job of jobs) {
    const out = (job.outputs?.[0] as ShapeFactoryMapMediaRef | undefined) || undefined;
    if (out?.relpath) jobOutputRefs.add(out.relpath);
    if (out?.basename) jobOutputRefs.add(out.basename);
  }
  const coveredByJob = (mem: ShapeFactoryMapMember): boolean =>
    Boolean((mem.relpath && jobOutputRefs.has(mem.relpath)) || (mem.basename && jobOutputRefs.has(mem.basename)));

  for (const pool of family.deposit_pools || []) {
    const poolKey = pool.pool_id || "deposit";
    for (const mem of pool.members_preview || []) {
      const memberKey = poolMemberKey(poolKey, mem);
      if (mem.job_key) {
        depositsByJob.set(mem.job_key, { member: mem, memberKey });
      } else if (coveredByJob(mem)) {
        continue;
      } else {
        const recovered = mem.source_still;
        const hasRecovered = mediaIsPresent(recovered);
        orphanOutputs.push({
          pairKey: `seed:${memberKey}`,
          output: mem,
          outputMemberKey: memberKey,
          source: hasRecovered ? recovered : undefined,
          gap: hasRecovered ? "none" : "source",
          gapNote: hasRecovered ? "recovered source" : mem.source === "seed" ? "seed" : "no source",
          phase: "seed",
          jobKind: "seed",
        });
      }
    }
  }

  const pairs: SourceOutputPair[] = [];
  const seenDepositJobs = new Set<string>();

  for (const job of jobs) {
    const jobKey = job.job_key;
    if (!jobKey) continue;
    const source = primarySourceBinding(job.bindings, undefined);
    const deposit = depositsByJob.get(jobKey);
    if (deposit) seenDepositJobs.add(jobKey);
    const output = deposit?.member || (job.outputs?.[0] as ShapeFactoryMapMember | undefined);

    let gap: SourceOutputPairGap = "none";
    let gapNote: string | undefined;
    const hasSource = mediaIsPresent(source);
    const hasOutput = mediaIsPresent(output);

    if (!hasSource && !hasOutput) {
      gap = "source";
      gapNote = "missing media";
    } else if (!hasSource) {
      gap = "source";
      gapNote = "no source";
    } else if (!hasOutput) {
      gap = "output";
      const st = (job.status || "pending").toLowerCase();
      gapNote = st === "pending" ? "pending submit" : st === "queued" ? "queued" : st === "running" ? "running" : "no output";
    }

    pairs.push({
      pairKey: jobKey,
      jobKey,
      job,
      source,
      output: deposit?.member ?? output,
      outputMemberKey: deposit?.memberKey,
      gap,
      gapNote,
      phase: "job",
      jobKind: classifyPairJobKind(jobKey, job, "job"),
    });
  }

  for (const [jobKey, deposit] of depositsByJob) {
    if (seenDepositJobs.has(jobKey)) continue;
    const recovered = deposit.member.source_still;
    const hasRecovered = mediaIsPresent(recovered);
    pairs.push({
      pairKey: jobKey,
      jobKey,
      output: deposit.member,
      outputMemberKey: deposit.memberKey,
      source: hasRecovered ? recovered : undefined,
      gap: hasRecovered ? "none" : "source",
      gapNote: hasRecovered ? "recovered source" : "job file missing",
      phase: "job",
      jobKind: classifyPairJobKind(jobKey, null, "job"),
    });
  }

  const futures = (family.projected_pairs || []).map(projectedToPair);
  return [...pairs, ...orphanOutputs, ...futures];
}

export function summarizePairGaps(pairs: SourceOutputPair[]): string {
  const missingSource = pairs.filter((p) => p.gap === "source" && p.phase !== "future").length;
  const missingOutput = pairs.filter((p) => p.gap === "output" && p.phase === "job").length;
  const future = pairs.filter((p) => p.phase === "future").length;
  const parts: string[] = [];
  if (missingOutput) parts.push(`${missingOutput} awaiting output`);
  if (future) parts.push(`${future} possible`);
  if (missingSource) parts.push(`${missingSource} without source`);
  return parts.join(" · ");
}

export function countPairPhases(pairs: SourceOutputPair[]): { runs: number; future: number } {
  const runs = pairs.filter((p) => p.phase === "job" || p.phase === "seed" || !p.phase).length;
  const future = pairs.filter((p) => p.phase === "future").length;
  return { runs, future };
}
