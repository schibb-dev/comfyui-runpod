import type { DispositionCatalogMarker, DispositionReasonDetail } from "./types";

/** POST marker that clears every entry + reason on the asset. */
export const DISPOSITION_CLEAR_ALL = "none";

function reasonIdsForProcess(reasons: DispositionCatalogMarker[], process: string): string[] {
  const proc = process.trim();
  return reasons.filter((r) => String(r.process || "").trim() === proc).map((r) => r.id);
}

function entriesConflict(a: DispositionCatalogMarker, b: DispositionCatalogMarker): boolean {
  if (a.id === b.id) return false;
  if (a.exclusive || b.exclusive) return true;
  const listedA = new Set((a.conflicts_with || []).map((x) => String(x).trim()).filter(Boolean));
  const listedB = new Set((b.conflicts_with || []).map((x) => String(x).trim()).filter(Boolean));
  return listedA.has(b.id) || listedB.has(a.id);
}

function dropProcess(
  nextMarkers: Set<string>,
  nextDetail: Record<string, DispositionReasonDetail>,
  reasons: DispositionCatalogMarker[],
  process: string,
): void {
  nextMarkers.delete(process);
  for (const rid of reasonIdsForProcess(reasons, process)) {
    nextMarkers.delete(rid);
    delete nextDetail[rid];
  }
}

function isClearAllMarker(markerId: string): boolean {
  const id = markerId.trim().toLowerCase();
  return id === DISPOSITION_CLEAR_ALL || id === "*" || id === "_clear";
}

/** Mirror server toggle rules so disposition tiles update before the POST returns. */
export function optimisticDispositionToggle(
  markers: string[],
  reasonDetail: Record<string, DispositionReasonDetail>,
  entries: DispositionCatalogMarker[],
  reasons: DispositionCatalogMarker[],
  markerId: string,
  on: boolean,
  extra?: { note?: string; modifiers?: string[] },
): { markers: string[]; reasonDetail: Record<string, DispositionReasonDetail> } {
  const nextMarkers = new Set(markers);
  const nextDetail: Record<string, DispositionReasonDetail> = { ...reasonDetail };

  if (isClearAllMarker(markerId)) {
    if (!on) {
      for (const e of entries) {
        dropProcess(nextMarkers, nextDetail, reasons, String(e.process || e.id).trim());
        nextMarkers.delete(e.id);
      }
      for (const r of reasons) {
        nextMarkers.delete(r.id);
        delete nextDetail[r.id];
      }
    }
    for (const rid of Object.keys(nextDetail)) {
      if (!nextMarkers.has(rid)) delete nextDetail[rid];
    }
    return { markers: [...nextMarkers].sort(), reasonDetail: nextDetail };
  }

  const spec = entries.find((e) => e.id === markerId) ?? reasons.find((r) => r.id === markerId);
  if (!spec) return { markers: [...markers], reasonDetail: { ...reasonDetail } };

  const kind = spec.kind;
  const entryIds = new Set(entries.map((e) => e.id));

  if (on) {
    if (kind === "entry") {
      for (const other of entries) {
        if (other.id !== markerId && entriesConflict(spec, other)) {
          dropProcess(nextMarkers, nextDetail, reasons, String(other.process || other.id).trim());
        }
      }
      nextMarkers.add(markerId);
    } else if (kind === "reason") {
      const process = String(spec.process || "").trim();
      const processEntry = entries.find((e) => e.id === process);
      if (process && entryIds.has(process) && processEntry) {
        for (const other of entries) {
          if (other.id !== process && entriesConflict(processEntry, other)) {
            dropProcess(nextMarkers, nextDetail, reasons, String(other.process || other.id).trim());
          }
        }
        nextMarkers.add(process);
      }
      nextMarkers.add(markerId);
      const detail: DispositionReasonDetail = {};
      if (extra?.modifiers !== undefined) {
        if (extra.modifiers.length) detail.modifiers = extra.modifiers;
      } else {
        const prev = nextDetail[markerId];
        if (prev?.modifiers?.length) detail.modifiers = [...prev.modifiers];
      }
      const note = (extra?.note || nextDetail[markerId]?.note || "").trim();
      if (note) detail.note = note;
      nextDetail[markerId] = detail;
    } else {
      nextMarkers.add(markerId);
    }
  } else {
    nextMarkers.delete(markerId);
    if (kind === "reason") {
      delete nextDetail[markerId];
    } else if (kind === "entry") {
      const process = String(spec.process || markerId).trim();
      dropProcess(nextMarkers, nextDetail, reasons, process);
    }
  }

  for (const rid of Object.keys(nextDetail)) {
    if (!nextMarkers.has(rid)) delete nextDetail[rid];
  }

  return { markers: [...nextMarkers].sort(), reasonDetail: nextDetail };
}
