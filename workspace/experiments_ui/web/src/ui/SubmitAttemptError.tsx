import type { ShapeFactorySubmitAttempt } from "./types";
import { humanQueueErrorTitle, isShapeFactoryQueueError, type ShapeFactoryQueueError } from "./api";

function bindingSummary(bindings?: Record<string, string> | null): string {
  if (!bindings) return "";
  const parts = Object.entries(bindings)
    .filter(([, v]) => Boolean(v))
    .map(([k, v]) => `${k}=${v}`)
    .slice(0, 3);
  return parts.join(" · ");
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

export function RecentSubmitAttemptsStrip({
  items,
  className = "",
}: {
  items: ShapeFactorySubmitAttempt[];
  className?: string;
}) {
  if (!items.length) return null;
  return (
    <div className={`submit-attempts-strip ${className}`.trim()}>
      <div className="submit-attempts-strip__head">Recent submit errors</div>
      <ul className="submit-attempts-strip__list">
        {items.map((it) => {
          const title = humanQueueErrorTitle(String(it.error || "error"), it.family_slug);
          const binds = bindingSummary(it.bindings);
          const when = it.ts ? it.ts.replace("T", " ").replace(/\+00:00$/, "Z") : "";
          return (
            <li key={it.attempt_id || `${it.ts}-${it.family_slug}-${it.error}`} className="submit-attempts-strip__item">
              <div className="submit-attempts-strip__title">
                {title}
                {when ? <span className="submit-attempts-strip__when">{when}</span> : null}
              </div>
              {it.detail ? <div className="submit-attempts-strip__detail">{it.detail}</div> : null}
              {it.hint ? <div className="submit-attempts-strip__hint">{it.hint}</div> : null}
              <div className="submit-attempts-strip__meta">
                {[it.attempt_id ? `attempt ${it.attempt_id}` : null, binds || null].filter(Boolean).join(" · ")}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
