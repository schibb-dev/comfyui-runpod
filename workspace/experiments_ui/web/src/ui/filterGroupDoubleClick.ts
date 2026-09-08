/** Off-set chips: double-click solos `key`; a second double-click on that solo restores the group. */
export function nextOffSetForGroupDoubleClick(
  off: ReadonlySet<string>,
  groupKeys: readonly string[],
  key: string,
): Set<string> {
  const alreadySolo =
    !off.has(key) &&
    groupKeys.some((k) => k !== key) &&
    groupKeys.every((k) => k === key || off.has(k));
  if (alreadySolo) return new Set();
  return new Set(groupKeys.filter((k) => k !== key));
}

export type QueueSectionShow = {
  running: boolean;
  pending: boolean;
  history: boolean;
};

/** Queue section chips: double-click solos one section; again restores running + waiting + history. */
export function nextQueueSectionShowForDoubleClick(
  show: QueueSectionShow,
  section: keyof QueueSectionShow,
  statusFilter: string,
): QueueSectionShow {
  const allOn: QueueSectionShow = { running: true, pending: true, history: true };
  const alreadySolo =
    statusFilter !== "errors" &&
    show[section] &&
    (Object.keys(show) as (keyof QueueSectionShow)[]).every((k) =>
      k === section ? show[k] : !show[k],
    );
  if (alreadySolo) return allOn;
  return {
    running: section === "running",
    pending: section === "pending",
    history: section === "history",
  };
}
