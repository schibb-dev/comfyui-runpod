import React, { useEffect, useMemo, useRef, useState } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  drainShapeFactoryStillTags,
  enqueueShapeFactoryStillTagRun,
  fetchShapeFactoryInputCurationState,
  fetchShapeFactoryInputCurationStills,
  fetchShapeFactoryStillTagBacklog,
  fetchShapeFactoryStillTagEvents,
  fetchShapeFactoryStillTagRun,
  mutateShapeFactoryInputCollection,
  mutateShapeFactoryInputStillTags,
  setShapeFactoryStillTagSchedule,
} from "./api";
import { parseStillDeepLink, stillsHref, submitHref } from "./discoveryDeepLink";
import { PageHeader } from "./PageHeader";
import { queryKeys } from "./queryKeys";
import type { InputCurationCollection, InputCurationStillItem, StillTagEvent } from "./types";

const PAGE = 96;
const TAG_BATCH_DEFAULT = 12;

function stillMediaRelpath(it: InputCurationStillItem): string {
  const rel = String(it.relpath || "").trim().replace(/\\/g, "/");
  if (rel) return rel;
  const bn = String(it.basename || it.path || "")
    .trim()
    .split("/")
    .pop();
  return bn ? `input/${bn}` : "";
}

function stillTileDomId(it: InputCurationStillItem): string {
  const cid = String(it.content_id || "").trim();
  if (cid) return `still-tile-${cid}`;
  const key = String(it.path || it.relpath || it.basename || "")
    .trim()
    .replace(/[^\w.-]+/g, "_");
  return `still-tile-${key || "x"}`;
}

function stillMatchesDeepLink(
  it: InputCurationStillItem,
  deep: { contentId: string | null; relpath: string | null },
): boolean {
  const wantCid = String(deep.contentId || "").trim().toLowerCase();
  const wantRel = String(deep.relpath || "")
    .trim()
    .replace(/\\/g, "/")
    .replace(/^\/+/, "")
    .toLowerCase();
  if (wantCid && String(it.content_id || "").trim().toLowerCase() === wantCid) return true;
  if (wantRel) {
    const rel = stillMediaRelpath(it).toLowerCase();
    const base = (it.basename || "").toLowerCase();
    if (rel === wantRel || rel.endsWith("/" + wantRel) || base === wantRel.split("/").pop()) return true;
  }
  return false;
}

export function StillGalleryApp() {
  const queryClient = useQueryClient();
  const deep = useMemo(() => parseStillDeepLink(), []);
  const [q, setQ] = useState(() => {
    if (deep.q) return deep.q;
    if (deep.contentId) return deep.contentId;
    if (deep.relpath) return deep.relpath.split("/").pop() || deep.relpath;
    return "";
  });
  const [qDebounced, setQDebounced] = useState(q);
  const [tagFilter, setTagFilter] = useState("");
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [collectionId, setCollectionId] = useState<string>("");
  const [newCollectionName, setNewCollectionName] = useState("");
  const [tagDraft, setTagDraft] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [runEvents, setRunEvents] = useState<StillTagEvent[]>([]);
  const [runStatus, setRunStatus] = useState<string | null>(null);
  const eventAfterId = useRef(0);
  const deepLinkDone = useRef(false);
  const [deepLinkHitPath, setDeepLinkHitPath] = useState<string | null>(null);

  useEffect(() => {
    const t = window.setTimeout(() => setQDebounced(q.trim()), 250);
    return () => window.clearTimeout(t);
  }, [q]);

  const stillsKey = {
    q: qDebounced,
    tag: tagFilter.trim(),
    limit: PAGE,
  };

  const stillsQuery = useInfiniteQuery({
    queryKey: queryKeys.shapeFactory.inputCurationStills(stillsKey),
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      fetchShapeFactoryInputCurationStills({
        q: stillsKey.q || undefined,
        tag: stillsKey.tag || undefined,
        limit: PAGE,
        offset: pageParam,
      }),
    getNextPageParam: (last) => {
      if (!last?.has_more) return undefined;
      const next = last.next_offset ?? (last.offset || 0) + (last.items?.length || 0);
      if (!last.items?.length) return undefined;
      return next;
    },
    staleTime: 15_000,
  });

  const stateQuery = useQuery({
    queryKey: queryKeys.shapeFactory.inputCurationState,
    queryFn: fetchShapeFactoryInputCurationState,
    staleTime: 15_000,
  });

  const backlogQuery = useQuery({
    queryKey: queryKeys.shapeFactory.stillTagBacklog,
    queryFn: fetchShapeFactoryStillTagBacklog,
    refetchInterval: 5_000,
    staleTime: 2_000,
  });

  const collections = (stateQuery.data?.collections || []) as InputCurationCollection[];
  const selectedCollection =
    collections.find((c) => c.id === collectionId) || (collections.length ? collections[0] : null);

  useEffect(() => {
    if (!collectionId && collections.length) setCollectionId(collections[0].id);
  }, [collectionId, collections]);

  const items = useMemo(() => {
    const out: InputCurationStillItem[] = [];
    const seen = new Set<string>();
    for (const page of stillsQuery.data?.pages || []) {
      for (const it of page.items || []) {
        const key = it.path || it.relpath || it.basename || "";
        if (!key || seen.has(key)) continue;
        seen.add(key);
        out.push(it);
      }
    }
    return out;
  }, [stillsQuery.data?.pages]);

  const selected =
    items.find((it) => it.path === selectedPath) ||
    (selectedPath ? items.find((it) => stillMediaRelpath(it) === selectedPath) : null) ||
    null;

  useEffect(() => {
    if (selected) setTagDraft((selected.editorial_tags || selected.tags || []).join(", "));
    else setTagDraft("");
  }, [selected?.path, selected?.content_id, (selected?.editorial_tags || selected?.tags || []).join("|")]);

  useEffect(() => {
    if (deepLinkDone.current || stillsQuery.isLoading) return;
    if (!deep.contentId && !deep.relpath) return;
    const match = items.find((it) => stillMatchesDeepLink(it, deep));
    if (!match) {
      if (stillsQuery.hasNextPage && !stillsQuery.isFetchingNextPage) {
        void stillsQuery.fetchNextPage();
      } else if (!stillsQuery.isFetchingNextPage && items.length) {
        deepLinkDone.current = true;
        setMsg(
          `Still not in gallery results: ${deep.contentId || deep.relpath}${
            qDebounced ? "" : " (try searching)"
          }`,
        );
      }
      return;
    }
    deepLinkDone.current = true;
    setSelectedPath(match.path);
    setDeepLinkHitPath(match.path);
    window.requestAnimationFrame(() => {
      const el = document.getElementById(stillTileDomId(match));
      el?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
    window.setTimeout(() => setDeepLinkHitPath((cur) => (cur === match.path ? null : cur)), 2400);
  }, [
    deep,
    deep.contentId,
    deep.relpath,
    items,
    qDebounced,
    stillsQuery.hasNextPage,
    stillsQuery.isFetchingNextPage,
    stillsQuery.isLoading,
    stillsQuery.fetchNextPage,
  ]);

  useEffect(() => {
    if (!selected) return;
    const next = stillsHref({
      contentId: selected.content_id || null,
      relpath: stillMediaRelpath(selected) || null,
      q: qDebounced || null,
    });
    if (`${window.location.pathname}${window.location.search}` === next) return;
    window.history.replaceState(null, "", next);
  }, [selected, qDebounced]);

  useEffect(() => {
    const el = sentinelRef.current;
    const root = scrollRef.current;
    if (!el) return;
    const obs = new IntersectionObserver(
      (entries) => {
        if (!entries.some((e) => e.isIntersecting)) return;
        if (stillsQuery.hasNextPage && !stillsQuery.isFetchingNextPage) {
          void stillsQuery.fetchNextPage();
        }
      },
      { root: root || null, rootMargin: "600px 0px", threshold: 0 },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [stillsQuery.hasNextPage, stillsQuery.isFetchingNextPage, stillsQuery.fetchNextPage]);

  const invalidate = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.inputCurationRoot }),
      queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.inputCurationStills(stillsKey) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.stillTagBacklog }),
    ]);
  };

  const collectionMut = useMutation({
    mutationFn: mutateShapeFactoryInputCollection,
    onSuccess: () => void invalidate(),
  });
  const tagsMut = useMutation({
    mutationFn: mutateShapeFactoryInputStillTags,
    onSuccess: () => void invalidate(),
  });

  const tagRunMut = useMutation({
    mutationFn: enqueueShapeFactoryStillTagRun,
    onSuccess: (res) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.stillTagBacklog });
      if (res.run_id) {
        setActiveRunId(res.run_id);
        setRunEvents([]);
        eventAfterId.current = 0;
        setRunStatus(res.drain_kicked ? "running" : "queued");
        const n = res.enqueued ?? 0;
        if (res.queued_for_index_hour) {
          setMsg(`Queued ${n} for index hour · run ${res.run_id} (no GPU yet)`);
        } else if (res.drain_kicked) {
          setMsg(`Tag run ${res.run_id} · enqueued ${n} · drain kicked`);
        } else {
          setMsg(`Tag run ${res.run_id} · enqueued ${n}`);
        }
      }
    },
  });

  const drainMut = useMutation({
    mutationFn: drainShapeFactoryStillTags,
    onSuccess: (res) => {
      void invalidate();
      const result = res.result || {};
      const runs = Array.isArray(result.runs) ? result.runs : [];
      const firstRun = runs.find((r) => r && typeof r === "object" && (r as { run_id?: string }).run_id) as
        | { run_id?: string }
        | undefined;
      if (firstRun?.run_id) {
        setActiveRunId(String(firstRun.run_id));
        setRunEvents([]);
        eventAfterId.current = 0;
        setRunStatus("done");
      }
      if (res.skipped || result.skipped) {
        setMsg(`Drain skipped · ${res.reason || result.reason || "outside window"}`);
      } else if (res.sync) {
        setMsg(
          `Drain done · ${res.done_items ?? result.done_items ?? 0} items · ${res.runs_processed ?? result.runs_processed ?? 0} runs`,
        );
      } else if (res.started) {
        setMsg("Drain started (background)");
      } else {
        setMsg(`Drain: ${res.reason || "not started"}`);
      }
    },
  });

  const scheduleMut = useMutation({
    mutationFn: setShapeFactoryStillTagSchedule,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.stillTagBacklog });
      setMsg("Schedule updated");
    },
  });

  useEffect(() => {
    if (!activeRunId) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const [runRes, evRes] = await Promise.all([
          fetchShapeFactoryStillTagRun(activeRunId),
          fetchShapeFactoryStillTagEvents(activeRunId, { after_id: eventAfterId.current, limit: 100 }),
        ]);
        if (cancelled) return;
        const st = runRes.run?.status || null;
        setRunStatus(st);
        const evs = evRes.events || [];
        if (evs.length) {
          eventAfterId.current = Math.max(eventAfterId.current, ...evs.map((e) => e.id));
          setRunEvents((prev) => [...prev, ...evs].slice(-40));
        }
        if (st === "done" || st === "error" || st === "cancelled") {
          void invalidate();
        }
      } catch {
        /* keep polling */
      }
    };
    void tick();
    const id = window.setInterval(() => void tick(), 1000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [activeRunId]);

  const firstPage = stillsQuery.data?.pages?.[0];
  const totalLabel = useMemo(() => {
    const bits = [`${items.length} loaded`];
    if (firstPage?.total != null) bits.push(`${firstPage.total} in catalog`);
    if (stillsQuery.hasNextPage) bits.push("scroll for more");
    return bits.join(" · ");
  }, [items.length, firstPage?.total, stillsQuery.hasNextPage]);

  const submitUrl = selected
    ? submitHref({
        mediaRelpath: stillMediaRelpath(selected),
        origin: "gallery",
      })
    : null;

  const backlog = backlogQuery.data;
  const win = backlog?.window;
  const sch = backlog?.schedule;
  const queuedTargets = backlog?.queued_targets ?? 0;
  const queuedRuns = backlog?.queued_runs ?? 0;
  const windowLabel = !win
    ? "…"
    : !win.enabled
      ? "schedule off"
      : win.in_window
        ? "in window"
        : "outside window";

  return (
    <div className="pipeline-screen layout still-gallery">
      <PageHeader
        title="Stills"
        subtitle="Browse input stills · collections · tags · launch I2V via Submit"
        actions={
          <>
            <button
              type="button"
              className="drt-btn"
              disabled={tagRunMut.isPending}
              onClick={() =>
                void tagRunMut
                  .mutateAsync({ only_missing: true, limit: TAG_BATCH_DEFAULT })
                  .catch((e) => setMsg(e instanceof Error ? e.message : String(e)))
              }
            >
              Queue untagged ({TAG_BATCH_DEFAULT})
            </button>
            <button
              type="button"
              className="drt-btn"
              disabled={stillsQuery.isFetching}
              onClick={() =>
                void fetchShapeFactoryInputCurationStills({
                  q: qDebounced || undefined,
                  tag: tagFilter.trim() || undefined,
                  limit: PAGE,
                  offset: 0,
                  scan: true,
                })
                  .then(() => invalidate())
                  .then(() => setMsg("Catalog rescanned"))
                  .catch((e) => setMsg(e instanceof Error ? e.message : String(e)))
              }
            >
              Rescan input
            </button>
          </>
        }
      />

      {msg ? <p className="factory-muted still-gallery__msg">{msg}</p> : null}

      <div className="still-gallery__index-hour" aria-live="polite">
        <div className="still-gallery__index-hour-head">
          <strong>Index hour</strong>
          <span className="factory-muted mono">
            backlog {queuedTargets} targets · {queuedRuns} runs · {windowLabel}
            {win?.local_now ? ` · local ${win.local_now.slice(11, 16)}` : ""}
          </span>
        </div>
        <p className="factory-muted still-gallery__index-hour-hint">
          Queue anytime; Florence runs in the reserved window (or Drain now). Avoids GPU thrash with I2V.
        </p>
        <div className="still-gallery__index-hour-actions">
          <label className="still-gallery__index-hour-toggle">
            <input
              type="checkbox"
              checked={Boolean(sch?.enabled)}
              disabled={scheduleMut.isPending || backlogQuery.isLoading}
              onChange={(e) =>
                void scheduleMut
                  .mutateAsync({ enabled: e.target.checked })
                  .catch((err) => setMsg(err instanceof Error ? err.message : String(err)))
              }
            />
            Schedule enabled
          </label>
          <button
            type="button"
            className="drt-btn"
            disabled={drainMut.isPending || queuedRuns < 1}
            title="Process backlog with dry-run provider (no Comfy)"
            onClick={() =>
              void drainMut
                .mutateAsync({
                  force: true,
                  respect_schedule: false,
                  sync: true,
                  front: true,
                  max_items: TAG_BATCH_DEFAULT,
                  provider: "dry-run",
                })
                .catch((e) => setMsg(e instanceof Error ? e.message : String(e)))
            }
          >
            Drain now (dry-run)
          </button>
          <button
            type="button"
            className="drt-btn"
            disabled={drainMut.isPending || queuedRuns < 1}
            title="Force Comfy drain now (front of queue)"
            onClick={() =>
              void drainMut
                .mutateAsync({
                  force: true,
                  respect_schedule: false,
                  front: true,
                  max_items: TAG_BATCH_DEFAULT,
                  sync: false,
                })
                .catch((e) => setMsg(e instanceof Error ? e.message : String(e)))
            }
          >
            Drain now (Comfy)
          </button>
          <button
            type="button"
            className="drt-btn"
            disabled={backlogQuery.isFetching}
            onClick={() => void backlogQuery.refetch()}
          >
            Refresh
          </button>
        </div>
        {backlogQuery.error instanceof Error ? (
          <p className="factory-error">{backlogQuery.error.message}</p>
        ) : null}
      </div>

      {activeRunId ? (
        <div className="still-gallery__run" aria-live="polite">
          <div className="still-gallery__run-head">
            <span className="mono">
              Tag run {activeRunId} · {runStatus || "…"}
              {runStatus === "queued" ? " · waiting for index hour" : ""}
            </span>
            {runStatus === "done" || runStatus === "error" || runStatus === "cancelled" ? (
              <button type="button" className="drt-btn" onClick={() => setActiveRunId(null)}>
                Dismiss
              </button>
            ) : null}
          </div>
          <ul className="still-gallery__run-events">
            {runEvents.slice(-8).map((e) => (
              <li key={e.id}>
                <span className="mono">{e.kind}</span>
                {e.content_id ? ` · ${e.content_id.slice(0, 8)}…` : ""}
                {e.message ? ` — ${e.message}` : ""}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="pipeline-scroll still-gallery__scroll" ref={scrollRef}>
      <div className="still-gallery__toolbar">
        <input
          className="still-gallery__search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search path / filename…"
          aria-label="Search stills"
        />
        <input
          className="still-gallery__search still-gallery__search--tag"
          value={tagFilter}
          onChange={(e) => setTagFilter(e.target.value)}
          placeholder="Filter tag…"
          aria-label="Filter by tag"
        />
        <span className="factory-muted still-gallery__count">{totalLabel}</span>
      </div>

      <div className="still-gallery__body">
        <div className="still-gallery__main">
          {stillsQuery.isLoading ? <p className="factory-muted">Loading stills…</p> : null}
          {stillsQuery.error instanceof Error ? (
            <p className="factory-error">{stillsQuery.error.message}</p>
          ) : null}
          <div className="still-gallery__grid" role="list">
            {items.map((it) => {
              const active = selected?.path === it.path;
              const deepHit = deepLinkHitPath === it.path;
              const src = it.thumb_url || it.url;
              return (
                <button
                  key={it.path}
                  id={stillTileDomId(it)}
                  type="button"
                  role="listitem"
                  className={
                    "still-gallery__tile" +
                    (active ? " still-gallery__tile--active" : "") +
                    (deepHit ? " still-gallery__tile--deep-link" : "")
                  }
                  onClick={() => setSelectedPath(it.path)}
                  title={it.basename || it.path}
                >
                  {src ? (
                    <img className="still-gallery__thumb" src={src} alt="" loading="lazy" />
                  ) : (
                    <div className="still-gallery__thumb still-gallery__thumb--empty">No preview</div>
                  )}
                  <span className="still-gallery__tile-label">{it.basename || it.relpath}</span>
                  {(it.tags || []).length ? (
                    <span className="still-gallery__tile-tags">{(it.tags || []).slice(0, 3).join(" · ")}</span>
                  ) : null}
                </button>
              );
            })}
          </div>
          <div ref={sentinelRef} className="still-gallery__sentinel" aria-hidden="true" />
          <div className="still-gallery__pager">
            {stillsQuery.isFetchingNextPage ? (
              <span className="factory-muted">Loading more…</span>
            ) : stillsQuery.hasNextPage ? (
              <button type="button" className="drt-btn" onClick={() => void stillsQuery.fetchNextPage()}>
                Load more
              </button>
            ) : items.length ? (
              <span className="factory-muted">End of gallery</span>
            ) : null}
          </div>
        </div>

        <aside className="still-gallery__side" aria-label="Still launch pad">
          <section className="still-gallery__panel">
            <h2>Selected</h2>
            {selected ? (
              <>
                {selected.url || selected.thumb_url ? (
                  <img
                    className="still-gallery__preview"
                    src={selected.url || selected.thumb_url}
                    alt={selected.basename || ""}
                  />
                ) : null}
                <p className="mono still-gallery__path">{stillMediaRelpath(selected)}</p>
                <div className="still-gallery__actions">
                  {submitUrl ? (
                    <a className="drt-btn still-gallery__cta" href={submitUrl}>
                      Open in Submit
                    </a>
                  ) : null}
                  <button
                    type="button"
                    className="drt-btn"
                    disabled={!selected.content_id || tagRunMut.isPending}
                    onClick={() =>
                      void tagRunMut
                        .mutateAsync({
                          content_ids: [String(selected.content_id)],
                          force: true,
                          limit: 1,
                        })
                        .catch((e) => setMsg(e instanceof Error ? e.message : String(e)))
                    }
                  >
                    Queue tag
                  </button>
                  <button
                    type="button"
                    className="drt-btn"
                    disabled={!selected.content_id || tagRunMut.isPending || drainMut.isPending}
                    title="Enqueue and drain immediately (smoke)"
                    onClick={() =>
                      void tagRunMut
                        .mutateAsync({
                          content_ids: [String(selected.content_id)],
                          force: true,
                          limit: 1,
                          dry_run: true,
                          drain_now: true,
                        })
                        .catch((e) => setMsg(e instanceof Error ? e.message : String(e)))
                    }
                  >
                    Tag now (dry-run)
                  </button>
                  <a
                    className="drt-btn"
                    href={`/discovery/factory-map`}
                    title="Factory Map (attach collections to I2V families there)"
                  >
                    Factory Map
                  </a>
                </div>
                {(selected.provisional_tags || []).length ? (
                  <p className="factory-muted still-gallery__prov">
                    Auto: {(selected.provisional_tags || []).slice(0, 8).join(", ")}
                    {(selected.provisional_tags || []).length > 8 ? "…" : ""}
                  </p>
                ) : null}
                <label className="still-gallery__field">
                  <span>Tags (comma-separated)</span>
                  <div className="still-gallery__tag-row">
                    <input
                      value={tagDraft}
                      onChange={(e) => setTagDraft(e.target.value)}
                      disabled={!selected.content_id || tagsMut.isPending}
                      placeholder="kneel, portrait…"
                    />
                    <button
                      type="button"
                      className="drt-btn"
                      disabled={!selected.content_id || tagsMut.isPending}
                      onClick={() => {
                        const tags = tagDraft
                          .split(",")
                          .map((t) => t.trim())
                          .filter(Boolean);
                        void tagsMut
                          .mutateAsync({ content_id: String(selected.content_id), tags })
                          .then(() => setMsg("Tags saved"))
                          .catch((e) => setMsg(e instanceof Error ? e.message : String(e)));
                      }}
                    >
                      Save
                    </button>
                  </div>
                  {!selected.content_id ? (
                    <span className="factory-muted">No content_id in filename — tags need a sha256 in the name.</span>
                  ) : null}
                </label>
              </>
            ) : (
              <p className="factory-muted">Select a still to launch, tag, or collect.</p>
            )}
          </section>

          <section className="still-gallery__panel">
            <h2>Collections</h2>
            <div className="still-gallery__tag-row">
              <input
                value={newCollectionName}
                onChange={(e) => setNewCollectionName(e.target.value)}
                placeholder="New collection"
              />
              <button
                type="button"
                className="drt-btn"
                disabled={!newCollectionName.trim() || collectionMut.isPending}
                onClick={() =>
                  void collectionMut
                    .mutateAsync({ op: "create", name: newCollectionName.trim() })
                    .then((res) => {
                      const created = (res.collections || []).find(
                        (c) => c.name === newCollectionName.trim(),
                      );
                      if (created?.id) setCollectionId(created.id);
                      setNewCollectionName("");
                      setMsg("Collection created");
                    })
                    .catch((e) => setMsg(e instanceof Error ? e.message : String(e)))
                }
              >
                Create
              </button>
            </div>
            <select
              value={selectedCollection?.id || ""}
              onChange={(e) => setCollectionId(e.target.value)}
              aria-label="Active collection"
            >
              {collections.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name} ({(c.items || []).length})
                </option>
              ))}
            </select>
            <button
              type="button"
              className="drt-btn still-gallery__cta"
              disabled={!selected || !selectedCollection || collectionMut.isPending}
              onClick={() =>
                void collectionMut
                  .mutateAsync({
                    op: "add_item",
                    collection_id: selectedCollection?.id,
                    path: selected?.path,
                  })
                  .then(() => setMsg(`Added to ${selectedCollection?.name}`))
                  .catch((e) => setMsg(e instanceof Error ? e.message : String(e)))
              }
            >
              Add selected to collection
            </button>
            {selectedCollection ? (
              <ul className="still-gallery__collection-list">
                {(selectedCollection.items || []).slice(0, 40).map((it) => (
                  <li key={it.path}>
                    <span className="mono">{it.path.split("/").pop()}</span>
                    <button
                      type="button"
                      className="drt-btn"
                      disabled={collectionMut.isPending}
                      onClick={() =>
                        void collectionMut
                          .mutateAsync({
                            op: "remove_item",
                            collection_id: selectedCollection.id,
                            path: it.path,
                          })
                          .then(() => setMsg("Removed from collection"))
                          .catch((e) => setMsg(e instanceof Error ? e.message : String(e)))
                      }
                    >
                      Remove
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
            <p className="factory-muted">
              Attach collections to I2V families on Factory → Input curation so hourly/pools can use them.
            </p>
          </section>
        </aside>
      </div>
      </div>
    </div>
  );
}
