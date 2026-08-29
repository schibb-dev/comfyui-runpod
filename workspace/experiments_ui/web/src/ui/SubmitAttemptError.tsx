import type { ShapeFactorySubmitAttempt } from "./types";
import { humanQueueErrorTitle, isShapeFactoryQueueError, type ShapeFactoryQueueError } from "./api";
import { queueHref, stripDownloadCopySuffix, workbenchHref } from "./discoveryDeepLink";

function bindingSummary(bindings?: Record<string, string> | null): string {
  if (!bindings) return "";
  const parts = Object.entries(bindings)
    .filter(([, v]) => Boolean(v))
    .map(([k, v]) => `${k}=${v}`)
    .slice(0, 3);
  return parts.join(" · ");
}

function formatAttemptWhen(ts?: string): string {
  if (!ts) return "";
  return ts.replace("T", " ").replace(/\+00:00$/, "Z");
}

function filesUrlFromRel(relpath: string): string {
  const norm = relpath.replace(/^\/+/, "").replace(/\\/g, "/");
  return "/files/" + norm.split("/").map(encodeURIComponent).join("/");
}

/** Best-effort thumb for a submit attempt (API thumb_url, or guess from bindings). */
function attemptThumbUrl(it: ShapeFactorySubmitAttempt): string | null {
  const direct = String(it.thumb_url || "").trim();
  if (direct) return direct;
  let rel = String(it.media_relpath || "").trim().replace(/\\/g, "/");
  if (!rel && it.bindings) {
    const preferred = [
      "source_still",
      "source_image",
      "identity_still",
      "start_image",
      "source_video",
      "parent_video",
      "source",
    ];
    for (const key of preferred) {
      const v = String(it.bindings[key] || "").trim();
      if (v) {
        const cleaned = stripDownloadCopySuffix(v);
        rel = cleaned.includes("/")
          ? cleaned
          : /\.(jpe?g|png|webp|gif)$/i.test(cleaned)
            ? `input/${cleaned}`
            : cleaned;
        break;
      }
    }
  }
  if (!rel) return null;
  rel = stripDownloadCopySuffix(rel);
  if (/\.(mp4|webm|mov|mkv)$/i.test(rel)) {
    rel = rel.replace(/\.(mp4|webm|mov|mkv)$/i, ".png");
  }
  if (!/\.(jpe?g|png|webp|gif)$/i.test(rel)) return null;
  return filesUrlFromRel(rel);
}

export function SubmitQueueErrorPanel({
  error,
  className = "",
}: {
  error: ShapeFactoryQueueError | Error | string | null;
  className?: string;
}) {
  if (!error) return null;
  if (typeof error === "string") {
    return (
      <div className={`submit-attempt-error ${className}`.trim()} role="alert">
        <div className="submit-attempt-error__title">Submit failed</div>
        <div className="submit-attempt-error__detail">{error}</div>
      </div>
    );
  }
  if (!isShapeFactoryQueueError(error)) {
    return (
      <div className={`submit-attempt-error ${className}`.trim()} role="alert">
        <div className="submit-attempt-error__title">Submit failed</div>
        <div className="submit-attempt-error__detail">{error.message}</div>
      </div>
    );
  }
  const binds = bindingSummary(error.bindings);
  return (
    <div className={`submit-attempt-error ${className}`.trim()} role="alert">
      <div className="submit-attempt-error__title">
        {humanQueueErrorTitle(error.errorCode, error.familySlug)}
      </div>
      {error.detail ? <div className="submit-attempt-error__detail">{error.detail}</div> : null}
      {error.hint ? <div className="submit-attempt-error__hint">{error.hint}</div> : null}
      <div className="submit-attempt-error__meta">
        {[
          error.attemptId ? `attempt ${error.attemptId}` : null,
          binds || null,
          error.pathHint ? `path ${error.pathHint}` : null,
        ]
          .filter(Boolean)
          .join(" · ")}
      </div>
    </div>
  );
}

/** Errors-only strip (legacy callers). Prefer RecentSubmitsPanel. */
export function RecentSubmitAttemptsStrip({
  items,
  className = "",
}: {
  items: ShapeFactorySubmitAttempt[];
  className?: string;
}) {
  return <RecentSubmitsPanel items={items} className={className} errorsOnly />;
}

/** Recent submit attempts (success + failure) with Queue / Workbench deep links when keys exist. */
export function RecentSubmitsPanel({
  items,
  className = "",
  errorsOnly = false,
  title,
}: {
  items: ShapeFactorySubmitAttempt[];
  className?: string;
  errorsOnly?: boolean;
  title?: string;
}) {
  const rows = errorsOnly ? items.filter((it) => it.ok === false) : items;
  if (!rows.length) return null;
  const head = title || (errorsOnly ? "Recent submit errors" : "Recent submits");
  return (
    <div className={`submit-attempts-strip ${className}`.trim()}>
      <div className="submit-attempts-strip__head">{head}</div>
      <ul className="submit-attempts-strip__list">
        {rows.map((it) => {
          const ok = it.ok !== false;
          const titleText = ok
            ? it.family_slug
              ? `Queued · ${it.family_slug}`
              : "Queued"
            : humanQueueErrorTitle(String(it.error || "error"), it.family_slug);
          const binds = bindingSummary(it.bindings);
          const when = formatAttemptWhen(it.ts);
          const jobKey = String(it.job_key || "").trim();
          const promptId = String(it.prompt_id || "").trim();
          const wb = jobKey || promptId ? workbenchHref({ jobKey: jobKey || null, promptId: promptId || null }) : null;
          const qq = jobKey || promptId ? queueHref({ jobKey: jobKey || null, promptId: promptId || null }) : null;
          const thumb = attemptThumbUrl(it);
          return (
            <li
              key={it.attempt_id || `${it.ts}-${it.family_slug}-${it.error || "ok"}`}
              className={`submit-attempts-strip__item${ok ? " submit-attempts-strip__item--ok" : ""}`}
            >
              <div className="submit-attempts-strip__row">
                <div className="submit-attempts-strip__thumb" aria-hidden={thumb ? undefined : true}>
                  {thumb ? (
                    <img className="submit-attempts-strip__thumb-img" src={thumb} alt="" loading="lazy" />
                  ) : (
                    <div className="submit-attempts-strip__thumb-empty" />
                  )}
                </div>
                <div className="submit-attempts-strip__body">
                  <div className="submit-attempts-strip__title">
                    <span className={`submit-attempts-strip__status${ok ? " is-ok" : " is-err"}`}>
                      {ok ? "ok" : "err"}
                    </span>
                    {titleText}
                    {when ? <span className="submit-attempts-strip__when">{when}</span> : null}
                  </div>
                  {!ok && it.detail ? <div className="submit-attempts-strip__detail">{it.detail}</div> : null}
                  {!ok && it.hint ? <div className="submit-attempts-strip__hint">{it.hint}</div> : null}
                  <div className="submit-attempts-strip__meta">
                    {[
                      it.attempt_id ? `attempt ${it.attempt_id}` : null,
                      jobKey ? `job ${jobKey}` : null,
                      promptId ? `prompt ${promptId.slice(0, 8)}…` : null,
                      binds || null,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </div>
                  {wb || qq ? (
                    <div className="submit-attempts-strip__links">
                      {wb ? (
                        <a className="drt-btn" href={wb}>
                          Workbench
                        </a>
                      ) : null}
                      {qq ? (
                        <a className="drt-btn" href={qq}>
                          Queue
                        </a>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
