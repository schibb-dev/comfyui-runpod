import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { mutateShapeFactoryPromptVariant } from "./api";
import { loadFamiliesBootstrap } from "./shapeFactorySessionCache";
import type { WorkProductFamilyPromptProfile } from "./types";

/** Apply an optimistic / sticky family default id onto catalog rows for picker labels. */
export function withFamilyDefaultOverride(
  profiles: WorkProductFamilyPromptProfile[],
  defaultVariantId?: string | null,
): WorkProductFamilyPromptProfile[] {
  const want = String(defaultVariantId || "").trim();
  if (!want) return profiles;
  return (profiles || []).map((p) => ({
    ...p,
    is_default: Boolean(p.variant_id && p.variant_id === want),
  }));
}

/**
 * Catalog designation control — sits beside a variant picker.
 * Does not change the job’s selected variant; only reassigns family default.
 */
export function PromptVariantDefaultButton({
  familySlug,
  selected,
  disabled,
  onDone,
}: {
  familySlug: string;
  selected?: WorkProductFamilyPromptProfile | null;
  disabled?: boolean;
  /** Called after a successful designation (families bootstrap already force-refreshed). */
  onDone?: (variantId: string) => void | Promise<void>;
}) {
  const [err, setErr] = useState<string | null>(null);
  const family = String(familySlug || "").trim();
  const variantId = String(selected?.variant_id || "").trim();
  const alreadyDefault = Boolean(selected?.is_default);
  const canSet = Boolean(family && variantId && !alreadyDefault && !disabled);

  const mut = useMutation({
    mutationFn: () =>
      mutateShapeFactoryPromptVariant({
        action: "set_default",
        family,
        variant_id: variantId,
      }),
    onSuccess: async () => {
      setErr(null);
      try {
        await loadFamiliesBootstrap({ force: true });
      } catch {
        /* picker labels still update via onDone optimistic path */
      }
      await onDone?.(variantId);
    },
    onError: (e) => setErr(e instanceof Error ? e.message : String(e)),
  });

  if (!family || !selected) return null;
  if (alreadyDefault) {
    return (
      <span className="prompt-variant-default-btn prompt-variant-default-btn--current" title="Family default designation">
        Family default
      </span>
    );
  }
  if (!variantId) {
    return (
      <span
        className="prompt-variant-default-btn prompt-variant-default-btn--disabled"
        title="This catalog entry has no variant id yet — run prompt-catalog backfill"
      >
        No id
      </span>
    );
  }

  return (
    <span className="prompt-variant-default-btn-wrap">
      <button
        type="button"
        className="prompt-variant-default-btn"
        disabled={!canSet || mut.isPending}
        title="Designate this catalog variant as the family default for new jobs (does not change this job’s selection)"
        onClick={() => void mut.mutateAsync()}
      >
        {mut.isPending ? "Setting…" : "Use as family default"}
      </button>
      {err ? (
        <span className="prompt-variant-default-btn__err" title={err}>
          failed
        </span>
      ) : null}
    </span>
  );
}
