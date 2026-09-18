/** Comma-separated still gallery tag filter (AND). Clicking a tag toggles membership. */

export function parseStillTagFilter(raw: string | string[] | null | undefined): string[] {
  const parts = Array.isArray(raw) ? raw : String(raw || "").split(/[,;]/);
  const out: string[] = [];
  const seen = new Set<string>();
  for (const part of parts) {
    const n = String(part || "").trim().toLowerCase();
    if (!n || seen.has(n)) continue;
    seen.add(n);
    out.push(n);
  }
  return out;
}

export function serializeStillTagFilter(tags: Iterable<string>): string {
  return parseStillTagFilter([...tags]).join(",");
}

export function stillTagFilterHas(tags: Iterable<string>, tag: string): boolean {
  const n = String(tag || "").trim().toLowerCase();
  return Boolean(n) && parseStillTagFilter([...tags]).includes(n);
}

export function toggleStillTagFilter(tags: Iterable<string>, tag: string): string[] {
  const cur = parseStillTagFilter([...tags]);
  const n = String(tag || "").trim().toLowerCase();
  if (!n) return cur;
  return cur.includes(n) ? cur.filter((t) => t !== n) : [...cur, n];
}
