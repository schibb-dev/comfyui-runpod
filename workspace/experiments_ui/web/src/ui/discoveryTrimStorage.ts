/**
 * Discovery trim in/out: server-backed sidecar (`*.trims.json` next to the video via
 * `/api/discovery/trim`), with localStorage fallback when the API is unreachable.
 *
 * Keys use `${context}::${media_relpath}` where `media_relpath` is a `.mp4` / `.webm`
 * path under the output root. If no such path exists (edge cases), fallback uses
 * `${context}::legacy::${assetKey}` so offline edits still stick to the current item.
 */
import type { DiscoveryLibraryItem } from "./types";
import { fetchDiscoveryTrim, postDiscoveryTrimSave } from "./api";
import { phoneTrimBounds, phoneTrimPlaybackActive } from "./phoneTrimModel";

const STORAGE_KEY = "discovery_library_trim_v2";

/** Default UI context; additional contexts can share the same sidecar file on the server. */
export const TRIM_CONTEXT_DISCOVERY_PLAYER = "discovery-player" as const;
export type TrimContextDiscoveryPlayer = typeof TRIM_CONTEXT_DISCOVERY_PLAYER;

export function discoveryTrimMediaRelpath(it: DiscoveryLibraryItem): string | null {
  const norm = (s: string) => s.replace(/\\/g, "/").trim();
  const vr = it.video_relpath ? norm(it.video_relpath) : "";
  if (vr && /\.(mp4|webm)$/i.test(vr)) return vr;
  const rp = it.relpath ? norm(it.relpath) : "";
  if (rp && /\.(mp4|webm)$/i.test(rp)) return rp;
  return null;
}

type TrimStoreV2 = {
  v: 2;
  entries: Record<string, { in: number; out: number; at: number }>;
};

function entryKey(context: string, mediaRelpath: string | null, legacyAssetKey: string): string | null {
  if (mediaRelpath) return `${context}::${mediaRelpath}`;
  if (legacyAssetKey) return `${context}::legacy::${legacyAssetKey}`;
  return null;
}

function readLocalStore(): TrimStoreV2 {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { v: 2, entries: {} };
    const p = JSON.parse(raw) as Partial<TrimStoreV2>;
    if (p?.v === 2 && p.entries && typeof p.entries === "object") return p as TrimStoreV2;
  } catch {
    /* ignore */
  }
  return { v: 2, entries: {} };
}

function writeLocalStore(s: TrimStoreV2): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
  } catch {
    /* ignore */
  }
}

function isValidEntry(inSec: number, outSec: number): boolean {
  return (
    Number.isFinite(inSec) &&
    Number.isFinite(outSec) &&
    inSec >= 0 &&
    outSec > inSec &&
    outSec - inSec >= 1 / 1000
  );
}

function loadDiscoveryTrimLocal(
  context: string,
  mediaRelpath: string | null,
  legacyAssetKey: string
): { in: number; out: number } | null {
  const key = entryKey(context, mediaRelpath, legacyAssetKey);
  if (!key) return null;
  const row = readLocalStore().entries[key];
  if (!row || !isValidEntry(row.in, row.out)) return null;
  return { in: row.in, out: row.out };
}

function deleteDiscoveryTrimLocal(
  context: string,
  mediaRelpath: string | null,
  legacyAssetKey: string
): void {
  const key = entryKey(context, mediaRelpath, legacyAssetKey);
  if (!key) return;
  const s = readLocalStore();
  if (!(key in s.entries)) return;
  delete s.entries[key];
  writeLocalStore(s);
}

function persistDiscoveryTrimLocal(
  context: string,
  mediaRelpath: string | null,
  legacyAssetKey: string,
  markIn: number | null,
  markOut: number | null,
  duration: number
): void {
  const key = entryKey(context, mediaRelpath, legacyAssetKey);
  if (!key) return;

  if (markIn == null && markOut == null) {
    deleteDiscoveryTrimLocal(context, mediaRelpath, legacyAssetKey);
    return;
  }

  if (!Number.isFinite(duration) || duration <= 0) return;

  const b = phoneTrimBounds(markIn, markOut, duration);
  if (!b || !phoneTrimPlaybackActive(b, duration)) {
    deleteDiscoveryTrimLocal(context, mediaRelpath, legacyAssetKey);
    return;
  }

  const s = readLocalStore();
  s.entries[key] = { in: b.in, out: b.out, at: Date.now() };
  writeLocalStore(s);
}

export type LoadDiscoveryTrimResult = {
  in: number;
  out: number;
  activePresetId: string | null;
};

/** Load trim for `context` + media path (server first, then local fallback). */
export async function loadDiscoveryTrimAsync(
  context: string,
  mediaRelpath: string | null,
  legacyAssetKey: string
): Promise<LoadDiscoveryTrimResult | null> {
  if (!legacyAssetKey && !mediaRelpath) return null;

  const pushLocal = () => {
    const row = loadDiscoveryTrimLocal(context, mediaRelpath, legacyAssetKey);
    return row ? { ...row, activePresetId: null as string | null } : null;
  };

  if (!mediaRelpath) {
    return pushLocal();
  }

  try {
    const j = await fetchDiscoveryTrim(mediaRelpath, context);
    const active = j.active;
    if (
      j.found &&
      active &&
      typeof active.in === "number" &&
      typeof active.out === "number" &&
      isValidEntry(active.in, active.out)
    ) {
      const pid =
        typeof active.id === "string" && active.id
          ? active.id
          : typeof j.active_preset_id === "string"
            ? j.active_preset_id
            : null;
      return { in: active.in, out: active.out, activePresetId: pid };
    }
    return pushLocal();
  } catch {
    return pushLocal();
  }
}

export type PersistDiscoveryTrimOpts = {
  context: string;
  mediaRelpath: string | null;
  legacyAssetKey: string;
  markIn: number | null;
  markOut: number | null;
  duration: number;
  /** When omitted, the server updates whichever preset is active in the sidecar. */
  presetId?: string | null;
};

/**
 * Persist trim (server when `mediaRelpath` is set). On failure, writes the same range to
 * localStorage so the phone UI stays usable offline.
 */
export async function persistDiscoveryTrimAsync(opts: PersistDiscoveryTrimOpts): Promise<void> {
  const { context, mediaRelpath, legacyAssetKey, markIn, markOut, duration, presetId } = opts;

  const pushLocal = () => persistDiscoveryTrimLocal(context, mediaRelpath, legacyAssetKey, markIn, markOut, duration);

  const durForApi = Number.isFinite(duration) && duration > 0 ? duration : 1;

  if (!mediaRelpath) {
    pushLocal();
    return;
  }

  if (markIn == null && markOut == null) {
    try {
      await postDiscoveryTrimSave({
        media_relpath: mediaRelpath,
        context,
        duration_sec: durForApi,
        clear: true,
      });
    } catch {
      pushLocal();
    }
    return;
  }

  if (!Number.isFinite(duration) || duration <= 0) return;

  const b = phoneTrimBounds(markIn, markOut, duration);
  if (!b || !phoneTrimPlaybackActive(b, duration)) {
    try {
      await postDiscoveryTrimSave({
        media_relpath: mediaRelpath,
        context,
        duration_sec: durForApi,
        clear: true,
      });
    } catch {
      pushLocal();
    }
    return;
  }

  try {
    await postDiscoveryTrimSave({
      media_relpath: mediaRelpath,
      context,
      duration_sec: duration,
      in: b.in,
      out: b.out,
      ...(presetId ? { preset_id: presetId } : {}),
    });
  } catch {
    pushLocal();
  }
}
