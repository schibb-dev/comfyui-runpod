import React from "react";
import { jobPromptVariantDisplayName, promptTextIsOverridden } from "./submitFamily";
import type {
  JobOverrideKind,
  JobOverrides,
  QueueComfyItem,
  QueueOverrideDetail,
  QueueOverrideLoraDiff,
  WorkProductItem,
} from "./types";

export const JOB_OVERRIDE_KINDS: JobOverrideKind[] = ["prompt", "loras", "params", "stack"];

export const JOB_OVERRIDE_LABEL: Record<JobOverrideKind, string> = {
  prompt: "prompt",
  loras: "loras",
  params: "params",
  stack: "stack",
};

export type OverrideNeed = "any" | JobOverrideKind;

export type JobOverrideSource = {
  overrides?: JobOverrides | null;
  prompt_profile?: WorkProductItem["prompt_profile"] | QueueComfyItem["prompt_profile"];
  params_profile?: WorkProductItem["params_profile"];
  loras_profile?: WorkProductItem["loras_profile"];
  glance?: {
    prompt_snowflake?: boolean | null;
    loras_snowflake?: boolean | null;
    params_snowflake?: boolean | null;
    stack_override?: boolean | null;
    stack_id?: string | null;
    overrides?: JobOverrides | null;
    override_detail?: QueueOverrideDetail | null;
  } | null;
  override_detail?: QueueOverrideDetail | null;
};

function paramsProfileIsOverridden(profile?: WorkProductItem["params_profile"]): boolean {
  if (!profile) return false;
  if (profile.snowflake) return true;
  const diffs = profile.diffs || {};
  return Object.keys(diffs).some((k) => k !== "seed" && diffs[k]);
}

export function jobOverrideFlags(item: JobOverrideSource | null | undefined): Required<JobOverrides> {
  const stamped = item?.overrides || item?.glance?.overrides;
  return {
    prompt: Boolean(
      stamped?.prompt || item?.prompt_profile?.snowflake || item?.glance?.prompt_snowflake || promptTextIsOverridden(item?.prompt_profile, item?.glance),
    ),
    loras: Boolean(stamped?.loras || item?.loras_profile?.snowflake || item?.glance?.loras_snowflake),
    params: Boolean(stamped?.params || paramsProfileIsOverridden(item?.params_profile) || item?.glance?.params_snowflake),
    stack: Boolean(stamped?.stack || item?.glance?.stack_override),
  };
}

export function jobOverrideKinds(item: JobOverrideSource | null | undefined): JobOverrideKind[] {
  const flags = jobOverrideFlags(item);
  return JOB_OVERRIDE_KINDS.filter((k) => flags[k]);
}

export function jobHasAnyOverride(item: JobOverrideSource | null | undefined): boolean {
  return jobOverrideKinds(item).length > 0;
}

export function jobMatchesOverrideNeed(item: JobOverrideSource | null | undefined, need: Iterable<OverrideNeed>): boolean {
  const wanted = new Set(Array.from(need));
  if (!wanted.size) return true;
  const flags = jobOverrideFlags(item);
  if (wanted.has("any") && (flags.prompt || flags.loras || flags.params || flags.stack)) return true;
  return JOB_OVERRIDE_KINDS.some((k) => wanted.has(k) && flags[k]);
}

export function filterWorkProductsByOverride(items: WorkProductItem[], need: Iterable<OverrideNeed>): WorkProductItem[] {
  const wanted = Array.from(need);
  if (!wanted.length) return items;
  return items.filter((it) => jobMatchesOverrideNeed(it, wanted));
}

export function filterWorkProductsByFamily(items: WorkProductItem[], family: string): WorkProductItem[] {
  const fam = family.trim().toLowerCase();
  if (!fam) return items;
  return items.filter((it) => String(it.family_slug || "").trim().toLowerCase() === fam);
}

export function overrideHaystackBits(item: JobOverrideSource | null | undefined): string[] {
  const kinds = jobOverrideKinds(item);
  if (!kinds.length) return [];
  return ["ovr", "override", "edited", ...kinds];
}

export function jobOverrideTitle(item: JobOverrideSource | null | undefined): string {
  const kinds = jobOverrideKinds(item);
  if (!kinds.length) return "";
  const names = kinds.map((k) => JOB_OVERRIDE_LABEL[k]).join(", ");
  return `Override: ${names}`;
}

export function jobOverrideSearchLabel(item: WorkProductItem): string {
  return [jobPromptVariantDisplayName(item), item.stack_id, item.spec_abbrev, item.spec_title, ...overrideHaystackBits(item)]
    .filter(Boolean)
    .join(" ");
}

export function jobOverrideDetail(item: JobOverrideSource | null | undefined): QueueOverrideDetail | null {
  return item?.override_detail || item?.glance?.override_detail || null;
}

function fmtStrength(raw: unknown): string {
  const n = Number(raw);
  if (!Number.isFinite(n)) return "—";
  return String(Math.round(n * 100) / 100);
}

export function formatOverrideLoraLine(row: QueueOverrideLoraDiff): string {
  const name = String(row.lora || "").trim() || "lora";
  const nowOn = row.on !== false;
  const bits: string[] = [name, nowOn ? "on" : "off", fmtStrength(row.strength)];
  if (row.seed_on != null || row.seed_strength != null) {
    const wasOn = row.seed_on !== false;
    bits.push(`was ${wasOn ? "on" : "off"} ${fmtStrength(row.seed_strength)}`);
  }
  return bits.join(" · ");
}

export function formatOverrideParamLine(key: string, diff: { job?: unknown; seed?: unknown }): string {
  const job = diff?.job == null || diff.job === "" ? "—" : String(diff.job);
  const seed = diff?.seed == null || diff.seed === "" ? null : String(diff.seed);
  return seed ? `${key} ${seed} → ${job}` : `${key} ${job}`;
}

export function JobOverrideBadge({
  item,
  className,
}: {
  item: JobOverrideSource | null | undefined;
  className?: string;
}) {
  const kinds = jobOverrideKinds(item);
  if (!kinds.length) return null;
  const title = jobOverrideTitle(item);
  return React.createElement(
    "span",
    {
      className: ["work-product-badge", "work-product-badge--ovr", className].filter(Boolean).join(" "),
      title,
    },
    "ovr",
    kinds.length > 1
      ? React.createElement("span", { className: "work-product-badge--ovr-kinds" }, kinds.join("·"))
      : React.createElement("span", { className: "work-product-badge--ovr-kinds" }, kinds[0]),
  );
}
