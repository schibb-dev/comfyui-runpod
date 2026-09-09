import type {
  ShapeFactoryMapQueueOverrides,
  WorkProductFamilyOption,
  WorkProductLoraEntry,
  WorkProductParamsValues,
} from "./types";

const PARAM_KEYS = ["frames", "steps", "overlap", "seed"] as const;

export function familyDefaultParams(
  families: WorkProductFamilyOption[],
  slug?: string | null,
): WorkProductParamsValues {
  const hit = families.find((f) => f.slug === slug);
  return { ...(hit?.params_defaults || {}) };
}

export function familyDefaultLoras(
  families: WorkProductFamilyOption[],
  slug?: string | null,
): WorkProductLoraEntry[] {
  const hit = families.find((f) => f.slug === slug);
  return [...(hit?.loras_defaults || [])];
}

export function compactParams(values?: WorkProductParamsValues | null): WorkProductParamsValues {
  const out: WorkProductParamsValues = {};
  for (const key of PARAM_KEYS) {
    const n = Number(values?.[key]);
    if (Number.isFinite(n)) out[key] = Math.trunc(n);
  }
  return out;
}

export function paramsDifferFromSeed(
  draft?: WorkProductParamsValues | null,
  seed?: WorkProductParamsValues | null,
): boolean {
  const a = compactParams(draft);
  const b = compactParams(seed);
  return PARAM_KEYS.some((key) => a[key] != null && a[key] !== b[key]);
}

export function paramsOverrideFromDraft(
  draft?: WorkProductParamsValues | null,
  seed?: WorkProductParamsValues | null,
): WorkProductParamsValues | undefined {
  const a = compactParams(draft);
  const b = compactParams(seed);
  const out: WorkProductParamsValues = {};
  for (const key of PARAM_KEYS) {
    if (a[key] != null && a[key] !== b[key]) out[key] = a[key];
  }
  return Object.keys(out).length ? out : undefined;
}

export function lorasDifferFromSeed(
  draft?: WorkProductLoraEntry[] | null,
  seed?: WorkProductLoraEntry[] | null,
): boolean {
  const a = draft || [];
  const b = seed || [];
  if (a.length !== b.length) return a.length > 0;
  return a.some((row, i) => {
    const s = b[i];
    if (!s) return true;
    return (
      String(row.lora || "") !== String(s.lora || "") ||
      Boolean(row.on) !== Boolean(s.on) ||
      Number(row.strength ?? NaN) !== Number(s.strength ?? NaN)
    );
  });
}

export function lorasOverrideFromDraft(
  draft?: WorkProductLoraEntry[] | null,
  seed?: WorkProductLoraEntry[] | null,
): { entries: WorkProductLoraEntry[] } | undefined {
  const rows = draft || [];
  if (!rows.length || !lorasDifferFromSeed(rows, seed)) return undefined;
  return { entries: rows };
}

export function loraBasename(name: string): string {
  const base = String(name || "").split(/[/\\]/).pop() || name;
  return base.replace(/\.safetensors$/i, "");
}

export function composeRuntimeOverrides(
  genFrames: number | null,
  paramDraft: WorkProductParamsValues,
  seedParams: WorkProductParamsValues,
  loraDraft: WorkProductLoraEntry[],
  seedLoras: WorkProductLoraEntry[],
): Pick<ShapeFactoryMapQueueOverrides, "parameters" | "loras"> {
  const parameters = paramsOverrideFromDraft(
    { ...paramDraft, ...(genFrames != null ? { frames: genFrames } : {}) },
    seedParams,
  );
  const loras = lorasOverrideFromDraft(loraDraft, seedLoras);
  return {
    ...(parameters ? { parameters } : {}),
    ...(loras ? { loras } : {}),
  };
}

export function paramsSummary(values?: WorkProductParamsValues | null): string {
  const v = compactParams(values);
  return [
    v.frames != null ? `frames ${v.frames}` : null,
    v.steps != null ? `steps ${v.steps}` : null,
    v.overlap != null ? `overlap ${v.overlap}` : null,
    v.seed != null ? `seed ${v.seed}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
}
