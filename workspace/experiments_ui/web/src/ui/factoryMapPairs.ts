import type {
  ShapeFactoryMapFamily,
  ShapeFactoryMapJob,
  ShapeFactoryMapMediaRef,
  ShapeFactoryMapMember,
  ShapeFactoryMapProjectedPair,
} from "./types";

export type PairPhase = "job" | "future" | "seed";

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
  comboKey?: string;
  bindings?: Record<string, ShapeFactoryMapMediaRef>;
};

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

export function shortPairLabel(pair: SourceOutputPair): string {
  const jk = pair.jobKey || "";
  const m = jk.match(/prompt_profile-(.+?)__source_video/);
  if (m?.[1]) return m[1];
  const prompt = pair.bindings?.prompt_profile?.basename?.replace(/\.json$/i, "");
  if (prompt) {
    const src =
      pair.source?.basename?.replace(/\.(mp4|png|jpe?g|webp)$/i, "") ||
      pair.bindings?.source_still?.basename?.replace(/\.(png|jpe?g|webp)$/i, "") ||
      pair.bindings?.source_video?.basename?.replace(/\.mp4$/i, "");
    if (src) return `${prompt} · ${src.split("_").slice(-1)[0] || src}`;
    return prompt;
  }
  return (
    pair.source?.basename?.replace(/\.mp4$/i, "") ||
    pair.output?.basename?.replace(/\.mp4$/i, "") ||
    jk.slice(0, 28) ||
    "run"
  );
}

function projectedToPair(row: ShapeFactoryMapProjectedPair): SourceOutputPair {
  const bindings = row.bindings;
  return {
    pairKey: row.pair_key || `future:${row.combo_key || "?"}`,
    comboKey: row.combo_key,
    phase: "future",
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
    });
  }

  for (const [jobKey, deposit] of depositsByJob) {
    if (seenDepositJobs.has(jobKey)) continue;
    pairs.push({
      pairKey: jobKey,
      jobKey,
      output: deposit.member,
      outputMemberKey: deposit.memberKey,
      gap: "source",
      gapNote: "job not in list",
      phase: "job",
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
