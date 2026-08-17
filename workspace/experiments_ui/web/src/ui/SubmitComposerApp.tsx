import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  composeSubmitAdvance,
  listShapeFactoryClipsLibrary,
  mintIdentityStill,
  type IdentityStillCandidate,
  type IdentityStillMintTarget,
  type ShapeFactoryClip,
} from "./api";
import { ClipBookmarksRail } from "./ClipBookmarksRail";
import {
  clipsLibraryHref,
  discoveryLibraryHref,
  hasSubmitIntent,
  parseSubmitDeepLink,
  submitOriginHref,
  workbenchHref,
} from "./discoveryDeepLink";
import { PageHeader } from "./PageHeader";
import {
  invalidateIdentityStill,
  loadFamiliesBootstrap,
  loadIdentityStillCandidates,
  peekFamiliesBootstrap,
  peekIdentityStill,
  type FamiliesBootstrap,
} from "./shapeFactorySessionCache";
import { isExtendFamilyOption, pickDefaultExtendFamily } from "./submitFamily";
import type { ShapeFactoryMapQueueOverrides, WorkProductFamilyOption } from "./types";
import { VideoTrimControls, type VideoTrimPlaybackMode } from "./VideoTrimControls";
import { useTrimPlaybackEnforcement } from "./useTrimPlayback";
import { marksToVhsWindow } from "./workProductTrim";

type RowLayout = "split" | "stacked";

const LAYOUT_KEY = "submit-composer-row-layout";

function loadLayout(): RowLayout {
  try {
    const v = localStorage.getItem(LAYOUT_KEY);
    if (v === "stacked" || v === "split") return v;
  } catch {
    /* ignore */
  }
  return "split";
}

function persistLayout(layout: RowLayout) {
  try {
    localStorage.setItem(LAYOUT_KEY, layout);
  } catch {
    /* ignore */
  }
}

function filesUrl(relpath: string): string {
  return "/files/" + encodeURIComponent(relpath.replace(/\\/g, "/"));
}

function thumbUrlForMedia(relpath: string): string | null {
  const norm = relpath.replace(/\\/g, "/");
  if (/\.(mp4|webm|mov|mkv)$/i.test(norm)) {
    return filesUrl(norm.replace(/\.(mp4|webm|mov|mkv)$/i, ".png"));
  }
  if (/\.(png|jpe?g|webp|gif)$/i.test(norm)) return filesUrl(norm);
  return null;
}

function formatTc(s: number): string {
  if (!Number.isFinite(s) || s < 0) return "0:00";
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${String(sec).padStart(2, "0")}`;
}

function stepToRouteFlags(step: string | null): { extend: boolean; vary: boolean; derive: boolean } {
  const s = String(step || "").trim().toLowerCase();
  if (s === "advance.vary" || s === "vary") return { extend: false, vary: true, derive: false };
  if (s === "advance.derive" || s === "derive") return { extend: false, vary: false, derive: true };
  return { extend: true, vary: false, derive: false };
}

function basenamePath(path: string): string {
  const norm = String(path || "").replace(/\\/g, "/");
  const parts = norm.split("/").filter(Boolean);
  return parts[parts.length - 1] || norm || "—";
}

function familyShapeId(families: WorkProductFamilyOption[], slug: string): string | null {
  const hit = families.find((f) => f.slug === slug);
  const sid = String(hit?.shape_id || "").trim();
  return sid || null;
}

type ConstructionReady = {
  ok: boolean;
  label: string;
  detail: string | null;
};

function SubmitConstructionPreview({
  routes,
  useLabel,
  useWindow,
  vhs,
  vhsWarning,
  identity,
  preferredWhen,
  origin,
  fromJob,
  ready,
}: {
  routes: { kind: string; family: string; shapeId: string | null }[];
  useLabel: string;
  useWindow: string | null;
  vhs: { skip: number; cap: number } | null;
  vhsWarning: string | null;
  identity: {
    mode: "off" | "loading" | "not_required" | "needed" | "set";
    path: string;
    thumbUrl: string | null;
  };
  preferredWhen: "now" | "later";
  origin: string | null;
  fromJob: string | null;
  ready: ConstructionReady;
}) {
  const identityThumb =
    identity.mode === "set" && identity.thumbUrl ? (
      <img className="submit-composer__construction-ident-thumb" src={identity.thumbUrl} alt="" loading="lazy" />
    ) : null;
  const identityValue =
    identity.mode === "off"
      ? "—"
      : identity.mode === "loading"
        ? "Loading…"
        : identity.mode === "not_required"
          ? "Not required"
          : identity.mode === "needed"
            ? "Needed — pick or mint"
            : basenamePath(identity.path);

  return (
    <div className="submit-composer__construction" aria-label="Construction preview">
      <div className="submit-composer__construction-head">
        <span className="work-product-quick-queue__label">Construction</span>
        <span
          className={`work-product-badge submit-composer__ready${
            ready.ok ? " submit-composer__ready--ok" : " submit-composer__ready--blocked"
          }`}
          title={ready.detail || ready.label}
        >
          {ready.label}
        </span>
      </div>
      <div className="work-product-details__chips submit-composer__construction-chips">
        {routes.length ? (
          routes.map((r) => (
            <span
              key={`${r.kind}:${r.family}`}
              className="work-product-badge"
              title={r.shapeId ? `${r.kind} · shape ${r.shapeId}` : r.kind}
            >
              {r.kind}@{r.family || "?"}
              {r.shapeId ? ` · ${r.shapeId}` : ""}
            </span>
          ))
        ) : (
          <span className="work-product-badge">no route</span>
        )}
        <span
          className={`work-product-badge ${
            preferredWhen === "now" ? "work-product-badge--front" : "work-product-badge--pending"
          }`}
          title="Intended priority (Now / Later buttons commit)"
        >
          {preferredWhen === "now" ? "now" : "later"}
        </span>
      </div>
      <dl className="submit-composer__construction-list">
        <div className="submit-composer__construction-row">
          <dt>Use</dt>
          <dd title={useWindow || useLabel}>
            {useLabel}
            {useWindow ? <span className="submit-composer__construction-sub"> · {useWindow}</span> : null}
          </dd>
        </div>
        <div className="submit-composer__construction-row">
          <dt>VHS</dt>
          <dd title={vhsWarning || undefined}>
            {vhs ? (
              <>
                skip {vhs.skip}
                {vhs.cap > 0 ? ` · cap ${vhs.cap}` : ""}
              </>
            ) : (
              "—"
            )}
            {vhsWarning ? <span className="submit-composer__construction-warn"> · {vhsWarning}</span> : null}
          </dd>
        </div>
        {identity.mode !== "off" ? (
          <div className="submit-composer__construction-row">
            <dt>Identity</dt>
            <dd className="submit-composer__construction-ident" title={identity.path || undefined}>
              {identityThumb}
              <span>{identityValue}</span>
            </dd>
          </div>
        ) : null}
        {origin || fromJob ? (
          <div className="submit-composer__construction-row">
            <dt>Context</dt>
            <dd title={[origin && `from ${origin}`, fromJob && `job ${fromJob}`].filter(Boolean).join(" · ")}>
              {[origin ? `from ${origin}` : null, fromJob ? fromJob : null].filter(Boolean).join(" · ")}
            </dd>
          </div>
        ) : null}
      </dl>
    </div>
  );
}

export function SubmitComposerApp() {
  const intent = useMemo(() => parseSubmitDeepLink(), []);
  const initialRoutes = useMemo(() => stepToRouteFlags(intent.step), [intent.step]);
  const cachedFamiliesBoot = useMemo(() => peekFamiliesBootstrap(), []);
  const [layout, setLayout] = useState<RowLayout>(() => loadLayout());
  const [mediaRelpath, setMediaRelpath] = useState(intent.mediaRelpath || "");
  const [clipId, setClipId] = useState(intent.clipId || "");
  const [markIn, setMarkIn] = useState<number | null>(intent.markIn);
  const [markOut, setMarkOut] = useState<number | null>(intent.markOut);
  const [activeClip, setActiveClip] = useState<ShapeFactoryClip | null>(null);
  const [videoDuration, setVideoDuration] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [trimMode, setTrimMode] = useState<VideoTrimPlaybackMode>("repeat");
  const videoRef = useRef<HTMLVideoElement | null>(null);

  const duration =
    videoDuration > 0
      ? videoDuration
      : // Never use clip.duration_s here — that is the bookmark span (out−in), not media length.
        // Until <video> metadata loads, span at least to markOut so trim handles remain usable.
        Math.max(markOut ?? 0, markIn ?? 0, 0);
  const fps = 18;

  const [families, setFamilies] = useState<WorkProductFamilyOption[]>(
    () => cachedFamiliesBoot?.families || [],
  );
  const [extendFamilyRows, setExtendFamilyRows] = useState<WorkProductFamilyOption[]>(
    () => cachedFamiliesBoot?.extend_families || cachedFamiliesBoot?.families || [],
  );
  const [varyFamilyRows, setVaryFamilyRows] = useState<WorkProductFamilyOption[]>(
    () => cachedFamiliesBoot?.vary_families || cachedFamiliesBoot?.families || [],
  );
  const [deriveFamilyRows, setDeriveFamilyRows] = useState<WorkProductFamilyOption[]>(
    () => cachedFamiliesBoot?.derive_families || cachedFamiliesBoot?.families || [],
  );
  const [extendOn, setExtendOn] = useState(initialRoutes.extend);
  const [varyOn, setVaryOn] = useState(initialRoutes.vary);
  const [deriveOn, setDeriveOn] = useState(initialRoutes.derive);
  const [extendFamily, setExtendFamily] = useState(() => {
    if (intent.family) return intent.family;
    if (!cachedFamiliesBoot) return "";
    const pool = cachedFamiliesBoot.extend_families?.length
      ? cachedFamiliesBoot.extend_families
      : cachedFamiliesBoot.families;
    return pickDefaultExtendFamily(
      pool,
      cachedFamiliesBoot.extend_family_defaults,
      intent.family,
      intent.mediaRelpath,
    );
  });
  const [varyFamily, setVaryFamily] = useState(() => {
    if (intent.family) return intent.family;
    if (!cachedFamiliesBoot) return "";
    const pool = cachedFamiliesBoot.extend_families?.length
      ? cachedFamiliesBoot.extend_families
      : cachedFamiliesBoot.families;
    return (
      pickDefaultExtendFamily(
        pool,
        cachedFamiliesBoot.extend_family_defaults,
        intent.family,
        intent.mediaRelpath,
      ) || ""
    );
  });
  const [deriveFamily, setDeriveFamily] = useState(() => {
    if (intent.family) return intent.family;
    if (!cachedFamiliesBoot) return "";
    const pool = cachedFamiliesBoot.extend_families?.length
      ? cachedFamiliesBoot.extend_families
      : cachedFamiliesBoot.families;
    return (
      pickDefaultExtendFamily(
        pool,
        cachedFamiliesBoot.extend_family_defaults,
        intent.family,
        intent.mediaRelpath,
      ) || ""
    );
  });

  const applyFamiliesBoot = useCallback(
    (boot: FamiliesBootstrap) => {
      const rows = boot.families || [];
      const extendRows = boot.extend_families?.length ? boot.extend_families : rows;
      const varyRows = boot.vary_families?.length ? boot.vary_families : rows;
      const deriveRows = boot.derive_families?.length ? boot.derive_families : rows;
      const defaults = boot.extend_family_defaults || {};
      setFamilies(rows);
      setExtendFamilyRows(extendRows);
      setVaryFamilyRows(varyRows);
      setDeriveFamilyRows(deriveRows);
      const extendDefault = pickDefaultExtendFamily(
        extendRows,
        defaults,
        intent.family,
        mediaRelpath || intent.mediaRelpath,
      );
      const seedFamily = String(intent.family || "").trim() || extendDefault;
      setExtendFamily((prev) => {
        const prevOk = Boolean(prev) && extendRows.some((f) => f.slug === prev && isExtendFamilyOption(f));
        return prevOk ? prev : extendDefault;
      });
      setVaryFamily((prev) => prev || seedFamily);
      setDeriveFamily((prev) => prev || seedFamily);
    },
    [intent.family, intent.mediaRelpath, mediaRelpath],
  );

  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [lastJobKey, setLastJobKey] = useState<string | null>(null);
  const [preferredWhen, setPreferredWhen] = useState<"now" | "later">(
    () => (intent.when === "now" || intent.when === "later" ? intent.when : "later"),
  );

  const cachedIdentity =
    intent.mediaRelpath && (initialRoutes.extend || intent.family)
      ? peekIdentityStill({
          relpath: intent.mediaRelpath,
          family_slug: intent.family || undefined,
          job_key: intent.fromJob || undefined,
        })
      : null;

  const [identityNeeded, setIdentityNeeded] = useState(() => Boolean(cachedIdentity?.needed));
  const [identityLoading, setIdentityLoading] = useState(false);
  const [identityCandidates, setIdentityCandidates] = useState<IdentityStillCandidate[]>(
    () => (Array.isArray(cachedIdentity?.candidates) ? cachedIdentity!.candidates! : []),
  );
  const [identityMintTargets, setIdentityMintTargets] = useState<IdentityStillMintTarget[]>(
    () => (Array.isArray(cachedIdentity?.mint_targets) ? cachedIdentity!.mint_targets! : []),
  );
  const [identitySelectedPath, setIdentitySelectedPath] = useState(intent.identity || "");
  const [identitySelectedId, setIdentitySelectedId] = useState("");
  const [identityMintBusy, setIdentityMintBusy] = useState(false);

  const playUrl = mediaRelpath.trim() ? filesUrl(mediaRelpath.trim()) : null;
  const posterUrl = mediaRelpath.trim() ? thumbUrlForMedia(mediaRelpath.trim()) : null;
  const mediaKey = mediaRelpath.trim() || "submit-empty";

  useTrimPlaybackEnforcement(videoRef, {
    mediaKey,
    markIn,
    markOut,
    mode: trimMode,
    enabled: Boolean(playUrl),
  });

  useEffect(() => {
    setVideoDuration(0);
    setCurrentTime(0);
  }, [mediaKey]);

  // Load families (session cache first; soft-refresh in background)
  useEffect(() => {
    let cancelled = false;
    const cached = peekFamiliesBootstrap();
    if (cached) applyFamiliesBoot(cached);
    void loadFamiliesBootstrap()
      .then((boot) => {
        if (cancelled) return;
        applyFamiliesBoot(boot);
      })
      .catch(() => {
        /* surface on submit */
      });
    return () => {
      cancelled = true;
    };
  }, [applyFamiliesBoot]);

  // Resolve clip_id → marks / media (skip when deep-link already has a window and no clip to resolve)
  useEffect(() => {
    const id = clipId.trim();
    if (!id) return;
    let cancelled = false;
    void (async () => {
      try {
        const lib = await listShapeFactoryClipsLibrary({ q: id, limit: 40 });
        const hit =
          (lib.clips || []).find((c) => c.clip_id === id) ||
          (lib.clips || []).find((c) => (c.clip_id || "").startsWith(id));
        if (cancelled || !hit) return;
        setActiveClip(hit);
        if (hit.media_relpath) setMediaRelpath(hit.media_relpath);
        setMarkIn(hit.mark_in_s);
        setMarkOut(hit.mark_out_s);
      } catch {
        /* keep deep-link marks */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [clipId]);

  // Identity candidates — only when Extend is checked
  useEffect(() => {
    const rel = mediaRelpath.trim();
    if (!extendOn || !rel || !extendFamily) {
      setIdentityNeeded(false);
      setIdentityCandidates([]);
      setIdentityMintTargets([]);
      return;
    }
    let cancelled = false;
    const opts = {
      relpath: rel,
      family_slug: extendFamily,
      job_key: intent.fromJob || undefined,
    };
    const cached = peekIdentityStill(opts);
    if (cached) {
      const needed = Boolean(cached.needed);
      setIdentityNeeded(needed);
      const cands = Array.isArray(cached.candidates) ? cached.candidates : [];
      setIdentityCandidates(cands);
      setIdentityMintTargets(Array.isArray(cached.mint_targets) ? cached.mint_targets : []);
      if (needed) {
        if (intent.identity && cands.some((c) => c.path === intent.identity)) {
          setIdentitySelectedPath(intent.identity);
          setIdentitySelectedId(cands.find((c) => c.path === intent.identity)?.id || "");
        } else {
          const rec = cands.find((c) => c.id === cached.recommended_id) || cands[0];
          setIdentitySelectedPath(rec?.path || intent.identity || "");
          setIdentitySelectedId(rec?.id || "");
        }
      }
      setIdentityLoading(false);
    } else {
      setIdentityLoading(true);
    }
    void loadIdentityStillCandidates(opts)
      .then((res) => {
        if (cancelled) return;
        const needed = Boolean(res.needed);
        setIdentityNeeded(needed);
        const cands = Array.isArray(res.candidates) ? res.candidates : [];
        setIdentityCandidates(cands);
        setIdentityMintTargets(Array.isArray(res.mint_targets) ? res.mint_targets : []);
        if (needed) {
          if (intent.identity && cands.some((c) => c.path === intent.identity)) {
            setIdentitySelectedPath(intent.identity);
            setIdentitySelectedId(cands.find((c) => c.path === intent.identity)?.id || "");
          } else {
            const rec = cands.find((c) => c.id === res.recommended_id) || cands[0];
            setIdentitySelectedPath(rec?.path || intent.identity || "");
            setIdentitySelectedId(rec?.id || "");
          }
        } else if (!intent.identity) {
          setIdentitySelectedPath("");
          setIdentitySelectedId("");
        }
      })
      .catch(() => {
        if (cancelled) return;
        if (!cached) {
          setIdentityNeeded(false);
          setIdentityCandidates([]);
          setIdentityMintTargets([]);
        }
      })
      .finally(() => {
        if (!cancelled) setIdentityLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [extendOn, extendFamily, mediaRelpath, intent.identity, intent.fromJob]);

  const windowOk =
    markIn != null &&
    markOut != null &&
    Number.isFinite(markIn) &&
    Number.isFinite(markOut) &&
    markOut > markIn + 0.05;

  const anyRoute = extendOn || varyOn || deriveOn;
  const canSubmit =
    Boolean(mediaRelpath.trim()) &&
    anyRoute &&
    (!extendOn || Boolean(extendFamily)) &&
    (!varyOn || Boolean(varyFamily)) &&
    (!deriveOn || Boolean(deriveFamily)) &&
    windowOk &&
    !busy &&
    !(extendOn && identityLoading) &&
    !(extendOn && identityNeeded && !identitySelectedPath);

  const familyOpts = useMemo(() => {
    const rows = [...varyFamilyRows];
    for (const slug of [extendFamily, varyFamily, deriveFamily]) {
      if (slug && !rows.some((f) => f.slug === slug)) rows.unshift({ slug });
    }
    return rows.length ? rows : families;
  }, [varyFamilyRows, families, extendFamily, varyFamily, deriveFamily]);

  /** Prefer server-partitioned extend set; fall back to client filter. */
  const extendFamilyOpts = useMemo(() => {
    const rows = [...(extendFamilyRows.length ? extendFamilyRows : families.filter(isExtendFamilyOption))];
    if (extendFamily && !rows.some((f) => f.slug === extendFamily)) {
      const hit = families.find((f) => f.slug === extendFamily) || extendFamilyRows.find((f) => f.slug === extendFamily);
      rows.unshift(hit || { slug: extendFamily });
    }
    return rows.length ? rows : familyOpts;
  }, [extendFamilyRows, families, extendFamily, familyOpts]);

  const deriveFamilyOpts = useMemo(() => {
    const rows = [...(deriveFamilyRows.length ? deriveFamilyRows : familyOpts)];
    if (deriveFamily && !rows.some((f) => f.slug === deriveFamily)) {
      rows.unshift({ slug: deriveFamily });
    }
    return rows;
  }, [deriveFamilyRows, familyOpts, deriveFamily]);

  const buildOverrides = useCallback((): {
    overrides?: ShapeFactoryMapQueueOverrides;
    warning: string | null;
  } => {
    if (!windowOk || markIn == null || markOut == null) return { warning: "Set mark in/out or select a clip" };
    const mediaDur =
      videoRef.current && Number.isFinite(videoRef.current.duration) && videoRef.current.duration > 0
        ? videoRef.current.duration
        : videoDuration > 0
          ? videoDuration
          : 0;
    const win = marksToVhsWindow(markIn, markOut, mediaDur, fps > 0 ? fps : 18, null);
    const overrides: ShapeFactoryMapQueueOverrides = {
      parameters: {
        // Seconds are authoritative — backend probes real fps/duration and derives skip/cap.
        mark_in: markIn,
        mark_out: markOut,
        skip_first_frames: win.skip_first_frames,
        frame_load_cap: win.frame_load_cap,
      },
    };
    if (activeClip?.clip_id || clipId) overrides.source_clip_id = activeClip?.clip_id || clipId;
    return { overrides, warning: win.warning };
  }, [activeClip?.clip_id, clipId, fps, markIn, markOut, videoDuration, windowOk]);

  const submit = async (when: "now" | "later") => {
    if (!canSubmit) return;
    setPreferredWhen(when);
    setBusy(true);
    setMsg(null);
    setLastJobKey(null);
    try {
      const { overrides, warning } = buildOverrides();
      if (!overrides) {
        setMsg(warning || "Need a clip window");
        return;
      }
      const routes = [];
      if (extendOn && extendFamily) {
        routes.push({
          stepId: "advance.extend",
          family: extendFamily,
          identityAnchor: identitySelectedPath || null,
        });
      }
      if (varyOn && varyFamily) {
        routes.push({ stepId: "advance.vary", family: varyFamily });
      }
      if (deriveOn && deriveFamily) {
        routes.push({ stepId: "advance.derive", family: deriveFamily });
      }
      if (!routes.length) {
        setMsg("Select Extend, Vary, and/or Derive");
        return;
      }
      const result = await composeSubmitAdvance({
        mediaRelpath: mediaRelpath.trim(),
        when,
        routes,
        overrides,
        jobKey: intent.fromJob,
      });
      setLastJobKey(result.jobKeys[0] || null);
      setMsg([result.message, warning].filter(Boolean).join(" · "));
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const mintIdentity = async (target: IdentityStillMintTarget) => {
    if (identityMintBusy || busy) return;
    setIdentityMintBusy(true);
    setMsg(null);
    try {
      const res = await mintIdentityStill({
        video_relpath: target.video_relpath,
        video_path: target.video_path,
        at: target.at || "start",
      });
      const cand = res.candidate;
      if (cand?.path) {
        invalidateIdentityStill({
          relpath: mediaRelpath.trim(),
          family_slug: extendFamily || undefined,
          job_key: intent.fromJob || undefined,
        });
        setIdentityCandidates((prev) => {
          if (prev.some((c) => c.id === cand.id || c.path === cand.path)) return prev;
          return [cand, ...prev];
        });
        setIdentitySelectedPath(cand.path);
        setIdentitySelectedId(cand.id);
        setMsg(`Minted identity still · ${cand.relpath || cand.path}`);
      }
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setIdentityMintBusy(false);
    }
  };

  const familySelect = (
    value: string,
    onChange: (slug: string) => void,
    label: string,
    title: string,
    opts: WorkProductFamilyOption[] = familyOpts,
  ) => (
    <label className="work-product-quick-queue__family-wrap">
      <span className="work-product-quick-queue__family-label">{label}</span>
      <select
        className="work-product-quick-queue__family"
        value={value}
        disabled={busy || !opts.length}
        aria-label={`${label} target family`}
        title={title}
        onChange={(e) => onChange(e.target.value)}
      >
        {opts.length === 0 ? <option value="">Loading…</option> : null}
        {opts.map((f) => (
          <option key={f.slug} value={f.slug}>
            {f.slug}
          </option>
        ))}
      </select>
    </label>
  );

  const hasIntent = hasSubmitIntent({
    mediaRelpath: mediaRelpath || intent.mediaRelpath,
    clipId: clipId || intent.clipId,
    fromJob: intent.fromJob,
  });
  const originBack = useMemo(
    () =>
      submitOriginHref(intent.origin, {
        mediaRelpath: mediaRelpath || intent.mediaRelpath,
        clipId: clipId || activeClip?.clip_id || intent.clipId,
        fromJob: intent.fromJob,
      }),
    [activeClip?.clip_id, clipId, intent.clipId, intent.fromJob, intent.mediaRelpath, intent.origin, mediaRelpath],
  );

  const constructionPreview = useMemo(() => {
    const routes: { kind: string; family: string; shapeId: string | null }[] = [];
    if (extendOn) {
      routes.push({
        kind: "Extend",
        family: extendFamily || "",
        shapeId: familyShapeId(families, extendFamily),
      });
    }
    if (varyOn) {
      routes.push({
        kind: "Vary",
        family: varyFamily || "",
        shapeId: familyShapeId(families, varyFamily),
      });
    }
    if (deriveOn) {
      routes.push({
        kind: "Derive",
        family: deriveFamily || "",
        shapeId: familyShapeId(families, deriveFamily),
      });
    }

    const { overrides, warning } = buildOverrides();
    const params = (overrides?.parameters || {}) as Record<string, unknown>;
    const vhs =
      overrides && windowOk
        ? {
            skip: Number(params.skip_first_frames ?? 0) || 0,
            cap: Number(params.frame_load_cap ?? 0) || 0,
          }
        : null;

    const useLabel = activeClip
      ? `Clip · ${activeClip.label || activeClip.clip_id}`
      : windowOk
        ? "Scrubber window"
        : "No Use window";
    const useWindow =
      windowOk && markIn != null && markOut != null ? `${formatTc(markIn)}–${formatTc(markOut)}` : null;

    let identityMode: "off" | "loading" | "not_required" | "needed" | "set" = "off";
    if (extendOn) {
      if (identityLoading) identityMode = "loading";
      else if (!identityNeeded) identityMode = "not_required";
      else if (identitySelectedPath) identityMode = "set";
      else identityMode = "needed";
    }
    const identityCand =
      identityCandidates.find((c) => c.id === identitySelectedId || c.path === identitySelectedPath) || null;

    const blockers: string[] = [];
    if (!mediaRelpath.trim()) blockers.push("need media");
    if (!anyRoute) blockers.push("select a route");
    if (extendOn && !extendFamily) blockers.push("Extend family");
    if (varyOn && !varyFamily) blockers.push("Vary family");
    if (deriveOn && !deriveFamily) blockers.push("Derive family");
    if (!windowOk) blockers.push("set Use window");
    if (extendOn && identityLoading) blockers.push("identity loading");
    if (extendOn && identityNeeded && !identitySelectedPath) blockers.push("pick identity");
    if (busy) blockers.push("submitting");

    const ready: ConstructionReady = canSubmit
      ? { ok: true, label: "Ready", detail: null }
      : {
          ok: false,
          label: blockers[0] ? `Blocked · ${blockers[0]}` : "Blocked",
          detail: blockers.join(" · ") || null,
        };

    return {
      routes,
      useLabel,
      useWindow,
      vhs,
      vhsWarning: warning,
      identity: {
        mode: identityMode,
        path: identitySelectedPath,
        thumbUrl: identityCand?.thumb_url || identityCand?.url || null,
      },
      ready,
    };
  }, [
    activeClip,
    anyRoute,
    buildOverrides,
    busy,
    canSubmit,
    deriveFamily,
    deriveOn,
    extendFamily,
    extendOn,
    families,
    identityCandidates,
    identityLoading,
    identityNeeded,
    identitySelectedId,
    identitySelectedPath,
    markIn,
    markOut,
    mediaRelpath,
    varyFamily,
    varyOn,
    windowOk,
  ]);

  return (
    <div className="layout submit-composer panel">
      <PageHeader
        title="Submit"
        subtitle="Compose a factory job from a door handoff — Library, Clips, and Workbench find the subject; Submit only composes."
        actions={
          <div className="submit-composer__header-actions">
            {originBack ? (
              <a className="drt-btn" href={originBack.href}>
                {originBack.label}
              </a>
            ) : null}
            {hasIntent ? (
              <div className="discovery-preview-layout-switch" role="group" aria-label="Compose layout">
                <span className="discovery-preview-layout-switch__label">Layout</span>
                <div className="segmented">
                  <button
                    type="button"
                    className={layout === "split" ? "seg-btn active" : "seg-btn"}
                    onClick={() => {
                      setLayout("split");
                      persistLayout("split");
                    }}
                  >
                    Side by side
                  </button>
                  <button
                    type="button"
                    className={layout === "stacked" ? "seg-btn active" : "seg-btn"}
                    onClick={() => {
                      setLayout("stacked");
                      persistLayout("stacked");
                    }}
                  >
                    Stacked
                  </button>
                </div>
              </div>
            ) : null}
          </div>
        }
      />

      {!hasIntent ? (
        <div className="submit-composer__empty" aria-label="Submit needs intent">
          <p className="submit-composer__empty-lead">
            Submit is <strong>intent-only</strong> — open it from a doorway with a clip, scrubber window, or job.
            This screen does not browse the corpus.
          </p>
          <p className="factory-muted">
            Deep link shape: <span className="mono">/submit?media=…&clip_id=…</span> or{" "}
            <span className="mono">from_job=…</span> (+ optional <span className="mono">origin</span>).
          </p>
          <div className="submit-composer__empty-doors" role="list">
            <a className="drt-btn" href="/discovery" role="listitem">
              Library
            </a>
            <a className="drt-btn" href={clipsLibraryHref({ view: "all" })} role="listitem">
              Clips
            </a>
            <a className="drt-btn" href="/workbench" role="listitem">
              Workbench
            </a>
            <span className="factory-muted submit-composer__empty-soon" role="listitem">
              Factory · Rating doors next
            </span>
          </div>
        </div>
      ) : (
        <div
          className={`work-product-row work-product-row--${layout} submit-composer__stage`}
          aria-label="Compose"
        >
          <div className="work-product-row__head">
            <div className="work-product-row__head-main">
              <div className="work-product-row__title">
                <span className="work-product-badge">compose</span>
                <span title={mediaRelpath}>{mediaRelpath.split("/").pop() || mediaRelpath}</span>
              </div>
              <code className="work-product-row__key" title={mediaRelpath}>
                {mediaRelpath}
              </code>
            </div>
            <div className="submit-composer__links">
              {originBack ? (
                <a className="drt-btn" href={originBack.href}>
                  {originBack.label}
                </a>
              ) : null}
              {mediaRelpath ? (
                <a className="drt-btn" href={discoveryLibraryHref(mediaRelpath)}>
                  Library
                </a>
              ) : null}
              {mediaRelpath ? (
                <a
                  className="drt-btn"
                  href={clipsLibraryHref({
                    mediaRelpath,
                    clipId: clipId || activeClip?.clip_id,
                    view: "by_source",
                  })}
                >
                  Clips
                </a>
              ) : null}
            </div>
          </div>

          <div className="work-product-row__body">
            <div className="work-product-viewer">
              <div className="work-product-viewer__main">
                {playUrl ? (
                  <video
                    ref={videoRef}
                    className="work-product-viewer__video"
                    src={playUrl}
                    poster={posterUrl || undefined}
                    controls
                    playsInline
                    muted
                    preload="metadata"
                    onLoadedMetadata={(e) => {
                      const d = e.currentTarget.duration;
                      if (Number.isFinite(d) && d > 0) setVideoDuration(d);
                      setCurrentTime(e.currentTarget.currentTime || 0);
                    }}
                    onDurationChange={(e) => {
                      const d = e.currentTarget.duration;
                      if (Number.isFinite(d) && d > 0) setVideoDuration(d);
                    }}
                    onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime || 0)}
                    onSeeked={(e) => setCurrentTime(e.currentTarget.currentTime || 0)}
                  />
                ) : (
                  <div className="work-product-viewer__empty">No media</div>
                )}
              </div>
              {playUrl ? (
                <>
                  <VideoTrimControls
                    className="work-product-viewer__trim"
                    videoRef={videoRef}
                    duration={duration}
                    currentTime={currentTime}
                    markIn={markIn}
                    markOut={markOut}
                    mode={trimMode}
                    mediaSyncKey={mediaKey}
                    onSeek={setCurrentTime}
                    onSyncTime={setCurrentTime}
                    onMarkInChange={(v) => {
                      setMarkIn(v);
                      setActiveClip(null);
                      setClipId("");
                    }}
                    onMarkOutChange={(v) => {
                      setMarkOut(v);
                      setActiveClip(null);
                      setClipId("");
                    }}
                    onClear={() => {
                      setMarkIn(null);
                      setMarkOut(null);
                      setActiveClip(null);
                      setClipId("");
                    }}
                    onModeChange={setTrimMode}
                  />
                  {!windowOk ? (
                    <p className="work-product-viewer__trim-warn">Set mark in/out or pick a clip for Use.</p>
                  ) : null}
                  <ClipBookmarksRail
                    mediaRelpath={mediaRelpath.trim() || null}
                    duration={duration}
                    markIn={markIn}
                    markOut={markOut}
                    trimEditable
                    origin="submit"
                    selectedClipId={activeClip?.clip_id || clipId || null}
                    onSelectClip={(c) => {
                      setActiveClip(c);
                      setClipId(c?.clip_id || "");
                    }}
                    onApplyClip={(mi, mo, clip) => {
                      setMarkIn(mi);
                      setMarkOut(mo);
                      if (clip) {
                        setActiveClip(clip);
                        setClipId(clip.clip_id);
                      }
                    }}
                  />
                </>
              ) : null}
            </div>

            <div className="work-product-details submit-composer__compose">
              <div className="work-product-quick-queue" role="group" aria-label="Submit advance">
                <div className="work-product-quick-queue__row">
                  <span className="work-product-quick-queue__label" title="Advance routes to create from this Use">
                    Advance
                  </span>
                  <label
                    className="work-product-quick-queue__check"
                    title="Extend — chain this media as the next source_video"
                  >
                    <input
                      type="checkbox"
                      checked={extendOn}
                      disabled={busy}
                      onChange={(e) => setExtendOn(e.target.checked)}
                    />
                    Extend
                  </label>
                  <label
                    className="work-product-quick-queue__check"
                    title="Vary — same bindings (exact replay style)"
                  >
                    <input
                      type="checkbox"
                      checked={varyOn}
                      disabled={busy}
                      onChange={(e) => setVaryOn(e.target.checked)}
                    />
                    Vary
                  </label>
                  <label
                    className="work-product-quick-queue__check"
                    title="Derive — new combo from this seed (rewire prompt and/or source)"
                  >
                    <input
                      type="checkbox"
                      checked={deriveOn}
                      disabled={busy}
                      onChange={(e) => setDeriveOn(e.target.checked)}
                    />
                    Derive
                  </label>
                  <span className="work-product-quick-queue__sep" aria-hidden="true" />
                  <button
                    type="button"
                    className={
                      "drt-btn work-product-quick-queue__now" +
                      (preferredWhen === "now" ? " submit-composer__when--preferred" : "")
                    }
                    disabled={!canSubmit}
                    title="Commit checked routes at front of queue and enqueue now"
                    onClick={() => void submit("now")}
                  >
                    {busy && preferredWhen === "now" ? "Submitting…" : "Now"}
                  </button>
                  <button
                    type="button"
                    className={
                      "drt-btn work-product-quick-queue__later" +
                      (preferredWhen === "later" ? " submit-composer__when--preferred" : "")
                    }
                    disabled={!canSubmit}
                    title="Commit checked routes at normal priority"
                    onClick={() => void submit("later")}
                  >
                    {busy && preferredWhen === "later" ? "Submitting…" : "Later"}
                  </button>
                </div>
                {anyRoute ? (
                  <div className="work-product-quick-queue__families">
                    {extendOn
                      ? familySelect(
                          extendFamily,
                          setExtendFamily,
                          "Extend",
                          "Family whose shape runs this Extend (video source_video)",
                          extendFamilyOpts,
                        )
                      : null}
                    {varyOn
                      ? familySelect(
                          varyFamily,
                          setVaryFamily,
                          "Vary",
                          "Family whose shape runs this Vary",
                          varyFamilyRows.length ? varyFamilyRows : familyOpts,
                        )
                      : null}
                    {deriveOn
                      ? familySelect(
                          deriveFamily,
                          setDeriveFamily,
                          "Derive",
                          "Family whose shape runs this Derive",
                          deriveFamilyOpts,
                        )
                      : null}
                  </div>
                ) : (
                  <p className="work-product-quick-queue__hint">Select Extend, Vary, and/or Derive</p>
                )}
                {extendOn && identityNeeded ? (
                  <div className="work-product-identity-still" aria-label="Identity still">
                    <div className="work-product-identity-still__head">
                      <span className="work-product-quick-queue__label">Identity</span>
                      {identityLoading ? <span className="work-product-quick-queue__hint">Loading…</span> : null}
                      {!identityLoading && identitySelectedPath ? (
                        <span className="work-product-quick-queue__hint" title={identitySelectedPath}>
                          selected
                        </span>
                      ) : null}
                      {!identityLoading && !identitySelectedPath ? (
                        <span className="work-product-quick-queue__hint">pick or mint a still</span>
                      ) : null}
                    </div>
                    {identityCandidates.length ? (
                      <div className="work-product-identity-still__strip" role="listbox">
                        {identityCandidates.slice(0, 8).map((c) => {
                          const selected = identitySelectedId === c.id || identitySelectedPath === c.path;
                          const thumb = c.thumb_url || c.url;
                          return (
                            <button
                              key={c.id || c.path}
                              type="button"
                              role="option"
                              aria-selected={selected}
                              className={`work-product-identity-still__thumb${selected ? " is-selected" : ""}`}
                              disabled={busy}
                              title={c.label || c.evidence || "still"}
                              onClick={() => {
                                setIdentitySelectedPath(c.path);
                                setIdentitySelectedId(c.id);
                              }}
                            >
                              {thumb ? <img src={thumb} alt="" loading="lazy" /> : <span>{(c.evidence || "?").slice(0, 3)}</span>}
                              <span className="work-product-identity-still__ev">{c.evidence || ""}</span>
                            </button>
                          );
                        })}
                      </div>
                    ) : (
                      <p className="work-product-quick-queue__hint">No identity still yet — mint a frame or pick another family.</p>
                    )}
                    {identityMintTargets.length ? (
                      <div className="work-product-identity-still__mints">
                        {identityMintTargets.slice(0, 3).map((t) => (
                          <button
                            key={`${t.video_relpath || t.video_path}-${t.lineage_depth}`}
                            type="button"
                            className="drt-btn work-product-identity-still__mint"
                            disabled={busy || identityMintBusy}
                            onClick={() => void mintIdentity(t)}
                          >
                            {identityMintBusy ? "Minting…" : t.label || "First frame"}
                          </button>
                        ))}
                      </div>
                    ) : null}
                  </div>
                ) : null}
                <SubmitConstructionPreview
                  routes={constructionPreview.routes}
                  useLabel={constructionPreview.useLabel}
                  useWindow={constructionPreview.useWindow}
                  vhs={constructionPreview.vhs}
                  vhsWarning={constructionPreview.vhsWarning}
                  identity={constructionPreview.identity}
                  preferredWhen={preferredWhen}
                  origin={intent.origin}
                  fromJob={intent.fromJob}
                  ready={constructionPreview.ready}
                />
                {msg ? (
                  <p className="work-product-quick-queue__msg" title={msg}>
                    {msg}
                  </p>
                ) : null}
                {lastJobKey ? (
                  <div className="submit-composer__links">
                    <a className="drt-btn" href={workbenchHref({ jobKey: lastJobKey })}>
                      Open in Workbench
                    </a>
                    <a className="drt-btn" href="/comfy-queue">
                      Open Queue
                    </a>
                    {originBack ? (
                      <a className="drt-btn" href={originBack.href}>
                        {originBack.label}
                      </a>
                    ) : null}
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
