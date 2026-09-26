import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppetitePreviewBadge } from "./AppetitePreviewBadge";
import {
  clearHourlyBinSteering,
  fetchHourlyBinCandidates,
  setHourlyBinCurationUnit,
  setHourlyBinItem,
} from "./api";
import {
  peekAssetRatings,
  prefetchAssetRatings,
  rememberAssetRatings,
} from "./assetRatingsCache";
import { factoryMapFamilyHref, factoryMapHourliesHref } from "./factoryMapRoute";
import { useRegisterPhoneOverflow } from "./phoneChrome";
import { useIsPhone } from "./viewport";
import type { Appetite, AppetiteFacet, HourlyBinCandidate, HourlyBinSummary } from "./types";
import { normalizeAppetiteRelpath } from "./workProductAppetite";

const SWIPE_AXIS_LOCK_PX = 10;
const SWIPE_COMMIT_PX = 48;
const DEFAULT_BIN_ID = "hourly-seed-stills";
const BIN_QUERY = "bin";
/** Prefetch appetite for current card ± this many neighbors while browsing. */
const APPETITE_WARM_RADIUS = 4;
const CURATION_UNITS = ["auto", "clips", "videos"] as const;
type CurationUnit = (typeof CURATION_UNITS)[number];

const APPETITES: ReadonlySet<string> = new Set(["less", "neutral", "more", "fast_track", "remove"]);
const APPETITE_FACETS: ReadonlySet<string> = new Set(["both", "source", "processing"]);

function asAppetite(value: string | null | undefined): Appetite | null {
  const v = String(value || "").trim().toLowerCase();
  return APPETITES.has(v) ? (v as Appetite) : null;
}

function asAppetiteFacet(value: string | null | undefined): AppetiteFacet | null {
  const v = String(value || "").trim().toLowerCase();
  return APPETITE_FACETS.has(v) ? (v as AppetiteFacet) : null;
}

function asCurationUnit(value: string | null | undefined): CurationUnit {
  const v = String(value || "").trim().toLowerCase();
  return v === "clips" || v === "videos" ? v : "auto";
}

function isVideoCandidate(item: HourlyBinCandidate | null | undefined): boolean {
  if (!item) return false;
  if (String(item.media_kind || "").toLowerCase() === "video") return true;
  const unit = String(item.unit || "").toLowerCase();
  return unit === "span" || unit === "whole";
}

/**
 * Seed session cache from candidate payload (instant badge), then prefetch
 * ratings for a small nearby window — not the whole deck (that stalls reloads).
 */
function warmAppetiteForCandidates(items: HourlyBinCandidate[], preferFirst = 0) {
  const rels: string[] = [];
  const maxWarm = Math.min(items.length, Math.max(preferFirst, 8));
  for (let i = 0; i < maxWarm; i++) {
    const it = items[i];
    if (!it || isVideoCandidate(it)) continue;
    const key = normalizeAppetiteRelpath(it.relpath);
    if (!key) continue;
    rels.push(key);
    if (peekAssetRatings(key)) continue;
    const appetite = asAppetite(it.appetite);
    const facet = asAppetiteFacet(it.appetite_facet);
    if (appetite || facet) {
      rememberAssetRatings(key, {
        ok: true,
        query_relpath: key,
        appetite,
        appetite_facet: facet,
      });
    }
  }
  if (rels.length) prefetchAssetRatings(rels);
}

type BinAction = "keep" | "pin" | "later" | "out";
type DeckAction = BinAction | "clear";
type FilterKey = "new" | BinAction;

const DECK_ACTIONS: DeckAction[] = ["clear", "out", "later", "keep", "pin"];
const FILTER_KEYS: FilterKey[] = ["new", "out", "later", "keep", "pin"];

function isBinAction(value: string | null | undefined): value is BinAction {
  return value === "keep" || value === "pin" || value === "later" || value === "out";
}

function actionLabel(status: DeckAction): string {
  if (status === "clear") return "Clear";
  if (status === "keep") return "Keep";
  if (status === "pin") return "Pin";
  if (status === "later") return "Later";
  return "Out";
}

function filterLabel(key: FilterKey): string {
  return key === "new" ? "New" : actionLabel(key);
}

function itemFilterKey(item: HourlyBinCandidate): FilterKey {
  return isBinAction(item.prior_status) ? item.prior_status : "new";
}

function countsLine(bin?: HourlyBinSummary | null): string {
  const c = bin?.counts;
  if (!c) return "";
  return `feed ${Number(c.keep || 0) + Number(c.pin || 0)} · later ${c.later || 0} · out ${c.out || 0}`;
}

type FilterSet = Record<FilterKey, boolean>;

const FILTERS_ALL_ON: FilterSet = { new: true, out: true, later: true, keep: true, pin: true };
const FILTERS_NEW_ONLY: FilterSet = { new: true, out: false, later: false, keep: false, pin: false };

function filtersAllOn(filters: FilterSet): boolean {
  return FILTER_KEYS.every((k) => filters[k]);
}

function filtersSoloKey(filters: FilterSet): FilterKey | null {
  const on = FILTER_KEYS.filter((k) => filters[k]);
  return on.length === 1 ? on[0] : null;
}

function anyFilterOn(filters: FilterSet): boolean {
  return FILTER_KEYS.some((k) => filters[k]);
}

/** Prefer New alone; if empty, enable every non-empty bucket (OR deck). */
function defaultFiltersForCounts(counts: Record<FilterKey, number>): FilterSet {
  if (counts.new > 0) return { ...FILTERS_NEW_ONLY };
  const next: FilterSet = { new: false, out: false, later: false, keep: false, pin: false };
  let any = false;
  for (const k of FILTER_KEYS) {
    if (counts[k] > 0) {
      next[k] = true;
      any = true;
    }
  }
  return any ? next : { ...FILTERS_NEW_ONLY };
}

function toggleFilter(filters: FilterSet, key: FilterKey): FilterSet {
  return { ...filters, [key]: !filters[key] };
}

/** Double-click: solo this key, or expand to all when already solo on it. */
function soloOrAllFilters(filters: FilterSet, key: FilterKey): FilterSet {
  if (filtersSoloKey(filters) === key) return { ...FILTERS_ALL_ON };
  return { new: false, out: false, later: false, keep: false, pin: false, [key]: true };
}

function filterItems(items: HourlyBinCandidate[], filters: FilterSet): HourlyBinCandidate[] {
  if (!anyFilterOn(filters)) return [];
  // Preserve API order — no status grouping until we add explicit sort controls.
  return items.filter((it) => filters[itemFilterKey(it)]);
}

function activeFilterLabels(filters: FilterSet): string {
  const labels = FILTER_KEYS.filter((k) => filters[k]).map(filterLabel);
  if (!labels.length) return "selected";
  if (labels.length === FILTER_KEYS.length) return "any";
  return labels.join("/");
}

function workflowFamilies(bin?: HourlyBinSummary | null): string[] {
  return (bin?.workflow_families || []).map((x) => String(x || "").trim()).filter(Boolean);
}

function targetLabel(t: HourlyBinSummary): string {
  const fam = String(t.pool_family || "").trim();
  const n = Number(t.item_count || 0);
  const feed = Number(t.feed_count || 0);
  const slot = String(t.pool_slot || "").trim();
  const kind = slot === "source_video" ? "v" : "s";
  const base = fam || String(t.label || t.id || "bin");
  // Keep closed <select> text short — long "feed / n" + "source_video" truncates the menu.
  if (n > 0) return `${base} · ${kind} · ${feed}/${n}`;
  return `${base} · ${kind}`;
}

function readBinIdFromUrl(): string {
  try {
    const sp = new URLSearchParams(window.location.search);
    return String(sp.get(BIN_QUERY) || "").trim() || DEFAULT_BIN_ID;
  } catch {
    return DEFAULT_BIN_ID;
  }
}

function writeBinIdToUrl(binId: string) {
  try {
    const url = new URL(window.location.href);
    if (!binId || binId === DEFAULT_BIN_ID) url.searchParams.delete(BIN_QUERY);
    else url.searchParams.set(BIN_QUERY, binId);
    window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
  } catch {
    /* ignore */
  }
}

/**
 * Phone-first hourly seed stills / clips curation.
 * Vertical swipe through candidates; fat Keep / Pin / Later / Out actions.
 */
export function HourlySeedBinCurateApp() {
  const isPhone = useIsPhone();
  const [binId, setBinId] = useState(readBinIdFromUrl);
  const [targets, setTargets] = useState<HourlyBinSummary[]>([]);
  const [items, setItems] = useState<HourlyBinCandidate[]>([]);
  const [index, setIndex] = useState(0);
  const [bin, setBin] = useState<HourlyBinSummary | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [hintVisible, setHintVisible] = useState(false);
  // Additive OR filters — click toggles each bucket; double-click solos / shows all.
  const [filters, setFilters] = useState<FilterSet>(FILTERS_NEW_ONLY);
  const swipeStartRef = useRef<{ x: number; y: number; id: number } | null>(null);
  const swipeAxisRef = useRef<null | "x" | "y">(null);
  const goRef = useRef<(delta: number) => void>(() => undefined);
  const hintShownRef = useRef(false);
  const filterClickTimerRef = useRef<number | null>(null);

  const load = useCallback(async (opts?: { binId?: string }) => {
    const want = String(opts?.binId || binId || DEFAULT_BIN_ID).trim() || DEFAULT_BIN_ID;
    setLoading(true);
    setError("");
    try {
      const res = await fetchHourlyBinCandidates({ binId: want, limit: 48 });
      const nextItems = res.items || [];
      const nextBin = res.bin || null;
      const nextTargets = Array.isArray(res.targets) ? res.targets : [];
      const resolvedId = String(nextBin?.id || res.bin_id || want).trim() || want;
      setTargets(nextTargets);
      setItems(nextItems);
      setBin(nextBin);
      setBinId(resolvedId);
      writeBinIdToUrl(resolvedId);
      // Nearby still appetite only — full-deck prefetch made reloads feel stuck.
      warmAppetiteForCandidates(nextItems, 8);
      const counts: Record<FilterKey, number> = { new: 0, out: 0, later: 0, keep: 0, pin: 0 };
      for (const it of nextItems) counts[itemFilterKey(it)] += 1;
      setFilters(defaultFiltersForCounts(counts));
      setIndex(0);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [binId]);

  useEffect(() => {
    void load({ binId });
    // Initial + explicit bin switches call load(); avoid double-fetch on every binId writeback.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- mount once from URL
  }, []);

  // Brief first-load hint, then fade so the image keeps the frame.
  useEffect(() => {
    if (!isPhone || loading || !items.length || hintShownRef.current) return;
    hintShownRef.current = true;
    setHintVisible(true);
    const hide = window.setTimeout(() => setHintVisible(false), 2800);
    return () => window.clearTimeout(hide);
  }, [isPhone, loading, items.length]);

  const filterCounts = useMemo(() => {
    const counts: Record<FilterKey, number> = { new: 0, out: 0, later: 0, keep: 0, pin: 0 };
    for (const it of items) counts[itemFilterKey(it)] += 1;
    return counts;
  }, [items]);

  const poolFamily = String(bin?.pool_family || "").trim();
  const poolSlot = String(bin?.pool_slot || "source_still").trim() || "source_still";
  const isVideoPool = poolSlot === "source_video";
  const curationUnit = asCurationUnit(bin?.curation_unit);
  const workflows = useMemo(() => workflowFamilies(bin), [bin]);
  const scopeTitle = useMemo(() => {
    const bits = [
      poolFamily ? `pool ${poolFamily}` : null,
      `slot ${poolSlot}`,
      workflows.length ? `hourly seed lottery: ${workflows.join(", ")}` : null,
    ].filter(Boolean);
    return bits.join(" · ");
  }, [poolFamily, poolSlot, workflows]);

  const selectTargets = useMemo(() => {
    if (targets.length) return targets;
    return bin ? [bin] : [];
  }, [targets, bin]);

  const setUnit = useCallback(
    async (unit: CurationUnit) => {
      if (!isVideoPool || busy || loading || unit === curationUnit) return;
      setBusy(true);
      setError("");
      try {
        const res = await setHourlyBinCurationUnit({ bin_id: binId, curation_unit: unit });
        if (res.bin) {
          setBin(res.bin);
          setTargets((prev) =>
            prev.map((t) => (String(t.id || "") === String(res.bin?.id || binId) ? res.bin! : t)),
          );
        }
        await load({ binId });
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [binId, busy, curationUnit, isVideoPool, load, loading],
  );

  const visible = useMemo(() => filterItems(items, filters), [items, filters]);

  // Keep appetite warm around the current card while browsing.
  useEffect(() => {
    if (!visible.length) return;
    const i = Math.min(Math.max(0, index), visible.length - 1);
    const lo = Math.max(0, i - APPETITE_WARM_RADIUS);
    const hi = Math.min(visible.length, i + APPETITE_WARM_RADIUS + 1);
    warmAppetiteForCandidates(visible.slice(lo, hi));
  }, [visible, index]);

  // Keep index in range when the active OR deck shrinks.
  useEffect(() => {
    if (!visible.length) {
      setIndex(0);
      return;
    }
    setIndex((i) => Math.min(Math.max(0, i), visible.length - 1));
  }, [visible.length, filters]);

  useEffect(() => {
    return () => {
      if (filterClickTimerRef.current != null) window.clearTimeout(filterClickTimerRef.current);
    };
  }, []);

  const onFilterClick = (key: FilterKey) => {
    if (filterClickTimerRef.current != null) window.clearTimeout(filterClickTimerRef.current);
    // Defer so a double-click can cancel and run solo/all instead.
    filterClickTimerRef.current = window.setTimeout(() => {
      filterClickTimerRef.current = null;
      setFilters((prev) => toggleFilter(prev, key));
      setIndex(0);
    }, 220);
  };

  const onFilterDoubleClick = (key: FilterKey) => {
    if (filterClickTimerRef.current != null) {
      window.clearTimeout(filterClickTimerRef.current);
      filterClickTimerRef.current = null;
    }
    setFilters((prev) => soloOrAllFilters(prev, key));
    setIndex(0);
  };

  const safeIndex = visible.length ? Math.min(index, visible.length - 1) : 0;
  const item = visible.length ? visible[safeIndex] : null;
  const selected = isBinAction(item?.prior_status) ? item.prior_status : null;
  // Absolute deck slot — Keep/Pin/Out must not change which image is N/total.
  const deckIndex = item ? items.findIndex((x) => x.content_id === item.content_id) : -1;
  const deckPos = deckIndex >= 0 ? deckIndex + 1 : 0;
  const deckTotal = items.length;

  const go = useCallback(
    (delta: number) => {
      if (visible.length <= 0) return;
      setIndex((i) => {
        const n = visible.length;
        return (((i + delta) % n) + n) % n;
      });
    },
    [visible.length],
  );
  goRef.current = go;

  const onSelectBin = (nextId: string) => {
    const id = String(nextId || "").trim() || DEFAULT_BIN_ID;
    if (id === binId && !loading) return;
    setBinId(id);
    writeBinIdToUrl(id);
    void load({ binId: id });
  };

  const decidedCount = useMemo(
    () => items.reduce((n, it) => n + (isBinAction(it.prior_status) ? 1 : 0), 0),
    [items],
  );

  const clearSteering = useCallback(async () => {
    if (busy || loading || decidedCount <= 0) return;
    const label = poolFamily || binId;
    const ok = window.confirm(
      `Clear all steering for ${label}?\n\n${decidedCount} Keep/Pin/Later/Out mark${decidedCount === 1 ? "" : "s"} become New. Hourly bias for this pool resets.`,
    );
    if (!ok) return;
    setBusy(true);
    setError("");
    try {
      const res = await clearHourlyBinSteering({
        bin_id: binId,
        surface: isPhone ? "phone" : "desktop",
      });
      if (res.bin) {
        setBin(res.bin);
        setTargets((prev) => {
          if (!prev.length) return prev;
          return prev.map((t) => (String(t.id || "") === String(res.bin?.id || binId) ? res.bin! : t));
        });
      }
      setItems((prev) => prev.map((x) => ({ ...x, prior_status: null })));
      setFilters({ ...FILTERS_NEW_ONLY });
      setIndex(0);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [binId, busy, decidedCount, isPhone, loading, poolFamily]);

  const act = useCallback(
    async (status: DeckAction) => {
      if (!item || busy) return;
      // Already New — Clear is a no-op.
      if (status === "clear" && !isBinAction(item.prior_status)) return;
      const cid = item.content_id;
      const activeFilters = filters;
      const activeBin = binId;
      const nextStatus: BinAction | null = status === "clear" ? null : status;
      setBusy(true);
      setError("");
      try {
        const res = await setHourlyBinItem({
          bin_id: activeBin,
          content_id: cid,
          relpath: item.relpath,
          status: status === "clear" ? "clear" : status,
          surface: isPhone ? "phone" : "desktop",
          unit: item.unit || undefined,
          parent_content_id: item.parent_content_id || undefined,
          mark_in_s: item.mark_in_s ?? undefined,
          mark_out_s: item.mark_out_s ?? undefined,
        });
        if (res.bin) {
          setBin(res.bin);
          setTargets((prev) => {
            if (!prev.length) return prev;
            return prev.map((t) => (String(t.id || "") === String(res.bin?.id || activeBin) ? res.bin! : t));
          });
        }
        setItems((prev) => {
          // Update status in place — never reorder the deck.
          const nextItems = prev.map((x) =>
            x.content_id === cid ? { ...x, prior_status: nextStatus } : x,
          );
          const nextVisible = filterItems(nextItems, activeFilters);
          const stillHere = nextVisible.findIndex((x) => x.content_id === cid);
          if (stillHere >= 0) {
            setIndex(stillHere);
          } else if (!nextVisible.length) {
            setIndex(0);
          } else {
            // Left the active OR filter: next matching card after this one in deck order.
            const abs = nextItems.findIndex((x) => x.content_id === cid);
            let nextVis = -1;
            for (let i = abs + 1; i < nextItems.length; i++) {
              const j = nextVisible.findIndex((x) => x.content_id === nextItems[i].content_id);
              if (j >= 0) {
                nextVis = j;
                break;
              }
            }
            if (nextVis < 0) {
              for (let i = abs - 1; i >= 0; i--) {
                const j = nextVisible.findIndex((x) => x.content_id === nextItems[i].content_id);
                if (j >= 0) {
                  nextVis = j;
                  break;
                }
              }
            }
            setIndex(nextVis >= 0 ? nextVis : 0);
          }
          return nextItems;
        });
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [binId, busy, filters, isPhone, item],
  );

  const phoneOverflow = useMemo(
    () =>
      isPhone
        ? [
            {
              id: "hourly-bin-refresh",
              label: loading ? "Refreshing…" : "Refresh",
              hint: countsLine(bin) || "Reload seed-bin candidates",
              onSelect: () => {
                if (!loading && !busy) void load({ binId });
              },
            },
            {
              id: "hourly-bin-hourlies",
              label: "Hourlies",
              hint: "Cadence and backlogs",
              onSelect: () => {
                window.location.assign(factoryMapHourliesHref());
              },
            },
          ]
        : [],
    [isPhone, loading, busy, load, bin, binId],
  );
  useRegisterPhoneOverflow(phoneOverflow);

  const onPointerDown = (e: React.PointerEvent) => {
    if (visible.length <= 1) return;
    if (e.pointerType === "mouse" && e.button !== 0) return;
    const t = e.target;
    if (
      t instanceof Element &&
      t.closest(
        "button, a, select, label, .appetite-preview-badge-wrap, .appetite-preview-popover",
      )
    ) {
      return;
    }
    swipeStartRef.current = { x: e.clientX, y: e.clientY, id: e.pointerId };
    swipeAxisRef.current = null;
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const start = swipeStartRef.current;
    if (!start || e.pointerId !== start.id) return;
    const dx = e.clientX - start.x;
    const dy = e.clientY - start.y;
    if (!swipeAxisRef.current) {
      if (Math.abs(dx) < SWIPE_AXIS_LOCK_PX && Math.abs(dy) < SWIPE_AXIS_LOCK_PX) return;
      swipeAxisRef.current = Math.abs(dy) > Math.abs(dx) * 1.1 ? "y" : "x";
      if (swipeAxisRef.current === "y") {
        try {
          (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
        } catch {
          /* ignore */
        }
      }
    }
    if (swipeAxisRef.current === "y") {
      e.preventDefault();
    }
  };

  const endPointer = (e: React.PointerEvent) => {
    const start = swipeStartRef.current;
    if (!start || e.pointerId !== start.id) return;
    const dx = e.clientX - start.x;
    const dy = e.clientY - start.y;
    const vertical = swipeAxisRef.current === "y" && Math.abs(dy) >= Math.abs(dx);
    swipeStartRef.current = null;
    swipeAxisRef.current = null;
    try {
      (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
    } catch {
      /* ignore */
    }
    if (!vertical) return;
    // Swipe up → next; swipe down → previous (Queue / Workbench).
    if (dy <= -SWIPE_COMMIT_PX) goRef.current(1);
    else if (dy >= SWIPE_COMMIT_PX) goRef.current(-1);
  };

  return (
    <div className={"layout home-screen hourly-bin-curate" + (isPhone ? " hourly-bin-curate--phone" : "")}>
      <div className="hourly-bin-curate__scope-row">
        <label className="hourly-bin-curate__scope-select-wrap">
          <span className="hourly-bin-curate__scope-label">Pool</span>
          <select
            className="hourly-bin-curate__scope-select"
            value={binId}
            disabled={loading || busy || selectTargets.length <= 0}
            aria-label={isVideoPool ? "Steer which source_video pool" : "Steer which source_still pool"}
            title={scopeTitle || undefined}
            onChange={(e) => onSelectBin(e.target.value)}
          >
            {selectTargets.map((t) => {
              const id = String(t.id || "").trim();
              if (!id) return null;
              return (
                <option key={id} value={id}>
                  {targetLabel(t)}
                </option>
              );
            })}
          </select>
        </label>
        {isVideoPool ? (
          <span className="hourly-bin-curate__unit" role="group" aria-label="Curation unit">
            {CURATION_UNITS.map((u) => (
              <button
                key={u}
                type="button"
                className={
                  "hourly-bin-curate__unit-btn" +
                  (curationUnit === u ? " hourly-bin-curate__unit-btn--on is-on" : "")
                }
                disabled={busy || loading}
                aria-pressed={curationUnit === u}
                title={
                  u === "auto"
                    ? "★ clips, then clips, then whole files"
                    : u === "clips"
                      ? "Span clips only"
                      : "Whole-file units only"
                }
                onClick={() => void setUnit(u)}
              >
                {u === "auto" ? "Auto" : u === "clips" ? "Clips" : "Videos"}
              </button>
            ))}
          </span>
        ) : null}
        {poolFamily ? (
          <a
            className="hourly-bin-curate__scope-link"
            href={factoryMapFamilyHref(poolFamily, { focus: "pools" })}
            title={scopeTitle || undefined}
          >
            map
          </a>
        ) : null}
        {!isPhone && workflows.length === 1 ? (
          <span className="hourly-bin-curate__scope-muted" title={workflows[0]}>
            · steers {workflows[0]}
          </span>
        ) : !isPhone && workflows.length ? (
          <span className="hourly-bin-curate__scope-muted" title={workflows.join(", ")}>
            · steers {workflows.length} families
          </span>
        ) : null}
        <button
          type="button"
          className="hourly-bin-curate__clear"
          disabled={busy || loading || decidedCount <= 0}
          title={
            decidedCount > 0
              ? `Clear ${decidedCount} steering mark${decidedCount === 1 ? "" : "s"} for this pool`
              : "No steering marks on this pool"
          }
          onClick={() => void clearSteering()}
        >
          Clear all
        </button>
      </div>
      <div className="hourly-bin-curate__filters" role="group" aria-label="Filter stills by bin status (OR)">
        {FILTER_KEYS.map((key) => {
          const on = filters[key];
          const solo = filtersSoloKey(filters) === key;
          const tone =
            key === "keep"
              ? " hourly-bin-curate__filter--keep"
              : key === "pin"
                ? " hourly-bin-curate__filter--pin"
                : key === "later"
                  ? " hourly-bin-curate__filter--later"
                  : key === "out"
                    ? " hourly-bin-curate__filter--out"
                    : " hourly-bin-curate__filter--new";
          return (
            <button
              key={key}
              type="button"
              role="checkbox"
              className={
                "hourly-bin-curate__filter" +
                tone +
                (on ? " hourly-bin-curate__filter--on is-on" : "") +
                (solo ? " hourly-bin-curate__filter--solo" : "")
              }
              aria-checked={on}
              title={
                solo
                  ? "Double-click to show all buckets"
                  : filtersAllOn(filters)
                    ? "Click to toggle · double-click to show only this"
                    : "Click to toggle (OR) · double-click to show only this"
              }
              onClick={() => onFilterClick(key)}
              onDoubleClick={(e) => {
                e.preventDefault();
                onFilterDoubleClick(key);
              }}
            >
              {filterLabel(key)}
              <span className="hourly-bin-curate__filter-count">{filterCounts[key]}</span>
            </button>
          );
        })}
        {deckTotal && item ? (
          <span
            className="hourly-bin-curate__filter-pos mono"
            title={
              visible.length !== deckTotal
                ? `${visible.length} matching filter · slot ${deckPos} of ${deckTotal} in deck`
                : undefined
            }
          >
            {deckPos}/{deckTotal}
          </span>
        ) : (
          <span className="hourly-bin-curate__filter-pos mono">0</span>
        )}
      </div>
      {error ? <p className="drt-err">{error}</p> : null}
      {loading && !item ? (
        <p className="factory-muted">Loading candidates…</p>
      ) : !item ? (
        <p className="factory-muted">
          {items.length
            ? anyFilterOn(filters)
              ? `No ${activeFilterLabels(filters).toLowerCase()} stills in this deck.`
              : "No buckets selected — tap a filter (or double-click one to solo)."
            : "Bin caught up — no candidates right now. Refresh later or mark more appetite."}
        </p>
      ) : (
        <>
          <div
            className={
              "hourly-bin-curate__stage" +
              (selected ? ` hourly-bin-curate__stage--${selected}` : "") +
              (isVideoCandidate(item) ? " hourly-bin-curate__stage--video" : "")
            }
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={endPointer}
            onPointerCancel={endPointer}
          >
            {selected ? (
              <div
                className={`hourly-bin-curate__status hourly-bin-curate__status--${selected}`}
                aria-live="polite"
              >
                {actionLabel(selected)}
              </div>
            ) : null}
            {visible.length > 1 ? (
              <div className="hourly-bin-curate__nav" role="group" aria-label="Still navigation">
                <button
                  type="button"
                  className="hourly-bin-curate__nav-btn hourly-bin-curate__nav-btn--prev"
                  aria-label="Previous still"
                  onClick={() => go(-1)}
                >
                  ˄
                </button>
                <button
                  type="button"
                  className="hourly-bin-curate__nav-btn hourly-bin-curate__nav-btn--next"
                  aria-label="Next still"
                  onClick={() => go(1)}
                >
                  ˅
                </button>
              </div>
            ) : null}
            {isVideoCandidate(item) ? (
              <video
                className="hourly-bin-curate__img hourly-bin-curate__video"
                src={item.url || item.thumb_url}
                poster={item.thumb_url && item.thumb_url !== item.url ? item.thumb_url : undefined}
                controls
                playsInline
                muted
                loop
                preload="metadata"
                draggable={false}
              />
            ) : (
              <img
                className="hourly-bin-curate__img"
                src={item.thumb_url || item.url}
                alt={item.basename || item.relpath}
                draggable={false}
              />
            )}
            {item.unit === "span" || item.unit === "whole" || item.starred ? (
              <div className="hourly-bin-curate__clip-meta" aria-hidden={false}>
                {item.starred ? <span className="hourly-bin-curate__clip-star">★</span> : null}
                <span>
                  {item.unit === "whole"
                    ? "Whole file"
                    : item.unit === "span"
                      ? [
                          item.label || "Clip",
                          item.mark_in_s != null || item.mark_out_s != null
                            ? `${item.mark_in_s ?? 0}s–${item.mark_out_s ?? "…"}s`
                            : null,
                        ]
                          .filter(Boolean)
                          .join(" · ")
                      : null}
                </span>
              </div>
            ) : null}
            {item.relpath && !isVideoCandidate(item) ? (
              <AppetitePreviewBadge
                relpath={item.relpath}
                defaultFacet={
                  (item.appetite_facet as AppetiteFacet | null | undefined) || "source"
                }
              />
            ) : null}
            {hintVisible ? (
              <p className="hourly-bin-curate__hint" role="status">
                Swipe ↑↓ · tap ˄ ˅
              </p>
            ) : null}
          </div>
          <div className="hourly-bin-curate__actions" role="group" aria-label="Bin actions">
            {DECK_ACTIONS.map((status) => {
              const on = status === "clear" ? !selected : selected === status;
              const tone =
                status === "clear"
                  ? " hourly-bin-curate__btn--clear"
                  : status === "keep"
                    ? " hourly-bin-curate__btn--keep"
                    : status === "pin"
                      ? " hourly-bin-curate__btn--pin"
                      : status === "later"
                        ? " hourly-bin-curate__btn--later"
                        : " hourly-bin-curate__btn--out";
              return (
                <button
                  key={status}
                  type="button"
                  className={
                    "drt-btn hourly-bin-curate__btn" +
                    tone +
                    (on ? " hourly-bin-curate__btn--selected is-on" : "")
                  }
                  disabled={busy || (status === "clear" && !selected)}
                  aria-pressed={on}
                  title={status === "clear" ? "Send back to New (no steering)" : undefined}
                  onClick={() => void act(status)}
                >
                  {actionLabel(status)}
                </button>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
