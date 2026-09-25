import React, { useCallback, useEffect, useMemo, useState } from "react";
import { fetchHomeSummary } from "./api";
import { PageHeader } from "./PageHeader";
import {
  queueHref,
  stillsHref,
  workbenchHref,
  workbenchHrefForMedia,
} from "./discoveryDeepLink";
import { factoryMapFamilyHref, factoryMapHourliesHref, factoryMapIndexHref } from "./factoryMapRoute";
import { HomeMediaStrip, type HomePreviewItem } from "./HomeMediaStrip";
import { HourlyHomeTeaser } from "./HourlyFactoryPanel";
import { routeHref } from "./routes";
import type {
  HomeSummaryFreshInput,
  HomeSummaryFreshOutput,
  HomeSummaryNewClip,
  HomeSummaryResponse,
  HomeSummarySection,
} from "./types";

function fileUrlFromRel(relpath?: string | null): string {
  if (!relpath) return "";
  return "/files/" + encodeURIComponent(relpath.replace(/\\/g, "/"));
}

function basename(rel?: string | null): string {
  const p = (rel || "").replace(/\\/g, "/");
  return p.split("/").pop() || p;
}

function num(n?: number | null): string {
  return typeof n === "number" && Number.isFinite(n) ? String(n) : "—";
}

type SectionPhase = "idle" | "loading" | "ready" | "error";

type SectionState<T> = {
  phase: SectionPhase;
  data: T | null;
  error: string;
  status: string;
};

function idleSection<T>(): SectionState<T> {
  return { phase: "idle", data: null, error: "", status: "" };
}

function beginLoading<T>(prev: SectionState<T>, loadingStatus: string): SectionState<T> {
  return {
    ...prev,
    phase: "loading",
    error: "",
    status: prev.data ? "Refreshing…" : loadingStatus,
  };
}

function failLoading<T>(prev: SectionState<T>, label: string, msg: string): SectionState<T> {
  return {
    ...prev,
    phase: "error",
    error: msg,
    status: `Couldn't load ${label} — ${msg}`,
  };
}

function Card({
  title,
  hint,
  children,
  footer,
  loading,
  status,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  loading?: boolean;
  status?: string | null;
}) {
  const showStatus = Boolean(loading || (status && status.trim()));
  return (
    <section className="home-card" aria-busy={loading || undefined}>
      <div className="home-card__head">
        <h2 className="home-card__title">{title}</h2>
        {hint ? <span className="home-card__hint factory-muted">{hint}</span> : null}
      </div>
      {showStatus ? (
        <div className="home-card__status" role="status" aria-live="polite">
          {loading ? <span className="home-card__spinner" aria-hidden="true" /> : null}
          <span className="home-card__status-text">{status || (loading ? "Loading…" : "")}</span>
        </div>
      ) : null}
      <div className="home-card__body">{children}</div>
      {footer ? <div className="home-card__foot">{footer}</div> : null}
    </section>
  );
}

function previewFromOutput(item: HomeSummaryFreshOutput): HomePreviewItem {
  const rel = String(item.relpath || "").trim();
  const label = basename(item.relpath || item.name) || "Output";
  const thumb =
    item.thumb_url ||
    (rel && /\.mp4$/i.test(rel) ? fileUrlFromRel(rel.replace(/\.mp4$/i, ".png")) : "") ||
    item.url ||
    "";
  const video =
    item.video_url ||
    (rel && /\.(mp4|webm|mov|mkv)$/i.test(rel) ? item.url || fileUrlFromRel(rel) : "") ||
    "";
  const jobKey = String(item.job_key || "").trim();
  const promptId = String(item.prompt_id || "").trim();
  const workbenchUrl = jobKey
    ? workbenchHref({ jobKey, promptId: promptId || null })
    : workbenchHrefForMedia({ relpath: item.relpath, name: item.name, groupId: item.group_id });
  const queueUrl =
    jobKey || promptId ? queueHref({ jobKey: jobKey || null, promptId: promptId || null }) : "";
  const rating = item.ratings?.rating_effective;
  return {
    id: String(item.group_id || item.relpath || label),
    label,
    subtitle: item.family_slug || undefined,
    thumbUrl: thumb || undefined,
    mediaKind: video ? "video" : "image",
    mediaUrl: video || item.url || thumb || undefined,
    appetiteRelpath: rel || null,
    badge: typeof rating === "number" && rating > 0 ? `★ ${rating.toFixed(rating >= 1 ? 1 : 2)}` : null,
    links: [
      { href: workbenchUrl, label: "Open in Workbench →" },
      queueUrl
        ? { href: queueUrl, label: "Open in Queue →" }
        : {
            href: "",
            label: "Queue unavailable",
            disabled: true,
            title: "No factory job mapped for this output yet",
          },
    ],
  };
}

function previewFromInput(item: HomeSummaryFreshInput): HomePreviewItem {
  const rel = String(item.relpath || "").trim();
  const cid = String(item.content_id || "").trim();
  const label = item.basename || basename(rel) || "Still";
  const url = item.thumb_url || item.url || (rel ? fileUrlFromRel(rel) : "");
  return {
    id: cid || rel || label,
    label,
    subtitle: "input",
    thumbUrl: url || undefined,
    mediaKind: "image",
    mediaUrl: url || undefined,
    appetiteRelpath: rel || null,
    defaultFacet: "source",
    links: [
      {
        href: stillsHref({ contentId: cid || null, relpath: rel || null }),
        label: "Open in Stills →",
      },
    ],
  };
}

function previewFromClip(item: HomeSummaryNewClip): HomePreviewItem {
  const clipId = String(item.clip_id || "").trim();
  const rel = String(item.media_relpath || "").trim();
  const label = String(item.label || "").trim() || item.media_basename || basename(rel) || clipId || "Clip";
  const thumb =
    item.thumb_url ||
    (rel && /\.(mp4|webm|mov|mkv)$/i.test(rel) ? fileUrlFromRel(rel.replace(/\.(mp4|webm|mov|mkv)$/i, ".png")) : "") ||
    "";
  const media = item.media_url || (rel ? fileUrlFromRel(rel) : "") || "";
  const markIn = typeof item.mark_in_s === "number" ? item.mark_in_s : null;
  const markOut = typeof item.mark_out_s === "number" ? item.mark_out_s : null;
  const dur =
    typeof item.duration_s === "number" && Number.isFinite(item.duration_s)
      ? `${item.duration_s.toFixed(1)}s`
      : null;
  return {
    id: clipId || `${rel}:${markIn ?? 0}`,
    label,
    subtitle: [item.origin, dur].filter(Boolean).join(" · ") || undefined,
    thumbUrl: thumb || undefined,
    mediaKind: media ? "video" : "image",
    mediaUrl: media || thumb || undefined,
    markInS: markIn,
    markOutS: markOut,
    // Appetite is path-keyed today (parent media), not per clip_id — omit until clip appetite exists.
    appetiteRelpath: null,
    badge: item.is_default ? "default" : dur,
    links: [
      {
        href: workbenchHrefForMedia({ relpath: rel || null, name: item.media_basename || label }),
        label: "Open in Workbench →",
      },
    ],
  };
}

const LAZY_SECTIONS: Array<{
  key: HomeSummarySection;
  loadingStatus: string;
}> = [
  { key: "rating", loadingStatus: "Loading rating session…" },
  { key: "attention", loadingStatus: "Checking attention…" },
  { key: "hourly", loadingStatus: "Loading hourlies…" },
  { key: "fresh_outputs", loadingStatus: "Loading fresh outputs…" },
  { key: "fresh_inputs", loadingStatus: "Loading fresh inputs…" },
  { key: "new_clips", loadingStatus: "Loading new clips…" },
];

export function HomeDashboard() {
  const [rating, setRating] = useState<SectionState<HomeSummaryResponse["rating"]>>(idleSection);
  const [attention, setAttention] = useState<SectionState<HomeSummaryResponse["attention"]>>(idleSection);
  const [hourly, setHourly] = useState<SectionState<HomeSummaryResponse["hourly"]>>(idleSection);
  const [fresh, setFresh] = useState<SectionState<HomeSummaryFreshOutput[]>>(idleSection);
  const [inputs, setInputs] = useState<SectionState<HomeSummaryFreshInput[]>>(idleSection);
  const [clips, setClips] = useState<SectionState<HomeSummaryNewClip[]>>(idleSection);
  const [pageError, setPageError] = useState("");
  const [outputPreview, setOutputPreview] = useState<number | null>(null);
  const [inputPreview, setInputPreview] = useState<number | null>(null);
  const [clipPreview, setClipPreview] = useState<number | null>(null);

  const applySection = useCallback((key: HomeSummarySection, payload: HomeSummaryResponse) => {
    const sectionErr = payload.errors?.[key] || "";
    if (key === "rating") {
      const err = sectionErr || payload.rating?.error || "";
      setRating({
        phase: err && !payload.rating ? "error" : "ready",
        data: payload.rating ?? null,
        error: err,
        status: err ? `Couldn't load rating — ${err}` : "",
      });
      return;
    }
    if (key === "attention") {
      const err = sectionErr || payload.attention?.error || "";
      setAttention({
        phase: err && !payload.attention ? "error" : "ready",
        data: payload.attention ?? null,
        error: err,
        status: err ? `Couldn't load attention — ${err}` : "",
      });
      return;
    }
    if (key === "hourly") {
      const err =
        sectionErr ||
        payload.errors?.shape_factory_jobs ||
        payload.errors?.hourly_schedule ||
        payload.hourly?.error ||
        payload.hourly?.schedule_error ||
        "";
      setHourly({
        phase: err && !payload.hourly ? "error" : "ready",
        data: payload.hourly ?? null,
        error: err,
        status: err && !payload.hourly ? `Couldn't load hourlies — ${err}` : "",
      });
      return;
    }
    if (key === "fresh_outputs") {
      const err = sectionErr || "";
      const rows = payload.fresh_outputs ?? [];
      setFresh({
        phase: err && !rows.length ? "error" : "ready",
        data: rows,
        error: err,
        status: err && !rows.length ? `Couldn't load fresh outputs — ${err}` : "",
      });
      return;
    }
    if (key === "fresh_inputs") {
      const err = sectionErr || "";
      const rows = payload.fresh_inputs ?? [];
      setInputs({
        phase: err && !rows.length ? "error" : "ready",
        data: rows,
        error: err,
        status: err && !rows.length ? `Couldn't load fresh inputs — ${err}` : "",
      });
      return;
    }
    if (key === "new_clips") {
      const err = sectionErr || "";
      const rows = payload.new_clips ?? [];
      setClips({
        phase: err && !rows.length ? "error" : "ready",
        data: rows,
        error: err,
        status: err && !rows.length ? `Couldn't load new clips — ${err}` : "",
      });
    }
  }, []);

  const loadSection = useCallback(
    async (key: HomeSummarySection, meta: { loadingStatus: string }) => {
      if (key === "rating") setRating((prev) => beginLoading(prev, meta.loadingStatus));
      else if (key === "attention") setAttention((prev) => beginLoading(prev, meta.loadingStatus));
      else if (key === "hourly") setHourly((prev) => beginLoading(prev, meta.loadingStatus));
      else if (key === "fresh_outputs") setFresh((prev) => beginLoading(prev, meta.loadingStatus));
      else if (key === "fresh_inputs") setInputs((prev) => beginLoading(prev, meta.loadingStatus));
      else if (key === "new_clips") setClips((prev) => beginLoading(prev, meta.loadingStatus));

      try {
        const payload = await fetchHomeSummary({ sections: [key] });
        applySection(key, payload);
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        if (key === "rating") setRating((prev) => failLoading(prev, "rating", msg));
        else if (key === "attention") setAttention((prev) => failLoading(prev, "attention", msg));
        else if (key === "hourly") setHourly((prev) => failLoading(prev, "hourlies", msg));
        else if (key === "fresh_outputs") setFresh((prev) => failLoading(prev, "fresh outputs", msg));
        else if (key === "fresh_inputs") setInputs((prev) => failLoading(prev, "fresh inputs", msg));
        else if (key === "new_clips") setClips((prev) => failLoading(prev, "new clips", msg));
      }
    },
    [applySection],
  );

  const loadAll = useCallback(async () => {
    setPageError("");
    await Promise.all(LAZY_SECTIONS.map((s) => loadSection(s.key, s)));
  }, [loadSection]);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  const anyLoading =
    rating.phase === "loading" ||
    attention.phase === "loading" ||
    hourly.phase === "loading" ||
    fresh.phase === "loading" ||
    inputs.phase === "loading" ||
    clips.phase === "loading";

  const ratingData = rating.data;
  const attentionData = attention.data;
  const hourlyData = hourly.data;
  const freshRows = fresh.data ?? [];
  const inputRows = inputs.data ?? [];
  const clipRows = clips.data ?? [];

  const outputPreviews = useMemo(() => freshRows.map(previewFromOutput), [freshRows]);
  const inputPreviews = useMemo(() => inputRows.map(previewFromInput), [inputRows]);
  const clipPreviews = useMemo(() => clipRows.map(previewFromClip), [clipRows]);

  useEffect(() => {
    if (outputPreview == null) return;
    if (!outputPreviews.length) setOutputPreview(null);
    else if (outputPreview >= outputPreviews.length) setOutputPreview(outputPreviews.length - 1);
  }, [outputPreview, outputPreviews.length]);

  useEffect(() => {
    if (inputPreview == null) return;
    if (!inputPreviews.length) setInputPreview(null);
    else if (inputPreview >= inputPreviews.length) setInputPreview(inputPreviews.length - 1);
  }, [inputPreview, inputPreviews.length]);

  useEffect(() => {
    if (clipPreview == null) return;
    if (!clipPreviews.length) setClipPreview(null);
    else if (clipPreview >= clipPreviews.length) setClipPreview(clipPreviews.length - 1);
  }, [clipPreview, clipPreviews.length]);

  const missingTotal = attentionData?.missing_sources_total ?? 0;
  const quarantine = attentionData?.quarantine;
  const quarantineCount = quarantine?.count ?? 0;
  const quarantineEntries = quarantine?.entries ?? [];
  const health = attentionData?.library_health;
  const healthIssues = health
    ? Object.entries(health).filter(([, v]) => typeof v === "number" && v > 0)
    : [];
  const attentionFactoryHref =
    quarantineCount > 0 ? `${factoryMapIndexHref()}#sfmap-quarantine` : factoryMapIndexHref();

  return (
    <div className="layout home-screen">
      <PageHeader
        title="Home"
        subtitle="Resume the loop — rate what's queued, triage fresh output, keep the factory fed."
        actions={
          <button type="button" className="drt-btn" disabled={anyLoading} onClick={() => void loadAll()}>
            {anyLoading ? "Refreshing…" : "Refresh"}
          </button>
        }
      />

      {pageError ? <p className="drt-err">{pageError}</p> : null}

      <div className="home-grid">
        <Card
          title="Continue rating"
          hint="Bootstrap the heuristics with a few quick calls"
          loading={rating.phase === "loading"}
          status={rating.status}
          footer={
            <a className="home-cta" href={routeHref("rate")}>
              Open rate queue →
            </a>
          }
        >
          {ratingData || rating.phase === "ready" ? (
            <>
              <div className="home-stat-row">
                <div className="home-stat">
                  <span className="home-stat__num">{num(ratingData?.unrated_videos)}</span>
                  <span className="home-stat__label">unrated videos</span>
                </div>
                <div className="home-stat">
                  <span className="home-stat__num">{num(ratingData?.selected)}</span>
                  <span className="home-stat__label">in next session</span>
                </div>
              </div>
              <div className="home-buckets" aria-label="Session mix">
                <span className="drq-bucket drq-bucket--down" title="Quick rejects">
                  ↓ {num(ratingData?.buckets?.easy_down)}
                </span>
                <span className="drq-bucket drq-bucket--up" title="Likely keepers">
                  ↑ {num(ratingData?.buckets?.easy_up)}
                </span>
                <span className="drq-bucket drq-bucket--mid" title="Middle band">
                  ~ {num(ratingData?.buckets?.middle)}
                </span>
              </div>
            </>
          ) : rating.phase === "loading" ? (
            <p className="factory-muted home-card__placeholder">Waiting for rating session…</p>
          ) : null}
        </Card>

        <Card
          title="Follow-up"
          hint="Videos marked to fix, look at, or vary — corollary to appetite"
          footer={
            <a className="home-cta" href={routeHref("pools")}>
              Open marked pile →
            </a>
          }
        >
          <p className="factory-muted" style={{ margin: 0 }}>
            Refine (fix) · Investigate (look closer) · Advance (variations) · Park (later). Mark from
            Workbench, Library, or Rating; come back here to work the pile.
          </p>
        </Card>

        <Card
          title="Remove review"
          hint="Permanently delete outputs marked Remove"
          footer={
            <a className="home-cta" href={routeHref("remove")}>
              Open remove review →
            </a>
          }
        >
          <p className="factory-muted" style={{ margin: 0 }}>
            Hidden from lists and factory until you restore appetite or delete. Delete only when
            nothing depends on the clip — not the same as Follow-up Retire (trash).
          </p>
        </Card>

        <Card
          title="Needs attention"
          hint="Fix these so generation + provenance stay clean"
          loading={attention.phase === "loading"}
          status={attention.status}
          footer={
            <a className="home-cta" href={attentionFactoryHref}>
              {quarantineCount > 0 ? "Open quarantine on factory map →" : "Open factory map →"}
            </a>
          }
        >
          {attentionData || attention.phase === "ready" ? (
            <>
              {quarantineCount > 0 ? (
                <>
                  <p className="home-attn home-attn--warn">
                    {quarantineCount} quarantined workflow{quarantineCount === 1 ? "" : "s"} blocking
                    factory generate/submit.
                  </p>
                  <ul className="home-fam-list home-quarantine-list">
                    {quarantineEntries.slice(0, 8).map((e) => {
                      const name = e.workflow_name || e.workflow_path || "workflow";
                      const reasons = (e.reasons || []).filter(Boolean).join(", ");
                      return (
                        <li key={String(e.workflow_path || e.workflow_name || name)}>
                          <span className="home-quarantine-list__name" title={String(e.workflow_path || name)}>
                            {name}
                          </span>
                          <span className="home-fam-count factory-muted">
                            {[e.category, reasons].filter(Boolean).join(" · ") || "quarantined"}
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                  {quarantineCount > quarantineEntries.slice(0, 8).length ? (
                    <p className="factory-muted home-quarantine-list__more">
                      +{quarantineCount - Math.min(8, quarantineEntries.length)} more on factory map
                    </p>
                  ) : null}
                </>
              ) : quarantine?.error ? (
                <p className="home-attn home-attn--warn">
                  Couldn't read quarantine registry — {quarantine.error}
                </p>
              ) : (
                <p className="home-attn home-attn--ok">No quarantined workflows.</p>
              )}
              {missingTotal > 0 ? (
                <p className="home-attn home-attn--warn">
                  {missingTotal} source{missingTotal === 1 ? "" : "s"} missing across{" "}
                  {attentionData?.families?.length ?? 0} famil
                  {(attentionData?.families?.length ?? 0) === 1 ? "y" : "ies"}.
                </p>
              ) : (
                <p className="home-attn home-attn--ok">No missing sources detected.</p>
              )}
              {attentionData?.families && attentionData.families.length > 0 ? (
                <ul className="home-fam-list">
                  {attentionData.families.map((f) => (
                    <li key={f.family_slug}>
                      <a href={factoryMapFamilyHref(f.family_slug || "")}>{f.family_slug}</a>
                      <span className="home-fam-count mono">{f.missing}</span>
                    </li>
                  ))}
                </ul>
              ) : null}
              {healthIssues.length > 0 ? (
                <p className="home-attn factory-muted">
                  Index health:{" "}
                  {healthIssues.map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(" · ")}
                </p>
              ) : null}
            </>
          ) : attention.phase === "loading" ? (
            <p className="factory-muted home-card__placeholder">Waiting for attention check…</p>
          ) : null}
        </Card>

        <Card
          title="Hourlies"
          hint="Schedule, next picks, and chain backlogs live in Factory"
          loading={hourly.phase === "loading"}
          status={hourly.status}
          footer={
            <a className="home-cta" href={factoryMapHourliesHref()}>
              Open Factory Hourlies →
            </a>
          }
        >
          {hourlyData || hourly.phase === "ready" ? (
            <HourlyHomeTeaser
              schedule={hourlyData?.schedule ?? null}
              nextSample={hourlyData?.next_sample}
              steer={hourlyData?.steer ?? null}
            />
          ) : hourly.phase === "loading" ? (
            <p className="factory-muted home-card__placeholder">Waiting for hourly schedule…</p>
          ) : null}
        </Card>
      </div>

      <Card
        title="Fresh outputs"
        hint="Newest indexed results — click a thumb to preview"
        loading={fresh.phase === "loading"}
        status={fresh.status}
        footer={
          <a className="home-cta" href={routeHref("workbench")}>
            Open Workbench →
          </a>
        }
      >
        {fresh.phase === "loading" && !outputPreviews.length ? (
          <p className="factory-muted home-card__placeholder">Waiting for fresh outputs…</p>
        ) : (
          <HomeMediaStrip
            items={outputPreviews}
            emptyLabel="No indexed outputs yet."
            previewIndex={outputPreview}
            onPreviewIndex={setOutputPreview}
          />
        )}
      </Card>

      <Card
        title="Fresh input"
        hint="Newest Comfy input stills — click a thumb to preview"
        loading={inputs.phase === "loading"}
        status={inputs.status}
        footer={
          <a className="home-cta" href={routeHref("stills")}>
            Open stills →
          </a>
        }
      >
        {inputs.phase === "loading" && !inputPreviews.length ? (
          <p className="factory-muted home-card__placeholder">Waiting for fresh inputs…</p>
        ) : (
          <HomeMediaStrip
            items={inputPreviews}
            emptyLabel="No input stills in the catalog yet."
            previewIndex={inputPreview}
            onPreviewIndex={setInputPreview}
          />
        )}
      </Card>

      <Card
        title="New clips"
        hint="Newest clip bookmarks — click a thumb to preview"
        loading={clips.phase === "loading"}
        status={clips.status}
        footer={
          <a className="home-cta" href={routeHref("clips")}>
            Open clips →
          </a>
        }
      >
        {clips.phase === "loading" && !clipPreviews.length ? (
          <p className="factory-muted home-card__placeholder">Waiting for new clips…</p>
        ) : (
          <HomeMediaStrip
            items={clipPreviews}
            emptyLabel="No clips bookmarked yet."
            previewIndex={clipPreview}
            onPreviewIndex={setClipPreview}
          />
        )}
      </Card>
    </div>
  );
}
