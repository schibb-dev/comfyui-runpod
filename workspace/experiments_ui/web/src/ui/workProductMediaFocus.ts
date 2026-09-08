import type { WorkProductItem } from "./types";

export type MediaFocusRelation = "output" | "source";

function normPath(raw: string | null | undefined): string {
  return String(raw || "")
    .trim()
    .toLowerCase()
    .replace(/\\/g, "/")
    .replace(/^\/+/, "");
}

export function normalizeMediaNeedle(media: string): { needle: string; base: string; stem: string } {
  const needle = normPath(media);
  const base = (needle.split("/").pop() || needle).toLowerCase();
  const stem = base.replace(/\.(mp4|webm|mov|mkv|png|jpe?g|webp|gif)$/i, "");
  return { needle, base, stem };
}

function pathMatchesNeedle(path: string | null | undefined, n: { needle: string; base: string; stem: string }): boolean {
  const p = normPath(path);
  if (!p || !n.needle) return false;
  if (p.includes(n.needle)) return true;
  if (n.base && p.includes(n.base)) return true;
  if (n.stem && n.stem !== n.base && p.includes(n.stem)) return true;
  return false;
}

/** Haystack for name + media matching (job key, output, parent, bindings). */
export function workProductMediaHaystack(item: WorkProductItem): string {
  const parts: string[] = [
    item.family_slug || "",
    item.job_key || "",
    item.status || "",
    item.prompt_id || "",
    item.output_relpath || "",
    item.parent_output_relpath || "",
    item.parent_output || "",
  ];
  const bindings = item.bindings;
  if (bindings && typeof bindings === "object") {
    for (const b of Object.values(bindings)) {
      if (!b || typeof b !== "object") continue;
      parts.push(b.relpath || "", b.basename || "", b.path || "");
    }
  }
  return parts.filter(Boolean).join(" ").toLowerCase();
}

export function workProductMatchesMedia(item: WorkProductItem, media: string): boolean {
  const n = normalizeMediaNeedle(media);
  if (!n.needle) return false;
  const inferred = inferJobKeyFromMediaPath(media);
  const jk = String(item.job_key || "").trim();
  if (inferred && jk === inferred) return true;
  const hay = workProductMediaHaystack(item);
  if (hay.includes(n.needle)) return true;
  if (n.base && n.base !== n.needle && hay.includes(n.base)) return true;
  if (n.stem && n.stem !== n.base && hay.includes(n.stem)) return true;
  return false;
}

/**
 * Factory outputs are `{job_key}_{NNNNN}.mp4`. Used to fetch the producer when
 * it is older than the recent Workbench window.
 */
export function inferJobKeyFromMediaPath(media: string): string | null {
  const n = normalizeMediaNeedle(media);
  if (!n.stem) return null;
  const m = /^(.+)_(\d{5})$/.exec(n.stem);
  return m ? m[1] : null;
}

export function isMediaOutputOfJob(item: WorkProductItem, media: string): boolean {
  const n = normalizeMediaNeedle(media);
  if (!n.needle) return false;
  if (pathMatchesNeedle(item.output_relpath, n)) return true;
  const inferred = inferJobKeyFromMediaPath(media);
  const jk = String(item.job_key || "").trim();
  return Boolean(inferred && jk === inferred);
}

export function workProductMediaRelation(item: WorkProductItem, media: string): MediaFocusRelation | null {
  if (!workProductMatchesMedia(item, media)) return null;
  if (isMediaOutputOfJob(item, media)) return "output";
  return "source";
}

/** Prefer the job that wrote the file over jobs that only bound it as source. */
export function pickBestMediaMatch(items: WorkProductItem[], media: string): WorkProductItem | null {
  const matches = items.filter((it) => workProductMatchesMedia(it, media));
  if (!matches.length) return null;
  return matches.find((it) => isMediaOutputOfJob(it, media)) || matches[0] || null;
}

/** Jobs that produced the file. Source-only bindings do not count. */
export function filterWorkProductsByMedia(
  items: WorkProductItem[],
  media: string | null,
  opts?: { producersOnly?: boolean },
): WorkProductItem[] {
  const m = String(media || "").trim();
  if (!m) return items;
  if (opts?.producersOnly) {
    return items.filter((it) => isMediaOutputOfJob(it, m));
  }
  return items.filter((it) => workProductMatchesMedia(it, m));
}

export function mediaFocusLabel(media: string): string {
  const n = normalizeMediaNeedle(media);
  return n.base || n.needle || media;
}

export type FocusedGoneReason = "missing" | "deleted";

export function focusedGoneMessage(reason: FocusedGoneReason): string {
  return reason === "deleted" ? "Item deleted" : "Item does not exist";
}

/** Workspace-relative paths to probe for a ``?media=`` stem (optional extension). */
export function mediaFileCandidates(media: string): string[] {
  const n = String(media || "")
    .trim()
    .replace(/\\/g, "/")
    .replace(/^\/+/, "");
  if (!n) return [];
  const out = [n];
  if (!/\.(mp4|webm|mov|mkv|png|jpe?g|webp|gif)$/i.test(n)) {
    out.push(`${n}.mp4`, `${n}.png`);
  }
  return out;
}

export function filesUrlForRelpath(rel: string): string {
  const n = String(rel || "")
    .trim()
    .replace(/\\/g, "/")
    .replace(/^\/+/, "");
  return "/files/" + encodeURIComponent(n);
}

export async function probeMediaFileExists(media: string): Promise<boolean> {
  for (const rel of mediaFileCandidates(media)) {
    try {
      const r = await fetch(filesUrlForRelpath(rel), { method: "HEAD" });
      if (r.ok) return true;
    } catch {
      /* try next candidate */
    }
  }
  return false;
}
