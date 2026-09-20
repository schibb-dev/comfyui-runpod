import { normalizeAppetiteRelpath } from "./workProductAppetite";
import type {
  DiscoveryAssetLineageAncestryNavEntry,
  DiscoveryAssetLineageResponse,
  ShapeFactoryMapJob,
  ShapeFactoryMapMediaRef,
  WorkProductBinding,
  WorkProductItem,
} from "./types";
import type { SourceOutputPair } from "./factoryMapPairs";

export type ProvenanceAppetiteRole = "source" | "parent" | "output" | "binding" | "ancestor";

export type ProvenanceAppetiteLayer = {
  id: string;
  role: ProvenanceAppetiteRole;
  label: string;
  relpath: string;
  thumbUrl?: string | null;
  url?: string | null;
  slot?: string;
};

const SKIP_SLOTS = new Set(["prompt_profile"]);

const SLOT_LABEL: Record<string, string> = {
  source_still: "Source still",
  source_video: "Source video",
  source_image: "Source image",
  identity_still: "Identity still",
  identity_anchor: "Identity",
  start_image: "Start image",
};

function slotRole(slot: string): ProvenanceAppetiteRole {
  if (/source|identity|start_image|anchor/i.test(slot)) return "source";
  return "binding";
}

function slotLabel(slot: string): string {
  return SLOT_LABEL[slot] || slot.replace(/_/g, " ");
}

function mediaRelpath(media?: { relpath?: string | null; path?: string | null } | null): string {
  const rel = normalizeAppetiteRelpath(media?.relpath);
  if (rel) return rel;
  const path = String(media?.path || "").replace(/\\/g, "/");
  if (/^(input|og|wip|output|experiments)\//i.test(path)) return normalizeAppetiteRelpath(path);
  return "";
}

function addLayer(
  layers: ProvenanceAppetiteLayer[],
  seen: Set<string>,
  layer: Omit<ProvenanceAppetiteLayer, "id">,
): void {
  const rel = normalizeAppetiteRelpath(layer.relpath);
  if (!rel || seen.has(rel)) return;
  seen.add(rel);
  layers.push({ ...layer, relpath: rel, id: rel });
}

export function layersFromWorkProduct(item: WorkProductItem): ProvenanceAppetiteLayer[] {
  const layers: ProvenanceAppetiteLayer[] = [];
  const seen = new Set<string>();
  const bindings = item.bindings || {};
  for (const [slot, binding] of Object.entries(bindings)) {
    if (SKIP_SLOTS.has(slot) || !binding) continue;
    const rel = mediaRelpath(binding);
    if (!rel) continue;
    addLayer(layers, seen, {
      role: slotRole(slot),
      label: slotLabel(slot),
      relpath: rel,
      thumbUrl: binding.thumb_url,
      url: binding.url,
      slot,
    });
  }
  const parent = normalizeAppetiteRelpath(item.parent_output_relpath);
  if (parent) {
    addLayer(layers, seen, {
      role: "parent",
      label: "Parent output",
      relpath: parent,
      thumbUrl: item.parent_output_thumb_url,
      url: item.parent_output_url,
    });
  }
  const output = normalizeAppetiteRelpath(item.output_relpath);
  if (output) {
    addLayer(layers, seen, {
      role: "output",
      label: "This output",
      relpath: output,
      thumbUrl: item.output_thumb_url,
      url: item.output_url,
    });
  }
  return layers;
}

export function layersFromFactoryPair(opts: {
  source?: ShapeFactoryMapMediaRef | null;
  output?: ShapeFactoryMapMediaRef | null;
  bindings?: Record<string, ShapeFactoryMapMediaRef | WorkProductBinding> | null;
  job?: ShapeFactoryMapJob | null;
}): ProvenanceAppetiteLayer[] {
  const layers: ProvenanceAppetiteLayer[] = [];
  const seen = new Set<string>();
  const bindings = opts.bindings || opts.job?.bindings || {};
  for (const [slot, binding] of Object.entries(bindings)) {
    if (SKIP_SLOTS.has(slot) || !binding) continue;
    const rel = mediaRelpath(binding);
    if (!rel) continue;
    addLayer(layers, seen, {
      role: slotRole(slot),
      label: slotLabel(slot),
      relpath: rel,
      thumbUrl: binding.thumb_url,
      url: binding.url,
      slot,
    });
  }
  const sourceRel = mediaRelpath(opts.source);
  if (sourceRel) {
    addLayer(layers, seen, {
      role: "source",
      label: opts.source?.basename ? `Source · ${opts.source.basename}` : "Source",
      relpath: sourceRel,
      thumbUrl: opts.source?.thumb_url,
      url: opts.source?.url,
    });
  }
  const outputRel = mediaRelpath(opts.output);
  if (outputRel) {
    addLayer(layers, seen, {
      role: "output",
      label: "This output",
      relpath: outputRel,
      thumbUrl: opts.output?.thumb_url,
      url: opts.output?.url,
    });
  } else {
    for (const out of opts.job?.outputs || []) {
      const rel = mediaRelpath(out);
      if (!rel) continue;
      addLayer(layers, seen, {
        role: "output",
        label: "This output",
        relpath: rel,
        thumbUrl: out.thumb_url,
        url: out.url,
      });
      break;
    }
  }
  return layers;
}

export function layersFromLineage(
  data: DiscoveryAssetLineageResponse | DiscoveryAssetLineageAncestryNavEntry[] | null | undefined,
): ProvenanceAppetiteLayer[] {
  const chain: DiscoveryAssetLineageAncestryNavEntry[] = Array.isArray(data)
    ? data
    : data?.provenance_chain || [...(data?.ancestry_nav || [])].reverse();
  const layers: ProvenanceAppetiteLayer[] = [];
  const seen = new Set<string>();
  for (const entry of chain) {
    const item = entry.item;
    const rel = normalizeAppetiteRelpath(
      item?.relpath || item?.workspace_relpath || item?.video_relpath || "",
    );
    if (!rel) continue;
    const isSeed = entry.role === "seed";
    const isSource = Boolean(entry.external) || entry.role === "source" || entry.role === "root";
    addLayer(layers, seen, {
      role: isSeed ? "output" : isSource ? "source" : "ancestor",
      label: isSeed
        ? "This output"
        : isSource
          ? item?.name
            ? `Source · ${item.name}`
            : "Source"
          : item?.name
            ? `Ancestor · ${item.name}`
            : "Ancestor",
      relpath: rel,
      thumbUrl: item?.thumb_url,
      url: item?.url || item?.video_url,
    });
  }
  return layers;
}

export function mergeAppetiteLayers(...groups: Array<ProvenanceAppetiteLayer[] | null | undefined>): ProvenanceAppetiteLayer[] {
  const byRel = new Map<string, ProvenanceAppetiteLayer>();
  const order: string[] = [];
  for (const group of groups) {
    for (const layer of group || []) {
      const rel = normalizeAppetiteRelpath(layer.relpath);
      if (!rel) continue;
      const prev = byRel.get(rel);
      if (!prev) {
        byRel.set(rel, { ...layer, relpath: rel, id: rel });
        order.push(rel);
        continue;
      }
      byRel.set(rel, {
        ...prev,
        ...layer,
        relpath: rel,
        id: rel,
        label: layer.role === "output" || prev.role !== "output" ? layer.label || prev.label : prev.label,
        role: layer.role === "output" || prev.role === "output" ? "output" : layer.role || prev.role,
        thumbUrl: layer.thumbUrl || prev.thumbUrl,
        url: layer.url || prev.url,
        slot: layer.slot || prev.slot,
      });
    }
  }
  const items = order.map((rel) => byRel.get(rel)!).filter(Boolean);
  const rest = items.filter((layer) => layer.role !== "output");
  const outputs = items.filter((layer) => layer.role === "output");
  return [...rest, ...outputs];
}

export function pairFromFactorySelection(opts: {
  pair?: SourceOutputPair | null;
  job?: ShapeFactoryMapJob | null;
}): ReturnType<typeof layersFromFactoryPair> {
  return layersFromFactoryPair({
    source: opts.pair?.source,
    output: opts.pair?.output,
    bindings: opts.pair?.bindings || opts.job?.bindings,
    job: opts.job,
  });
}
