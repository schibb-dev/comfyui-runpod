import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  fetchHomeSummary,
  fetchHourlyChainBacklogs,
  fetchHourlySchedule,
  queueShapeFactoryCombo,
  setHourlySchedule,
} from "./api";
import { AppetitePreviewBadge } from "./AppetitePreviewBadge";
import { workbenchHref, workbenchHrefForMedia } from "./discoveryDeepLink";
import {
  backlogItemByKey,
  backlogNavItems,
  backlogNextPicks,
  stepBacklogNav,
} from "./hourlyBacklogNav";
import { PipelineMediaPlayer, type PipelineMediaPlayerHandle } from "./PipelineMediaPlayer";
import { routeHref } from "./routes";
import { comfyHealthIsBackoff, comfyHealthSummary } from "./comfyHealth";
import { SteerPreviewBadge } from "./SteerPreviewBadge";
import { destinationForWhen, type SubmitWhen } from "./workProductPendingQueue";
import type {
  Appetite,
  AppetiteFacet,
  HomeSummaryResponse,
  HourlyChainBacklog,
  HourlyChainBacklogItem,
  HourlyChainBacklogsResponse,
  HourlyExploreKind,
  HourlyExploreStrength,
  HourlyScheduleStatus,
  HourlySteerTeaser,
  HourlySubmitMode,
} from "./types";
import { factoryMapHourliesCurateHref } from "./factoryMapRoute";
import { WorkProductAppetiteStrip } from "./WorkProductAppetiteStrip";

function fileUrlFromRel(relpath?: string | null): string {
  if (!relpath) return "";
  return "/files/" + encodeURIComponent(relpath.replace(/\\/g, "/"));
}

function num(n?: number | null): string {
  return typeof n === "number" && Number.isFinite(n) ? String(n) : "—";
}

function formatDue(iso?: string | null): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

const INTERVAL_PRESETS = [15, 20, 30, 45, 60, 90, 120];

function hourlySuspendBanner(initial?: HourlyScheduleStatus | null): {
  active: boolean;
  text: string;
} {
  const pause = initial?.gpu_pause || initial?.suspend?.gpu_pause;
  if (pause?.active) {
    const by = pause.paused_by || "still_tag";
    const restore = pause.restore_enabled ? "on" : "off";
    return {
      active: true,
      text: `Suspended: ${by} holds the GPU — hourlies forced off (restores to ${restore}).`,
    };
  }
  if (initial?.schedule?.enabled === false) {
    return { active: true, text: "Hourlies disabled in schedule (ticks skip until Enabled)." };
  }
  if (initial?.due === false && initial?.schedule?.enabled !== false) {
    return { active: false, text: "" };
  }
  return { active: false, text: "" };
}

function HourlyScheduleControls({
  initial,
  onSaved,
}: {
  initial?: HourlyScheduleStatus | null;
  onSaved: (s: HourlyScheduleStatus) => void;
}) {
  const sch = initial?.schedule;
  const [interval, setIntervalMin] = useState(sch?.interval_minutes ?? 20);
  const [enabled, setEnabled] = useState(sch?.enabled !== false);
  const [mode, setMode] = useState<HourlySubmitMode>(
    (sch?.submit_mode as HourlySubmitMode) || "auto",
  );
  const [comfyMax, setComfyMax] = useState(sch?.comfy_queue_max ?? 3);
  const [pendingMax, setPendingMax] = useState(sch?.pending_queue_max ?? 10);
  const [hourlyMin, setHourlyMin] = useState(sch?.pending_hourly_min ?? 5);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!sch) return;
    setIntervalMin(sch.interval_minutes ?? 20);
    setEnabled(sch.enabled !== false);
    setMode((sch.submit_mode as HourlySubmitMode) || "auto");
    setComfyMax(sch.comfy_queue_max ?? 3);
    setPendingMax(sch.pending_queue_max ?? 10);
    setHourlyMin(sch.pending_hourly_min ?? 5);
  }, [sch]);

  const apply = async () => {
    setBusy(true);
    setErr("");
    try {
      const res = await setHourlySchedule({
        interval_minutes: Number(interval),
        enabled,
        submit_mode: mode,
        comfy_queue_max: Number(comfyMax),
        pending_queue_max: Number(pendingMax),
        pending_hourly_min: Number(hourlyMin),
      });
      onSaved(res);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const ruleHint =
    mode === "comfy"
      ? "Generate only when Comfy waiting is below max; otherwise skip. No hourly pending floor."
      : mode === "pending"
        ? "Keep this many hourly jobs on the pending FIFO; refill if the count dips. Drain takes overflow. Submit Queue/Next can still add and reorder."
        : "Keep this many hourly jobs on the pending FIFO; refill if the count dips. Extra hourlies and Submit jobs drain into Comfy.";

  return (
    <div className="home-hourly-controls">
      <div className="home-hourly-controls__row">
        <label className="home-hourly-controls__field">
          <span>Interval</span>
          <select value={interval} disabled={busy} onChange={(e) => setIntervalMin(Number(e.target.value))}>
            {(initial?.interval_presets?.length ? initial.interval_presets : INTERVAL_PRESETS).map((m) => (
              <option key={m} value={m}>
                {m} min
              </option>
            ))}
          </select>
        </label>
        <label className="home-hourly-controls__field">
          <span>Mode</span>
          <select value={mode} disabled={busy} onChange={(e) => setMode(e.target.value as HourlySubmitMode)}>
            <option value="auto">Auto</option>
            <option value="comfy">Comfy</option>
            <option value="pending">Pending</option>
          </select>
        </label>
        <label className="home-hourly-controls__field home-hourly-controls__field--num">
          <span>Comfy max</span>
          <input
            type="number"
            min={0}
            max={20}
            value={comfyMax}
            disabled={busy}
            onChange={(e) => setComfyMax(Number(e.target.value))}
          />
        </label>
        <label className="home-hourly-controls__field home-hourly-controls__field--num">
          <span>Pending max</span>
          <input
            type="number"
            min={0}
            max={50}
            value={pendingMax}
            disabled={busy}
            onChange={(e) => setPendingMax(Number(e.target.value))}
          />
        </label>
        <label
          className="home-hourly-controls__field home-hourly-controls__field--num"
          title="Hourly jobs kept on the pending queue. Drain refills if the count falls below this."
        >
          <span>Hourly min</span>
          <input
            type="number"
            min={0}
            max={50}
            value={hourlyMin}
            disabled={busy}
            onChange={(e) => setHourlyMin(Number(e.target.value))}
          />
        </label>
        <label
          className="home-hourly-controls__toggle"
          title={
            initial?.gpu_pause?.active
              ? "Forced off while still-tag/Florence holds the GPU. Enabling clears that pause."
              : "Writes hourly-schedule.json immediately"
          }
        >
          <input
            type="checkbox"
            checked={enabled && !initial?.gpu_pause?.active}
            disabled={busy}
            onChange={(e) => {
              const next = e.target.checked;
              setEnabled(next);
              setBusy(true);
              setErr("");
              void setHourlySchedule({ enabled: next })
                .then((res) => onSaved(res))
                .catch((ex) => {
                  setErr(ex instanceof Error ? ex.message : String(ex));
                  setEnabled(!next);
                })
                .finally(() => setBusy(false));
            }}
          />
          <span>
            {initial?.gpu_pause?.active
              ? "Enabled (paused)"
              : "Enabled"}
          </span>
        </label>
        <button type="button" className="drt-btn" disabled={busy} onClick={() => void apply()}>
          {busy ? "Saving…" : "Apply"}
        </button>
      </div>
      {hourlySuspendBanner(initial).active ? (
        <p className="home-hourly-controls__suspend" role="status">
          {hourlySuspendBanner(initial).text}
        </p>
      ) : null}
      <p className="home-hourly-controls__meta factory-muted">
        Next due {formatDue(initial?.next_due_at)}
        {initial?.due ? " · due now" : ""}
        {" · "}
        waiting {num(initial?.comfy_waiting)} · running {num(initial?.comfy_running)} · pending{" "}
        {num(initial?.factory_pending)}
        {" · "}
        hourlies {num(initial?.factory_hourly_pending)}/{num(sch?.pending_hourly_min ?? hourlyMin)}
        {comfyHealthIsBackoff(initial?.comfy_health)
          ? ` · ${comfyHealthSummary(initial?.comfy_health, initial?.comfy_health?.retry_in_sec)}`
          : ""}
        {initial?.still_promo?.until ? ` · image starters promoted until ${formatDue(initial.still_promo.until)}` : ""}
        {initial?.faceblast_promo?.until
          ? ` · FaceBlast-extend prompts promoted until ${formatDue(initial.faceblast_promo.until)}`
          : ""}
        {initial?.explore?.target
          ? ` · exploring ${initial.explore.target} (${initial.explore.strength || "boost"})`
          : ""}
      </p>
      <p className="home-hourly-controls__hint factory-muted">{ruleHint}</p>
      {err ? <p className="home-hourly-controls__err">{err}</p> : null}
    </div>
  );
}

function HourlyExploreControls({
  initial,
  onSaved,
}: {
  initial?: HourlyScheduleStatus | null;
  onSaved: (s: HourlyScheduleStatus) => void;
}) {
  const live = initial?.explore;
  const [kind, setKind] = useState<HourlyExploreKind>(live?.kind || "family");
  const [target, setTarget] = useState(live?.target || "");
  const [strength, setStrength] = useState<HourlyExploreStrength>(live?.strength || "boost");
  const [hours, setHours] = useState(2);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!live) return;
    setKind(live.kind || "family");
    setTarget(live.target || "");
    setStrength(live.strength || "boost");
  }, [live]);

  const apply = async () => {
    const want = target.trim();
    if (!want) {
      setErr("Set a family, prompt, still, or clip to explore.");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      const res = await setHourlySchedule({
        explore: {
          kind,
          target: want,
          strength,
          hours: hours > 0 ? hours : undefined,
        },
      });
      onSaved(res);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const clear = async () => {
    setBusy(true);
    setErr("");
    try {
      const res = await setHourlySchedule({ explore_clear: true });
      onSaved(res);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="home-hourly-controls home-hourly-controls--explore">
      <div className="home-hourly-controls__row">
        <label className="home-hourly-controls__field">
          <span>Explore</span>
          <select value={kind} disabled={busy} onChange={(e) => setKind(e.target.value)}>
            <option value="family">Family</option>
            <option value="prompt">Prompt</option>
            <option value="still">Still</option>
            <option value="clip">Clip</option>
          </select>
        </label>
        <label className="home-hourly-controls__field home-hourly-controls__field--wide">
          <span>Target</span>
          <input
            type="text"
            value={target}
            disabled={busy}
            placeholder="X-KNEEL-FB9-bare"
            onChange={(e) => setTarget(e.target.value)}
          />
        </label>
        <label className="home-hourly-controls__field">
          <span>Strength</span>
          <select value={strength} disabled={busy} onChange={(e) => setStrength(e.target.value)}>
            <option value="boost">Boost (~half)</option>
            <option value="focus">Focus (~most)</option>
          </select>
        </label>
        <label className="home-hourly-controls__field home-hourly-controls__field--num">
          <span>Hours</span>
          <input
            type="number"
            min={0}
            max={48}
            step={0.5}
            value={hours}
            disabled={busy}
            onChange={(e) => setHours(Number(e.target.value))}
          />
        </label>
        <button type="button" className="drt-btn" disabled={busy} onClick={() => void apply()}>
          {busy ? "Saving…" : "Explore"}
        </button>
        <button type="button" className="drt-btn" disabled={busy || !live} onClick={() => void clear()}>
          Clear
        </button>
      </div>
      <p className="home-hourly-controls__hint factory-muted">
        Time-boxed overlay — no appetite prior. Same record as Factory MCP{" "}
        <code>hourly_explore</code>.
        {live?.target
          ? ` Active: ${live.kind || "family"} ${live.target}${
              live.until ? ` until ${formatDue(live.until)}` : ""
            }${
              typeof live.remaining_ticks === "number" ? ` · ${live.remaining_ticks} ticks left` : ""
            }.`
          : " Idle."}
      </p>
      {err ? <p className="home-hourly-controls__err">{err}</p> : null}
    </div>
  );
}

function FreshThumb({ item }: { item: HomeSummaryFreshOutput }) {
  // Prefer an explicit thumb; fall back to a companion .png next to an mp4, then the file itself.
  const guessThumb =
    item.thumb_url ||
    (item.relpath && /\.mp4$/i.test(item.relpath)
      ? fileUrlFromRel(item.relpath.replace(/\.mp4$/i, ".png"))
      : "") ||
    item.url ||
    "";
  const rating = item.ratings?.rating_effective;
  return (
    <a className="home-thumb" href={discoveryLibraryHref(item.relpath)} title={basename(item.relpath)}>
      <img
        className="home-thumb__img"
        src={guessThumb}
        alt=""
        loading="lazy"
        onError={(e) => {
          const img = e.currentTarget;
          if (img.dataset.fallback !== "1" && item.url && img.src !== item.url) {
            img.dataset.fallback = "1";
            img.src = item.url;
          }
        }}
      />
      {typeof rating === "number" && rating > 0 ? (
        <span className="home-thumb__rating">★ {rating.toFixed(rating >= 1 ? 1 : 2)}</span>
      ) : null}
    </a>
  );
}

function backlogThumbUrl(item: HourlyChainBacklogItem): string {
  if (item.thumb_url) return item.thumb_url;
  const rel = item.video_relpath || "";
  if (rel && /\.mp4$/i.test(rel)) return fileUrlFromRel(rel.replace(/\.mp4$/i, ".png"));
  if (item.video_url) return item.video_url;
  return "";
}

function BacklogThumb({ item }: { item: HourlyChainBacklogItem }) {
  const src = backlogThumbUrl(item);
  const rel = item.video_relpath || "";
  return (
    <span className="home-backlog-thumb-wrap">
      {src ? (
        <img
          className="home-backlog-thumb"
          src={src}
          alt=""
          loading="lazy"
          onError={(e) => {
            const img = e.currentTarget;
            if (img.dataset.fallback !== "1" && item.video_url && img.src !== item.video_url) {
              img.dataset.fallback = "1";
              img.src = item.video_url;
              return;
            }
            img.dataset.fallback = "1";
            img.classList.add("home-backlog-thumb--empty");
            img.removeAttribute("src");
          }}
        />
      ) : (
        <span className="home-backlog-thumb home-backlog-thumb--empty" aria-hidden />
      )}
      {rel ? (
        <AppetitePreviewBadge
          relpath={rel}
          size="sm"
          jobKey={item.job_key}
          familySlug={item.consumer_family}
          className="home-backlog-thumb__appetite"
        />
      ) : null}
    </span>
  );
}

function HourlyBacklogCullControls({
  item,
  chain,
  onCulled,
}: {
  item: HourlyChainBacklogItem;
  chain: HourlyChainBacklog;
  onCulled: () => void;
}) {
  const rel = String(item.video_relpath || "").trim();
  const fam = String(chain.consumer_family || item.consumer_family || "").trim();

  const onAppetiteSaved = useCallback(
    (appetite: Appetite | "", _facet: AppetiteFacet) => {
      if (appetite === "remove") onCulled();
    },
    [onCulled],
  );

  if (!rel) {
    return (
      <p className="factory-muted home-backlog-cull__hint">No media path — use the viewer badges once media resolves.</p>
    );
  }

  return (
    <div className="home-backlog-cull">
      <p className="home-backlog-cull__hint factory-muted">
        Appetite on this parent for <strong>{fam || "chain"}</strong>. Use the{" "}
        <strong>upper-left steer badge</strong> on the viewer for Keep / Pin / Later / Out.
        Appetite <strong>Remove</strong> drops it from the waiting list.
      </p>
      <WorkProductAppetiteStrip
        relpath={rel}
        jobKey={item.job_key}
        familySlug={fam || undefined}
        onSaved={onAppetiteSaved}
      />
    </div>
  );
}

/** Jump to parent on Workbench, or submit the predicted child to Comfy now/later. */
function HourlyBacklogActionBar({
  item,
  chain,
  onSubmitted,
  compact,
}: {
  item: HourlyChainBacklogItem;
  chain: HourlyChainBacklog;
  onSubmitted?: (jobKey: string) => void;
  compact?: boolean;
}) {
  const preview = item.pending_preview || {};
  const family = String(preview.family || chain.consumer_family || item.consumer_family || "").trim();
  const sourceVideo = String(item.video || "").trim();
  const promptProfile = String(preview.prompt_profile || "").trim();
  const [busyWhen, setBusyWhen] = useState<SubmitWhen | "">("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [submittedKey, setSubmittedKey] = useState("");

  const workbenchUrl = item.job_key
    ? workbenchHref({ jobKey: item.job_key })
    : workbenchHrefForMedia({
        relpath: item.video_relpath,
        name: item.video_name,
      });

  const canSubmit = Boolean(family && sourceVideo);
  const busy = Boolean(busyWhen);

  const submit = useCallback(
    async (when: "now" | "later") => {
      if (!canSubmit || busy) return;
      setBusyWhen(when);
      setErr("");
      setMsg("");
      try {
        const dest = destinationForWhen(when);
        const res = await queueShapeFactoryCombo({
          family_slug: family,
          bindings: {
            source_video: sourceVideo,
            ...(promptProfile ? { prompt_profile: promptProfile } : {}),
          },
          destination: dest.destination,
          pending_position: dest.pending_position,
          front: dest.front,
          source_surface: "hourly_backlog",
        });
        const key = String(res.job_key || "").trim();
        if (key) {
          setSubmittedKey(key);
          const rank =
            typeof res.pending_rank === "number" ? ` · pending #${res.pending_rank + 1}` : "";
          setMsg(
            when === "now"
              ? `Submitted now · ${key}${res.prompt_id ? ` · ${res.prompt_id}` : ""}`
              : `Submitted later · ${key}${rank}`,
          );
          onSubmitted?.(key);
        } else {
          setMsg(res.prompt_id ? `Comfy ${res.prompt_id}` : `Submitted ${when}`);
        }
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e));
      } finally {
        setBusyWhen("");
      }
    },
    [busy, canSubmit, family, onSubmitted, promptProfile, sourceVideo],
  );

  return (
    <div className={`home-backlog-actions${compact ? " home-backlog-actions--compact" : ""}`}>
      <div className="home-backlog-actions__row" role="group" aria-label="Backlog actions">
        <a className="drt-btn" href={workbenchUrl} title="Open the parent job on Workbench">
          Workbench
        </a>
        <button
          type="button"
          className="drt-btn home-backlog-actions__submit-now"
          disabled={!canSubmit || busy}
          title={
            canSubmit
              ? `Submit ${family} straight to Comfy now (front of queue)`
              : "Need consumer family + source video"
          }
          onClick={() => void submit("now")}
        >
          {busyWhen === "now" ? "Submitting…" : "Submit now"}
        </button>
        <button
          type="button"
          className="drt-btn home-backlog-actions__submit-later"
          disabled={!canSubmit || busy}
          title={
            canSubmit
              ? `Submit ${family} to Comfy later (normal priority)`
              : "Need consumer family + source video"
          }
          onClick={() => void submit("later")}
        >
          {busyWhen === "later" ? "Submitting…" : "Submit later"}
        </button>
      </div>
      {msg ? (
        <p className="home-backlog-actions__msg factory-muted">
          {msg}
          {submittedKey ? (
            <>
              {" · "}
              <a className="home-cta" href={workbenchHref({ jobKey: submittedKey })}>
                Open on Workbench →
              </a>
            </>
          ) : null}
        </p>
      ) : null}
      {err ? <p className="home-hourly-controls__err">{err}</p> : null}
    </div>
  );
}

const BACKLOG_PLAYBACK_PREFS_KEY = "hourly-backlog-preview-playback";

function loadBacklogPlaybackPrefs(): { autoplay: boolean; repeat: boolean } {
  try {
    const raw = localStorage.getItem(BACKLOG_PLAYBACK_PREFS_KEY);
    if (!raw) return { autoplay: true, repeat: true };
    const parsed = JSON.parse(raw) as { autoplay?: unknown; repeat?: unknown };
    return {
      autoplay: parsed.autoplay !== false,
      repeat: parsed.repeat !== false,
    };
  } catch {
    return { autoplay: true, repeat: true };
  }
}

function saveBacklogPlaybackPrefs(prefs: { autoplay: boolean; repeat: boolean }) {
  try {
    localStorage.setItem(BACKLOG_PLAYBACK_PREFS_KEY, JSON.stringify(prefs));
  } catch {
    /* ignore quota / private mode */
  }
}

function HourlyBacklogPendingModal({
  chain,
  item,
  index,
  count,
  onClose,
  onStep,
  onCulled,
  onSubmitted,
}: {
  chain: HourlyChainBacklog;
  item: HourlyChainBacklogItem;
  index: number;
  count: number;
  onClose: () => void;
  onStep: (delta: number) => void;
  onCulled: () => void;
  onSubmitted?: (jobKey: string) => void;
}) {
  const preview = item.pending_preview || {};
  const family = preview.family || item.consumer_family || "child";
  const promptName = preview.prompt_name || preview.prompt_label || preview.prompt_slug || "default";
  const rank = typeof item.next_rank === "number" ? item.next_rank : item.next ? 1 : 0;
  const playerRef = useRef<PipelineMediaPlayerHandle | null>(null);
  const [playbackPrefs, setPlaybackPrefs] = useState(loadBacklogPlaybackPrefs);

  const setAutoplay = useCallback((next: boolean) => {
    setPlaybackPrefs((prev) => {
      const prefs = { ...prev, autoplay: next };
      saveBacklogPlaybackPrefs(prefs);
      return prefs;
    });
  }, []);

  const setRepeat = useCallback((next: boolean) => {
    setPlaybackPrefs((prev) => {
      const prefs = { ...prev, repeat: next };
      saveBacklogPlaybackPrefs(prefs);
      return prefs;
    });
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopImmediatePropagation();
        onClose();
        return;
      }
      if (e.key === "ArrowDown" || e.key === "ArrowRight") {
        e.preventDefault();
        e.stopImmediatePropagation();
        onStep(1);
        return;
      }
      if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
        e.preventDefault();
        e.stopImmediatePropagation();
        onStep(-1);
        return;
      }
      if (e.code === "Space" || e.key === " ") {
        e.preventDefault();
        e.stopImmediatePropagation();
        playerRef.current?.togglePlay();
      }
    };
    window.addEventListener("keydown", onKey, { capture: true });
    return () => window.removeEventListener("keydown", onKey, { capture: true });
  }, [onClose, onStep]);

  return createPortal(
    <div
      className="modal-overlay home-backlog-modal-overlay"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={`Pending preview ${family}`}
    >
      <div className="modal home-backlog-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title">
            Pending preview · {family}
            {rank ? ` · pick ${rank}` : ""}
            {count > 0 ? ` · ${index + 1}/${count}` : ""}
          </div>
          <div className="modal-actions">
            <button type="button" className="drt-btn" disabled={count < 2} onClick={() => onStep(-1)}>
              ←
            </button>
            <button type="button" className="drt-btn" disabled={count < 2} onClick={() => onStep(1)}>
              →
            </button>
            <button type="button" onClick={onClose}>
              Close
            </button>
          </div>
        </div>
        <div className="home-backlog-modal__body">
          <div className="home-backlog-modal__player-wrap">
            <PipelineMediaPlayer
              ref={playerRef}
              videoUrl={item.video_url}
              thumbUrl={backlogThumbUrl(item)}
              mediaKey={item.job_key || item.video_name}
              alt={item.video_name || "source clip"}
              className="home-backlog-modal__player"
              autoplay={playbackPrefs.autoplay}
              loop={playbackPrefs.repeat}
            />
            {item.video_relpath ? (
              <>
                <SteerPreviewBadge
                  key={`steer:${item.job_key || item.video_relpath}`}
                  relpath={item.video_relpath}
                  jobKey={item.job_key}
                  familySlug={chain.consumer_family || item.consumer_family}
                  assetKind="video"
                  surface="chain_backlog"
                  className="home-backlog-modal__steer"
                  onStatusChange={(st) => {
                    if (st === "out") onCulled();
                  }}
                />
                <AppetitePreviewBadge
                  relpath={item.video_relpath}
                  jobKey={item.job_key}
                  familySlug={chain.consumer_family || item.consumer_family}
                  className="home-backlog-modal__appetite"
                />
              </>
            ) : null}
            <div className="home-backlog-playback" role="group" aria-label="Playback">
              <label className="home-backlog-playback__tog">
                <input
                  type="checkbox"
                  checked={playbackPrefs.autoplay}
                  onChange={(e) => setAutoplay(e.target.checked)}
                />
                Auto-play
              </label>
              <label className="home-backlog-playback__tog">
                <input
                  type="checkbox"
                  checked={playbackPrefs.repeat}
                  onChange={(e) => setRepeat(e.target.checked)}
                />
                Auto-repeat
              </label>
              <span className="factory-muted home-backlog-playback__hint">Space play/pause</span>
            </div>
          </div>
          <div className="home-backlog-modal__meta">
            <p>
              Hourly would enqueue a <strong>{family}</strong> job on the pending FIFO, using this
              {item.producer_family ? ` ${item.producer_family}` : ""} clip as <code>source_video</code>.
              Use <strong>Submit now</strong> / <strong>Submit later</strong> to send it to Comfy
              immediately.
            </p>
            <HourlyBacklogActionBar item={item} chain={chain} onSubmitted={onSubmitted} />
            <HourlyBacklogCullControls item={item} chain={chain} onCulled={onCulled} />
            <dl>
              <div>
                <dt>Prompt</dt>
                <dd>
                  {promptName}
                  {preview.prompt_slug ? <span className="factory-muted"> · {preview.prompt_slug}</span> : null}
                </dd>
              </div>
              <div>
                <dt>From</dt>
                <dd className="mono">{item.job_key}</dd>
              </div>
              <div>
                <dt>Source</dt>
                <dd className="mono">{item.video_name}</dd>
              </div>
              <div>
                <dt>Chain</dt>
                <dd>
                  {chain.label}
                  {rank === 1 ? " · next drain pick" : rank ? ` · upcoming pick ${rank}` : ""}
                </dd>
              </div>
            </dl>
            {preview.prompt_excerpt ? (
              <blockquote className="home-backlog-modal__excerpt">{preview.prompt_excerpt}</blockquote>
            ) : null}
            <p className="factory-muted">↑↓ or ←→ next item · Space play/pause · Esc close</p>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function isTypingTarget(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
}

function focusBacklogKey(root: HTMLElement | null, jobKey: string) {
  if (!root || !jobKey) return;
  const nodes = root.querySelectorAll<HTMLElement>(`[data-backlog-key="${CSS.escape(jobKey)}"]`);
  nodes.forEach((node) => node.scrollIntoView({ block: "nearest" }));
  const prefer = root.querySelector<HTMLElement>(
    `.home-backlog-items [data-backlog-key="${CSS.escape(jobKey)}"]`,
  );
  (prefer || nodes[0])?.focus();
}

function shortBacklogKey(jobKey: string): string {
  const s = String(jobKey || "").trim();
  if (!s) return "—";
  if (s.length <= 36) return s;
  return `${s.slice(0, 14)}…${s.slice(-12)}`;
}

type BacklogSnapChain = {
  id: string;
  label: string;
  count: number;
  next: string;
  keys: string[];
};

type BacklogSnap = {
  at: string;
  chains: BacklogSnapChain[];
};

function snapshotBacklog(data: HourlyChainBacklogsResponse | null): BacklogSnap | null {
  if (!data?.chains?.length) return null;
  return {
    at: new Date().toISOString(),
    chains: data.chains.map((c) => ({
      id: String(c.id || ""),
      label: String(c.label || c.id || "chain"),
      count: Number(c.count ?? (c.items || []).length),
      next: String(c.next?.job_key || (c.next_picks || [])[0]?.job_key || ""),
      keys: (c.items || []).map((it) => String(it.job_key || "")).filter(Boolean),
    })),
  };
}

function diffBacklogLines(prev: BacklogSnap | null, next: BacklogSnap): string[] {
  if (!prev) {
    return next.chains.map((c) => `${c.label}: ${c.count} waiting`);
  }
  const lines: string[] = [];
  for (const nc of next.chains) {
    const pc = prev.chains.find((c) => c.id === nc.id);
    if (!pc) {
      lines.push(`${nc.label}: ${nc.count} (new chain)`);
      continue;
    }
    const delta = nc.count - pc.count;
    const countBit =
      delta === 0 ? String(nc.count) : `${pc.count} → ${nc.count} (${delta > 0 ? "+" : ""}${delta})`;
    let line = `${nc.label}: ${countBit}`;
    if (pc.next !== nc.next) {
      line += ` · next ${shortBacklogKey(pc.next)} → ${shortBacklogKey(nc.next)}`;
    }
    const prevSet = new Set(pc.keys);
    const nextSet = new Set(nc.keys);
    let dropped = 0;
    let added = 0;
    for (const k of prevSet) if (!nextSet.has(k)) dropped += 1;
    for (const k of nextSet) if (!prevSet.has(k)) added += 1;
    if (dropped || added) line += ` · culled −${dropped} / new +${added}`;
    lines.push(line);
  }
  return lines;
}

function formatRecalcClock(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit", second: "2-digit" });
  } catch {
    return iso;
  }
}

function HourlyChainBacklogsCard({ refreshToken }: { refreshToken: number }) {
  const [data, setData] = useState<HourlyChainBacklogsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingDetailId, setLoadingDetailId] = useState<string>("");
  const [loadingMoreId, setLoadingMoreId] = useState<string>("");
  const [elapsedSec, setElapsedSec] = useState(0);
  const [statusLine, setStatusLine] = useState("Starting scan…");
  const [error, setError] = useState("");
  const [openId, setOpenId] = useState<string>("i2v_to_gex");
  const [familyFilter, setFamilyFilter] = useState<Record<string, string>>({});
  const [selectedKey, setSelectedKey] = useState<string>("");
  const [viewerKey, setViewerKey] = useState<string>("");
  const [diffLines, setDiffLines] = useState<string[]>([]);
  const [lastRecalcAt, setLastRecalcAt] = useState<string>("");
  const cardRef = useRef<HTMLDivElement | null>(null);
  const prevSnapRef = useRef<BacklogSnap | null>(null);
  const dataRef = useRef<HourlyChainBacklogsResponse | null>(null);
  const openIdRef = useRef(openId);
  const loadGenRef = useRef(0);
  dataRef.current = data;
  openIdRef.current = openId;

  useEffect(() => {
    if (!loading && !loadingDetailId && !loadingMoreId) {
      setElapsedSec(0);
      return;
    }
    setElapsedSec(0);
    const t0 = Date.now();
    const id = window.setInterval(() => {
      setElapsedSec(Math.floor((Date.now() - t0) / 1000));
    }, 250);
    return () => window.clearInterval(id);
  }, [loading, loadingDetailId, loadingMoreId, refreshToken]);

  const mergeChain = useCallback((chain: HourlyChainBacklog, append: boolean) => {
    setData((prev) => {
      if (!prev?.chains?.length) {
        return { ok: true, chains: [chain], mode: "detail", cursor: prev?.cursor };
      }
      const chains = prev.chains.map((c) => {
        if (c.id !== chain.id) return c;
        if (!append) return { ...c, ...chain };
        const prevItems = c.items || [];
        const nextItems = [...prevItems, ...(chain.items || [])];
        return {
          ...c,
          ...chain,
          items: nextItems,
          count: chain.count ?? c.count,
        };
      });
      return { ...prev, chains };
    });
  }, []);

  const loadDetail = useCallback(
    async (chainId: string, opts?: { append?: boolean; offset?: number }) => {
      const append = Boolean(opts?.append);
      if (append) setLoadingMoreId(chainId);
      else setLoadingDetailId(chainId);
      setStatusLine(append ? `Loading more for ${chainId}…` : `Loading list for ${chainId}…`);
      try {
        const detail = await fetchHourlyChainBacklogs({
          mode: "detail",
          chainId,
          offset: opts?.offset ?? 0,
          limit: 80,
          cursor: dataRef.current?.cursor,
        });
        const chain = (detail.chains || [])[0];
        if (chain) mergeChain(chain, append);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setLoadingDetailId("");
        setLoadingMoreId("");
        setStatusLine("");
      }
    },
    [mergeChain],
  );

  const load = useCallback(
    async (opts?: { compare?: boolean }) => {
      const gen = ++loadGenRef.current;
      setLoading(true);
      setError("");
      setStatusLine("Scanning complete parents (summary)…");
      const compare = opts?.compare !== false;
      const before = compare ? prevSnapRef.current || snapshotBacklog(dataRef.current) : null;
      try {
        const next = await fetchHourlyChainBacklogs({ mode: "summary" });
        if (gen !== loadGenRef.current) return;
        setData(next);
        setStatusLine("Summary ready — expand a chain for the full list");
        const after = snapshotBacklog(next);
        if (after) {
          prevSnapRef.current = after;
          setLastRecalcAt(after.at);
          if (compare && before) setDiffLines(diffBacklogLines(before, after));
          else setDiffLines(after.chains.map((c) => `${c.label}: ${c.count} waiting`));
        }
        const open = String(openIdRef.current || "").trim();
        const openChain = (next.chains || []).find((c) => c.id === open);
        if (open && openChain && openChain.items_loaded === false && (openChain.count || 0) > 0) {
          void loadDetail(open);
        }
      } catch (e) {
        if (gen !== loadGenRef.current) return;
        setError(e instanceof Error ? e.message : String(e));
        setStatusLine("");
      } finally {
        if (gen === loadGenRef.current) setLoading(false);
      }
    },
    [loadDetail],
  );

  useEffect(() => {
    void load({ compare: false });
  }, [load, refreshToken]);

  const chains = data?.chains ?? [];
  const openChain = chains.find((c) => c.id === openId) || null;
  const filter = familyFilter[openId] || "all";
  const nav = useMemo(
    () => (openChain ? backlogNavItems(openChain, filter) : []),
    [openChain, filter],
  );
  const selectedItem = backlogItemByKey(openChain, selectedKey) || nav[0] || null;
  const viewerItem = viewerKey ? backlogItemByKey(openChain, viewerKey) : null;
  const viewerIndex = viewerItem
    ? nav.findIndex((it) => it.job_key === viewerItem.job_key)
    : -1;

  const selectItem = useCallback(
    (item: HourlyChainBacklogItem | null, opts?: { openPreview?: boolean; focus?: boolean }) => {
      const key = String(item?.job_key || "");
      setSelectedKey(key);
      if (opts?.openPreview && key) setViewerKey(key);
      if (opts?.focus && key) {
        requestAnimationFrame(() => focusBacklogKey(cardRef.current, key));
      }
    },
    [],
  );

  const stepNav = useCallback(
    (delta: number, opts?: { openPreview?: boolean }) => {
      const next = stepBacklogNav(nav, selectedKey || viewerKey, delta);
      if (next) selectItem(next, { openPreview: opts?.openPreview ?? Boolean(viewerKey), focus: true });
    },
    [nav, selectItem, selectedKey, viewerKey],
  );

  const onCulled = useCallback(() => {
    setViewerKey("");
    setSelectedKey("");
    void load({ compare: true });
  }, [load]);

  const onToggleChain = useCallback(
    (chainId: string) => {
      setOpenId((cur) => {
        const next = cur === chainId ? "" : chainId;
        if (next) {
          const ch = (dataRef.current?.chains || []).find((c) => c.id === next);
          if (ch && ch.items_loaded === false && (ch.count || 0) > 0) {
            void loadDetail(next);
          }
        }
        return next;
      });
    },
    [loadDetail],
  );

  const onLoadMore = useCallback(
    (chain: HourlyChainBacklog) => {
      const id = String(chain.id || "");
      if (!id) return;
      const offset = (chain.items || []).length;
      void loadDetail(id, { append: true, offset });
    },
    [loadDetail],
  );

  useEffect(() => {
    if (!openChain) return;
    if (selectedKey && backlogItemByKey(openChain, selectedKey)) return;
    const first = nav[0];
    if (first?.job_key) setSelectedKey(String(first.job_key));
  }, [nav, openChain, selectedKey]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (viewerKey) return;
      if (isTypingTarget(e.target)) return;
      const root = cardRef.current;
      if (!root || !(e.target instanceof Node) || !root.contains(e.target)) return;
      if (e.key === "ArrowDown" || e.key === "ArrowRight") {
        e.preventDefault();
        stepNav(1);
        return;
      }
      if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
        e.preventDefault();
        stepNav(-1);
        return;
      }
      if (e.code === "Space" || e.key === " ") {
        const t = e.target;
        if (
          t instanceof HTMLElement &&
          t.closest(".home-backlog-fams, .home-backlog-chain__head, .home-backlog-cull, .home-card__actions")
        ) {
          return;
        }
        if (!selectedItem) return;
        e.preventDefault();
        setViewerKey(String(selectedItem.job_key || ""));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectedItem, stepNav, viewerKey]);

  const busy = loading || Boolean(loadingDetailId) || Boolean(loadingMoreId);
  const waitHint =
    busy && elapsedSec >= 2
      ? elapsedSec >= 60
        ? " — still walking job trees; counts come before the full lists"
        : elapsedSec >= 20
          ? " — large trees; hang tight"
          : " — scanning parents"
      : "";

  return (
    <Panel
      title="Chain backlogs"
      hint="Steer parent clips here — Recalculate reloads what the next tick would drain"
      actions={
        <button
          type="button"
          className="drt-btn home-backlog-recalc"
          disabled={loading}
          onClick={() => void load({ compare: true })}
        >
          {loading ? `Scanning… ${elapsedSec}s` : "Recalculate"}
        </button>
      }
      footer={
        <span className="home-backlog-foot">
          <a className="home-cta" href={routeHref("workbench")}>
            Open Workbench pending →
          </a>
          <a className="home-cta" href={routeHref("queue")}>
            Queue ledger →
          </a>
        </span>
      }
    >
      <div ref={cardRef} className="home-backlog-card" tabIndex={-1}>
        {busy || statusLine ? (
          <p className="home-backlog-status" role="status" aria-live="polite">
            {loading
              ? `Scanning complete parents… ${elapsedSec}s${waitHint}`
              : loadingDetailId
                ? `Loading ${loadingDetailId} list… ${elapsedSec}s`
                : loadingMoreId
                  ? `Loading more… ${elapsedSec}s`
                  : statusLine}
          </p>
        ) : null}
        {error ? <p className="home-hourly-controls__err">{error}</p> : null}
        {data?.note ? <p className="home-hourly-controls__hint">{data.note}</p> : null}
        {typeof data?.cursor === "number" ? (
          <p className="home-hourly-controls__meta">
            Cursor {data.cursor}
            {data.mode ? ` · ${data.mode}` : ""}
            {data.cached ? ` · cached ${data.cache_age_sec ?? "?"}s` : ""}
            {lastRecalcAt ? ` · scanned ${formatRecalcClock(lastRecalcAt)}` : ""}
          </p>
        ) : null}
        {diffLines.length ? (
          <div className="home-backlog-delta" role="status" aria-live="polite">
            <div className="home-backlog-delta__title">Since last scan</div>
            <ul>
              {diffLines.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </div>
        ) : null}
        <div className="home-backlog-list">
          {loading && !chains.length ? (
            <div className="home-backlog-skeleton" aria-hidden={false}>
              <p className="factory-muted">Waiting on summary counts (i2v, facial, self-extend, Kneel→GEX2)…</p>
              <p className="mono">{elapsedSec}s</p>
              <div className="home-backlog-list" style={{ marginTop: 10 }}>
                {["I2V → GEX", "GEX2 → Facial", "Named self-extend", "Kneel → GEX2"].map((label) => (
                  <section key={label} className="home-backlog-chain" aria-busy="true">
                    <div className="home-backlog-chain__head" style={{ cursor: "default" }}>
                      <span className="home-backlog-chain__title">{label}</span>
                      <span className="home-backlog-chain__count mono">…</span>
                      <span className="home-backlog-chain__meta">scanning…</span>
                    </div>
                  </section>
                ))}
              </div>
            </div>
          ) : null}
          {chains.map((chain) => (
            <HourlyBacklogChain
              key={chain.id || chain.label}
              chain={chain}
              open={openId === chain.id}
              familyFilter={familyFilter[chain.id || ""] || "all"}
              selectedKey={openId === chain.id ? selectedKey : ""}
              itemsLoading={loadingDetailId === chain.id}
              moreLoading={loadingMoreId === chain.id}
              onToggle={() => onToggleChain(String(chain.id || ""))}
              onFamily={(fam) =>
                setFamilyFilter((prev) => ({ ...prev, [chain.id || ""]: fam }))
              }
              onSelectItem={(item) => selectItem(item, { openPreview: false })}
              onOpenItem={(item) => selectItem(item, { openPreview: true })}
              onLoadMore={() => onLoadMore(chain)}
            />
          ))}
        </div>
        {openChain && selectedItem ? (
          <div className="home-backlog-steer-dock">
            <div className="home-backlog-steer-dock__head">
              Steer selected
              <span className="factory-muted mono">
                {" "}
                {selectedItem.producer_family || ""} · {shortBacklogKey(String(selectedItem.job_key || ""))}
              </span>
            </div>
            <HourlyBacklogActionBar
              item={selectedItem}
              chain={openChain}
              compact
              onSubmitted={() => void load({ compare: true })}
            />
            <HourlyBacklogCullControls item={selectedItem} chain={openChain} onCulled={onCulled} />
          </div>
        ) : null}
      </div>
      {openChain && viewerItem ? (
        <HourlyBacklogPendingModal
          chain={openChain}
          item={viewerItem}
          index={Math.max(0, viewerIndex)}
          count={nav.length}
          onClose={() => {
            setViewerKey("");
            requestAnimationFrame(() =>
              focusBacklogKey(cardRef.current, String(viewerItem.job_key || selectedKey)),
            );
          }}
          onStep={(delta) => stepNav(delta, { openPreview: true })}
          onCulled={onCulled}
          onSubmitted={() => void load({ compare: true })}
        />
      ) : null}
    </Panel>
  );
}

function HourlyBacklogChain({
  chain,
  open,
  familyFilter,
  selectedKey,
  itemsLoading,
  moreLoading,
  onToggle,
  onFamily,
  onSelectItem,
  onOpenItem,
  onLoadMore,
}: {
  chain: HourlyChainBacklog;
  open: boolean;
  familyFilter: string;
  selectedKey: string;
  itemsLoading?: boolean;
  moreLoading?: boolean;
  onToggle: () => void;
  onFamily: (family: string) => void;
  onSelectItem: (item: HourlyChainBacklogItem) => void;
  onOpenItem: (item: HourlyChainBacklogItem) => void;
  onLoadMore?: () => void;
}) {
  const items = chain.items ?? [];
  const visible =
    familyFilter === "all"
      ? items
      : items.filter((it) => (it.producer_family || "") === familyFilter);
  const nextPicks = backlogNextPicks(chain);
  const families = Object.entries(chain.by_family || {}).sort((a, b) => b[1] - a[1]);
  const lookback =
    typeof chain.lookback_days === "number"
      ? `last ${chain.lookback_days}d`
      : chain.lookback_note
        ? "no age cull"
        : null;
  const deferred = chain.items_loaded === false;
  const hasMore = Boolean(chain.items_has_more);

  return (
    <section className="home-backlog-chain">
      <button type="button" className="home-backlog-chain__head" onClick={onToggle} aria-expanded={open}>
        <span className="home-backlog-chain__title">{chain.label}</span>
        <span className="home-backlog-chain__count mono">{chain.count ?? 0}</span>
        <span className="home-backlog-chain__meta">
          every {chain.drain_every ?? "?"} cursor
          {chain.due_this_cursor ? " · due now" : ""}
          {lookback ? ` · ${lookback}` : ""}
          {deferred && !itemsLoading ? " · list on expand" : ""}
        </span>
      </button>
      {open ? (
        <div className="home-backlog-chain__body">
          {chain.lookback_note ? <p className="factory-muted">{chain.lookback_note}</p> : null}
          {nextPicks.length ? (
            <ol className="home-backlog-nexts" aria-label="Next hourly picks">
              {nextPicks.map((it, i) => {
                const selected = it.job_key === selectedKey;
                return (
                  <li
                    key={it.job_key || i}
                    className={[it.next || i === 0 ? "is-next" : "", selected ? "is-selected" : ""]
                      .filter(Boolean)
                      .join(" ")}
                  >
                    <button
                      type="button"
                      className="home-backlog-row"
                      data-backlog-key={it.job_key}
                      aria-selected={selected}
                      title={it.job_key || it.video_name || ""}
                      onClick={() => onOpenItem(it)}
                      onFocus={() => onSelectItem(it)}
                    >
                      <span className="home-backlog-nexts__n">{i + 1}</span>
                      <BacklogThumb item={it} />
                      <span className="home-backlog-row__text">
                        {it.producer_family ? <strong>{it.producer_family}</strong> : null}
                        {i === 0 ? <em>next</em> : <em>pick {i + 1}</em>}
                        <span className="factory-muted">{it.video_name || it.job_key}</span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ol>
          ) : (
            <p className="factory-muted">Nothing waiting.</p>
          )}
          {nextPicks.length ? (
            <p className="home-backlog-keys factory-muted">↑↓ select · Space preview</p>
          ) : null}
          {families.length > 1 ? (
            <div className="home-backlog-fams" role="group" aria-label="Filter by producer family">
              <button
                type="button"
                className={familyFilter === "all" ? "is-on" : undefined}
                onClick={() => onFamily("all")}
              >
                all {chain.count ?? 0}
              </button>
              {families.map(([fam, n]) => (
                <button
                  key={fam}
                  type="button"
                  className={familyFilter === fam ? "is-on" : undefined}
                  onClick={() => onFamily(fam)}
                >
                  {fam} {n}
                </button>
              ))}
            </div>
          ) : null}
          {itemsLoading ? (
            <p className="factory-muted home-backlog-status">Loading waiting list…</p>
          ) : null}
          {visible.length > 0 ? (
            <ul className="home-backlog-items">
              {visible.map((it) => {
                const selected = it.job_key === selectedKey;
                const rank = typeof it.next_rank === "number" ? it.next_rank : it.next ? 1 : 0;
                return (
                  <li
                    key={it.job_key}
                    className={[rank ? "is-next" : "", selected ? "is-selected" : ""]
                      .filter(Boolean)
                      .join(" ") || undefined}
                  >
                    <button
                      type="button"
                      className="home-backlog-row"
                      data-backlog-key={it.job_key}
                      aria-selected={selected}
                      title={it.job_key || it.video_name || ""}
                      onClick={() => onOpenItem(it)}
                      onFocus={() => onSelectItem(it)}
                    >
                      <BacklogThumb item={it} />
                      <span className="home-backlog-row__text">
                        {it.producer_family ? <strong>{it.producer_family}</strong> : null}
                        {rank ? <em>{rank === 1 ? "next" : `#${rank}`}</em> : null}
                        <span className="factory-muted">{it.video_name || it.job_key}</span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : null}
          {!itemsLoading && chain.items_loaded && hasMore ? (
            <button
              type="button"
              className="drt-btn home-backlog-more"
              disabled={moreLoading}
              onClick={() => onLoadMore?.()}
            >
              {moreLoading
                ? "Loading…"
                : `Load more (${(chain.items || []).length}/${chain.items_total ?? chain.count ?? "?"})`}
            </button>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function Panel({
  title,
  hint,
  children,
  footer,
  actions,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <section className="home-card sfmap-hourlies-card">
      <div className="home-card__head">
        <div className="home-card__head-main">
          <h2 className="home-card__title">{title}</h2>
          {hint ? <span className="home-card__hint factory-muted">{hint}</span> : null}
        </div>
        {actions ? <div className="home-card__actions">{actions}</div> : null}
      </div>
      <div className="home-card__body">{children}</div>
      {footer ? <div className="home-card__foot">{footer}</div> : null}
    </section>
  );
}

type HourlyNextSampleInfo = NonNullable<HomeSummaryResponse["hourly"]>["next_sample"];

function HourlyNextSample({ sample }: { sample?: HourlyNextSampleInfo }) {
  if (!sample) {
    return <p className="factory-muted">No hourly sample queued.</p>;
  }
  return (
    <dl className="home-hourly">
      {sample.sample_id ? (
        <div>
          <dt>sample</dt>
          <dd className="mono">{sample.sample_id}</dd>
        </div>
      ) : null}
      {typeof sample.pick_index === "number" ? (
        <div>
          <dt>pick</dt>
          <dd className="mono">#{sample.pick_index}</dd>
        </div>
      ) : null}
      {sample.gex2_prompt ? (
        <div>
          <dt>prompt</dt>
          <dd>{sample.gex2_prompt}</dd>
        </div>
      ) : null}
      {sample.note ? (
        <div>
          <dt>note</dt>
          <dd className="factory-muted">{sample.note}</dd>
        </div>
      ) : null}
    </dl>
  );
}

export function HourlyFactoryPanel({ refreshToken = 0 }: { refreshToken?: number }) {
  const [summary, setSummary] = useState<HomeSummaryResponse | null>(null);
  const [schedule, setSchedule] = useState<HourlyScheduleStatus | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const sch = await fetchHourlySchedule();
      setSchedule(sch);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
    try {
      setSummary(await fetchHomeSummary());
    } catch {
      /* schedule already loaded; next-sample is optional */
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshToken]);

  return (
    <div className="sfmap-hourlies">
      {error ? <p className="home-hourly-controls__err">{error}</p> : null}
      <HourlyChainBacklogsCard refreshToken={refreshToken} />
      <p className="home-hourly-controls__steer">
        <a className="home-cta" href={factoryMapHourliesCurateHref()}>
          Steer seed stills →
        </a>
        <span className="factory-muted">
          {" "}
          · per-family bins that soft-bias hourly still picks
        </span>
      </p>
      <Panel
        title="Schedule"
        hint="Cadence, Comfy caps, and the hourly pending floor"
        footer={
          <span className="home-backlog-foot">
            <a className="home-cta" href={routeHref("queue")}>
              Queue ledger →
            </a>
            <a className="home-cta" href={routeHref("workbench")}>
              Workbench pending →
            </a>
          </span>
        }
      >
        <HourlyScheduleControls
          initial={schedule ?? summary?.hourly?.schedule ?? null}
          onSaved={(s) => setSchedule(s)}
        />
        <HourlyExploreControls
          initial={schedule ?? summary?.hourly?.schedule ?? null}
          onSaved={(s) => setSchedule(s)}
        />
        <HourlyNextSample sample={summary?.hourly?.next_sample} />
      </Panel>
    </div>
  );
}

export function HourlyHomeTeaser({
  schedule,
  nextSample,
  steer,
}: {
  schedule?: HourlyScheduleStatus | null;
  nextSample?: HourlyNextSampleInfo;
  steer?: HourlySteerTeaser | null;
}) {
  const sch = schedule?.schedule;
  const bin = steer?.bin;
  const feed = Number(bin?.feed_count || 0);
  const left = Number(bin?.counts?.later || 0);
  return (
    <>
      {hourlySuspendBanner(schedule).active ? (
        <p className="home-hourly-controls__suspend" role="status">
          {hourlySuspendBanner(schedule).text}
        </p>
      ) : null}
      <p className="home-hourly-controls__meta factory-muted">
        {sch?.enabled === false || schedule?.gpu_pause?.active
          ? "Hourlies off"
          : "Hourlies on"}
        {" · "}
        Next due {formatDue(schedule?.next_due_at)}
        {schedule?.due ? " · due now" : ""}
        {" · "}
        hourlies {num(schedule?.factory_hourly_pending)}/{num(sch?.pending_hourly_min ?? 5)}
        {" · "}
        mode {sch?.submit_mode || "auto"}
        {schedule?.explore?.target
          ? ` · exploring ${schedule.explore.target}`
          : ""}
      </p>
      <HourlyNextSample sample={nextSample} />
      <p className="home-hourly-controls__steer">
        <a className="home-cta" href={steer?.curate_href || factoryMapHourliesCurateHref()}>
          Steer seed stills →
        </a>
        <span className="factory-muted">
          {" "}
          {steer?.hint
            ? `· ${steer.hint}`
            : bin
              ? `· bin feed ${feed}${left ? ` · later ${left}` : ""}`
              : "· sort stills that hourlies should prefer"}
        </span>
      </p>
    </>
  );
}

