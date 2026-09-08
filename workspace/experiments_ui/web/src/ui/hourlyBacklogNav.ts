import type { HourlyChainBacklog, HourlyChainBacklogItem } from "./types";

export function backlogNextPicks(chain: HourlyChainBacklog): HourlyChainBacklogItem[] {
  if (chain.next_picks?.length) return chain.next_picks.filter((it) => Boolean(it?.job_key));
  return chain.next?.job_key ? [chain.next] : [];
}

export function backlogNavItems(
  chain: HourlyChainBacklog,
  familyFilter: string,
): HourlyChainBacklogItem[] {
  const items = chain.items ?? [];
  const visible =
    familyFilter === "all"
      ? items
      : items.filter((it) => (it.producer_family || "") === familyFilter);
  const seen = new Set<string>();
  const out: HourlyChainBacklogItem[] = [];
  for (const it of [...backlogNextPicks(chain), ...visible]) {
    const key = String(it.job_key || "");
    if (!key || seen.has(key)) continue;
    seen.add(key);
    out.push(it);
  }
  return out;
}

export function stepBacklogNav(
  items: HourlyChainBacklogItem[],
  currentKey: string | null | undefined,
  delta: number,
): HourlyChainBacklogItem | null {
  if (!items.length) return null;
  const idx = items.findIndex((it) => it.job_key === currentKey);
  if (idx < 0) return items[0] ?? null;
  const next = (idx + delta + items.length * 8) % items.length;
  return items[next] ?? null;
}

export function backlogItemByKey(
  chain: HourlyChainBacklog | null | undefined,
  jobKey: string | null | undefined,
): HourlyChainBacklogItem | null {
  if (!chain || !jobKey) return null;
  return (
    backlogNextPicks(chain).find((it) => it.job_key === jobKey) ||
    (chain.items ?? []).find((it) => it.job_key === jobKey) ||
    (chain.next?.job_key === jobKey ? chain.next : null)
  );
}
