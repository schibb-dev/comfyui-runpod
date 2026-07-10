/**
 * Display locale for human-readable dates in the Experiments UI.
 *
 * WSL/Linux often defaults to en-GB (DD/MM/YYYY). We default to en-US unless
 * VITE_DISPLAY_LOCALE is set at build time.
 */
export const DISPLAY_LOCALE =
  (import.meta.env.VITE_DISPLAY_LOCALE as string | undefined)?.trim() || "en-US";

const DATE_TIME_FORMAT: Intl.DateTimeFormatOptions = {
  year: "numeric",
  month: "numeric",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
  second: "2-digit",
};

let _dateTimeFormatter: Intl.DateTimeFormat | null = null;

function dateTimeFormatter(): Intl.DateTimeFormat {
  if (!_dateTimeFormatter) {
    _dateTimeFormatter = new Intl.DateTimeFormat(DISPLAY_LOCALE, DATE_TIME_FORMAT);
  }
  return _dateTimeFormatter;
}

/** Unix seconds (discovery index mtime). */
export function formatUnixMtime(seconds: number): string {
  if (!seconds) return "—";
  try {
    return dateTimeFormatter().format(new Date(seconds * 1000));
  } catch {
    return "—";
  }
}

/** ISO-8601 or parseable date string from API. */
export function formatIsoDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  try {
    return dateTimeFormatter().format(d);
  } catch {
    return iso;
  }
}

export function formatInteger(n: number): string {
  return n.toLocaleString(DISPLAY_LOCALE);
}
