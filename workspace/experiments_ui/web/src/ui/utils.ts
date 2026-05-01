export function uniq<T>(xs: T[]): T[] {
  const out: T[] = [];
  for (const x of xs) if (!out.includes(x)) out.push(x);
  return out;
}

export function cmp(a: unknown, b: unknown): number {
  if (a === b) return 0;
  if (a == null) return -1;
  if (b == null) return 1;
  const na = typeof a === "number" ? a : Number(a);
  const nb = typeof b === "number" ? b : Number(b);
  if (Number.isFinite(na) && Number.isFinite(nb)) return na < nb ? -1 : 1;
  const sa = String(a);
  const sb = String(b);
  return sa.localeCompare(sb);
}

export function fmt(v: unknown): string {
  if (v === null) return "null";
  if (v === undefined) return "";
  if (typeof v === "number") return Number.isFinite(v) ? String(v) : "NaN";
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "string") return v;
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

