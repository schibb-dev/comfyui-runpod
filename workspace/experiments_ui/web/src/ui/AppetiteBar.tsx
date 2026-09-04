import React from "react";
import type { Appetite, AppetiteFacet } from "./types";

export const APPETITE_ORDER: { key: Appetite; label: string; short: string; glyph: string; hint: string }[] = [
  { key: "less", label: "Less", short: "z", glyph: "−", hint: "Steer away from this direction" },
  { key: "neutral", label: "Neutral", short: "x", glyph: "○", hint: "No strong pull either way" },
  { key: "more", label: "More", short: "c", glyph: "+", hint: "Want more work in this direction" },
  { key: "fast_track", label: "Fast-track", short: "v", glyph: "»", hint: "Strong pin — hourly prefers this when it picks next" },
];

export const FACETS: { key: AppetiteFacet; label: string; glyph: string; hint: string }[] = [
  { key: "both", label: "Both", glyph: "◎", hint: "Appetite for the whole result (source + look)" },
  { key: "source", label: "Source", glyph: "◻", hint: "Appetite for the source material (steers derive sources)" },
  { key: "processing", label: "Look", glyph: "✦", hint: "Appetite for the processing/look (prompt + lora)" },
];

/** z/x/c/v map to appetite states; g cycles the facet. */
export const APPETITE_KEYMAP: Record<string, Appetite> = { z: "less", x: "neutral", c: "more", v: "fast_track" };
export const APPETITE_FACET_CYCLE: AppetiteFacet[] = ["both", "source", "processing"];

export function AppetiteBar({
  appetite,
  facet,
  busy,
  onSet,
  onFacetChange,
  embedded = false,
  iconsOnly = false,
}: {
  appetite: Appetite | null | undefined;
  facet: AppetiteFacet;
  busy?: boolean;
  onSet: (state: Appetite, facet: AppetiteFacet) => void;
  onFacetChange?: (facet: AppetiteFacet) => void;
  /** When true, omit the label row (parent supplies a matching judgment header). */
  embedded?: boolean;
  /** Compact glyph-only buttons for preview popovers. */
  iconsOnly?: boolean;
}) {
  return (
    <div
      className={
        "appetite-bar" +
        (embedded ? " appetite-bar--embedded" : "") +
        (iconsOnly ? " appetite-bar--icons" : "")
      }
      role="group"
      aria-label="Appetite — do more with this"
    >
      {!embedded && !iconsOnly ? (
        <span className="appetite-bar-label" title="Direction (do more WITH this), separate from the quality star">
          Appetite
        </span>
      ) : null}
      <div className={embedded ? "drq-rate-bar" : "appetite-btns"}>
        {APPETITE_ORDER.map((a) => (
          <button
            key={a.key}
            type="button"
            className={
              (embedded ? "drq-star-btn drq-appetite-tile " : "appetite-btn ") +
              "appetite-btn--" +
              a.key +
              (appetite === a.key ? " appetite-btn--on" : "")
            }
            disabled={busy}
            title={`${a.label}: ${a.hint} (${a.short})`}
            aria-pressed={appetite === a.key}
            aria-label={`${a.label} appetite`}
            onClick={() => onSet(a.key, facet)}
          >
            {embedded ? (
              <>
                <span className="drq-star-btn__n">{a.short}</span>
                <span className="drq-star-btn__glyph" aria-hidden="true">
                  {a.glyph}
                </span>
              </>
            ) : iconsOnly ? (
              <span aria-hidden="true">{a.glyph}</span>
            ) : (
              a.label
            )}
          </button>
        ))}
      </div>
      {onFacetChange && !iconsOnly ? (
        <div
          className={embedded ? "drq-rate-bar drq-rate-bar--facet" : "appetite-facet"}
          role="group"
          aria-label="Attribute appetite to"
        >
          {FACETS.map((f) => (
            <button
              key={f.key}
              type="button"
              className={
                (embedded ? "drq-facet-btn" : "appetite-facet-btn") +
                (facet === f.key ? (embedded ? " drq-facet-btn--on" : " appetite-facet-btn--on") : "")
              }
              disabled={busy}
              title={`${f.hint} — press g to cycle`}
              aria-pressed={facet === f.key}
              onClick={() => onFacetChange(f.key)}
            >
              {iconsOnly ? <span aria-hidden="true">{f.glyph}</span> : f.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
