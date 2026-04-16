import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";

/** Comfy API prompt: node id → { class_type, inputs } */
export type ComfyPromptMap = Record<string, unknown>;

function _sortNodeIds(ids: string[]): string[] {
  return [...ids].sort((a, b) => {
    const na = Number.parseInt(a, 10);
    const nb = Number.parseInt(b, 10);
    if (String(na) === a && String(nb) === b && !Number.isNaN(na) && !Number.isNaN(nb)) return na - nb;
    return a.localeCompare(b);
  });
}

function _nodeInputs(prompt: ComfyPromptMap, nodeId: string): Record<string, unknown> | null {
  const node = prompt[nodeId];
  if (typeof node !== "object" || node === null) return null;
  const ins = (node as { inputs?: unknown }).inputs;
  if (typeof ins !== "object" || ins === null) return null;
  return ins as Record<string, unknown>;
}

function _classType(prompt: ComfyPromptMap, nodeId: string): string {
  const node = prompt[nodeId];
  if (typeof node !== "object" || node === null) return "";
  const ct = (node as { class_type?: unknown }).class_type;
  return typeof ct === "string" ? ct : "";
}

function _clipEncodeNodes(prompt: ComfyPromptMap): { nodeId: string; text: string }[] {
  const out: { nodeId: string; text: string }[] = [];
  for (const nodeId of _sortNodeIds(Object.keys(prompt))) {
    const ct = _classType(prompt, nodeId);
    if (!ct.includes("CLIPTextEncode")) continue;
    const ins = _nodeInputs(prompt, nodeId);
    if (!ins) continue;
    const t = ins.text;
    const text = typeof t === "string" ? t : "";
    out.push({ nodeId, text });
  }
  return out;
}

function _findSamplerQuick(prompt: ComfyPromptMap): {
  nodeId: string;
  classType: string;
  cfg: number;
  steps: number;
  denoise: number | null;
  keys: { cfg: string; steps: string; denoise?: string };
} | null {
  const candidates: string[] = [];
  for (const nodeId of _sortNodeIds(Object.keys(prompt))) {
    const ct = _classType(prompt, nodeId);
    if (ct !== "KSampler" && ct !== "KSamplerAdvanced") continue;
    const ins = _nodeInputs(prompt, nodeId);
    if (!ins) continue;
    const cfg = ins.cfg;
    const steps = ins.steps;
    if (typeof cfg !== "number" || !Number.isFinite(cfg)) continue;
    if (typeof steps !== "number" || !Number.isFinite(steps)) continue;
    candidates.push(nodeId);
  }
  if (!candidates.length) return null;
  const nodeId = candidates[0];
  const ins = _nodeInputs(prompt, nodeId)!;
  const cfg = typeof ins.cfg === "number" && Number.isFinite(ins.cfg) ? ins.cfg : 8;
  const steps = typeof ins.steps === "number" && Number.isFinite(ins.steps) ? Math.round(ins.steps) : 20;
  const denoiseRaw = ins.denoise;
  const hasDenoise = typeof denoiseRaw === "number" && Number.isFinite(denoiseRaw);
  return {
    nodeId,
    classType: _classType(prompt, nodeId),
    cfg,
    steps,
    denoise: hasDenoise ? (denoiseRaw as number) : null,
    keys: { cfg: "cfg", steps: "steps", ...(hasDenoise ? { denoise: "denoise" } : {}) },
  };
}

function _findTeaCacheSpeed(prompt: ComfyPromptMap): { nodeId: string; classType: string; rel_l1: number } | null {
  for (const nodeId of _sortNodeIds(Object.keys(prompt))) {
    const ct = _classType(prompt, nodeId);
    if (!/teacache/i.test(ct)) continue;
    const ins = _nodeInputs(prompt, nodeId);
    if (!ins) continue;
    const v = ins.rel_l1_thresh;
    if (typeof v !== "number" || !Number.isFinite(v)) continue;
    return { nodeId, classType: ct, rel_l1: v };
  }
  return null;
}

type PowerLoraSlot = {
  nodeId: string;
  slotKey: string;
  name: string;
  on: boolean;
  strength: number;
  strengthTwo: number | null;
};

type StandardLoraRow = {
  nodeId: string;
  classType: string;
  name: string;
  strengthModel: number;
  strengthClip: number | null;
};

function _isLoraSlotObj(v: unknown): v is { on?: unknown; lora?: unknown; strength?: unknown; strengthTwo?: unknown } {
  return typeof v === "object" && v !== null && !Array.isArray(v) && "lora" in (v as object);
}

function _collectPowerLoraSlots(prompt: ComfyPromptMap): PowerLoraSlot[] {
  const slots: PowerLoraSlot[] = [];
  for (const nodeId of _sortNodeIds(Object.keys(prompt))) {
    const ct = _classType(prompt, nodeId);
    if (!ct.toLowerCase().includes("power lora loader")) continue;
    const ins = _nodeInputs(prompt, nodeId);
    if (!ins) continue;
    for (const key of Object.keys(ins).sort((a, b) => a.localeCompare(b))) {
      if (!/^lora_\d+$/i.test(key)) continue;
      const v = ins[key];
      if (!_isLoraSlotObj(v)) continue;
      const name = typeof v.lora === "string" ? v.lora : "";
      const on = Boolean(v.on);
      const strength = typeof v.strength === "number" && Number.isFinite(v.strength) ? v.strength : 1;
      const st2 = v.strengthTwo;
      const strengthTwo = typeof st2 === "number" && Number.isFinite(st2) ? st2 : null;
      slots.push({ nodeId, slotKey: key, name, on, strength, strengthTwo });
    }
  }
  return slots;
}

function _collectStandardLoras(prompt: ComfyPromptMap): StandardLoraRow[] {
  const rows: StandardLoraRow[] = [];
  for (const nodeId of _sortNodeIds(Object.keys(prompt))) {
    const ct = _classType(prompt, nodeId);
    if (!ct.includes("LoraLoader")) continue;
    if (ct.toLowerCase().includes("power lora")) continue;
    const ins = _nodeInputs(prompt, nodeId);
    if (!ins) continue;
    const nameRaw = ins.lora_name ?? ins.lora;
    const name = typeof nameRaw === "string" ? nameRaw : "";
    const sm = ins.strength_model;
    const sc = ins.strength_clip;
    const strengthModel = typeof sm === "number" && Number.isFinite(sm) ? sm : 1;
    const strengthClip = typeof sc === "number" && Number.isFinite(sc) ? sc : null;
    rows.push({ nodeId, classType: ct, name, strengthModel, strengthClip });
  }
  return rows;
}

function _summarizePrompt(text: string, maxLen = 96): string {
  const t = text.replace(/\s+/g, " ").trim();
  if (!t) return "(empty)";
  if (t.length <= maxLen) return t;
  return `${t.slice(0, maxLen - 1)}…`;
}

function SliderNumRow({
  label,
  value,
  min,
  max,
  step,
  disabled,
  onChange,
  intMode,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  disabled?: boolean;
  intMode?: boolean;
  onChange: (n: number) => void;
}) {
  const [text, setText] = useState(() => String(intMode ? Math.round(value) : value));
  useEffect(() => {
    setText(String(intMode ? Math.round(value) : value));
  }, [value, intMode]);
  return (
    <div className="discovery-comfy-q-slider-row">
      <div className="discovery-comfy-q-slider-label">{label}</div>
      <input
        type="range"
        className="discovery-comfy-q-range"
        disabled={disabled}
        min={min}
        max={max}
        step={step}
        value={Math.min(max, Math.max(min, value))}
        onChange={(e) => {
          const raw = Number.parseFloat(e.target.value);
          const n = intMode ? Math.round(raw) : raw;
          if (Number.isFinite(n)) onChange(n);
        }}
      />
      <input
        type="number"
        className="discovery-comfy-q-num"
        disabled={disabled}
        step={step}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={() => {
          const raw = intMode ? Number.parseInt(text, 10) : Number.parseFloat(text);
          if (!Number.isFinite(raw)) {
            setText(String(intMode ? Math.round(value) : value));
            return;
          }
          const clamped = Math.min(max, Math.max(min, raw));
          onChange(intMode ? Math.round(clamped) : clamped);
          setText(String(intMode ? Math.round(clamped) : clamped));
        }}
      />
    </div>
  );
}

type QuickDialog =
  | { kind: "pos"; nodeId: string }
  | { kind: "neg"; nodeId: string }
  | { kind: "prompt"; nodeId: string }
  | { kind: "cfg" }
  | { kind: "steps" }
  | { kind: "denoise" }
  | { kind: "speed" }
  | { kind: "lora" };

export function DiscoveryComfyQuickEditsSection({
  promptDraft,
  setPromptInput,
  disabled,
}: {
  promptDraft: ComfyPromptMap;
  setPromptInput: (nodeId: string, inputKey: string, value: unknown) => void;
  disabled?: boolean;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [dlg, setDlg] = useState<QuickDialog | null>(null);

  const clipSlots = useMemo(() => _clipEncodeNodes(promptDraft), [promptDraft]);
  const posSlot = clipSlots[0] ?? null;
  const negSlot = clipSlots[1] ?? null;
  const extraClip = clipSlots.slice(2);

  const sampler = useMemo(() => _findSamplerQuick(promptDraft), [promptDraft]);
  const speed = useMemo(() => _findTeaCacheSpeed(promptDraft), [promptDraft]);
  const powerSlots = useMemo(() => _collectPowerLoraSlots(promptDraft), [promptDraft]);
  const standardLoras = useMemo(() => _collectStandardLoras(promptDraft), [promptDraft]);

  const openDlg = useCallback((d: QuickDialog) => {
    setDlg(d);
    queueMicrotask(() => dialogRef.current?.showModal());
  }, []);

  const closeDlg = useCallback(() => {
    dialogRef.current?.close();
    setDlg(null);
  }, []);

  useEffect(() => {
    const el = dialogRef.current;
    if (!el) return;
    const onClose = () => setDlg(null);
    el.addEventListener("close", onClose);
    return () => el.removeEventListener("close", onClose);
  }, []);

  const loraSummary = useMemo(() => {
    const parts: string[] = [];
    if (powerSlots.length) {
      const onN = powerSlots.filter((s) => s.on).length;
      parts.push(`${powerSlots.length} Power LoRA slots (${onN} on)`);
    }
    if (standardLoras.length) {
      parts.push(`${standardLoras.length} LoRA loader node(s)`);
    }
    if (!parts.length) return "No LoRA nodes detected in this prompt.";
    return parts.join(" · ");
  }, [powerSlots, standardLoras]);

  const hasAnyQuick =
    Boolean(posSlot) ||
    Boolean(negSlot) ||
    Boolean(sampler) ||
    Boolean(speed) ||
    powerSlots.length > 0 ||
    standardLoras.length > 0;

  const renderDialogBody = () => {
    if (!dlg) return null;
    if (dlg.kind === "pos" && posSlot) {
      return (
        <>
          <h3 className="discovery-comfy-q-dlg-title">Positive prompt</h3>
          <p className="discovery-comfy-q-dlg-hint">Node {posSlot.nodeId}</p>
          <textarea
            className="discovery-comfy-q-dlg-textarea mono"
            rows={14}
            spellCheck={false}
            disabled={disabled}
            value={posSlot.text}
            onChange={(e) => setPromptInput(posSlot.nodeId, "text", e.target.value)}
          />
        </>
      );
    }
    if (dlg.kind === "neg" && negSlot) {
      return (
        <>
          <h3 className="discovery-comfy-q-dlg-title">Negative prompt</h3>
          <p className="discovery-comfy-q-dlg-hint">Node {negSlot.nodeId}</p>
          <textarea
            className="discovery-comfy-q-dlg-textarea mono"
            rows={12}
            spellCheck={false}
            disabled={disabled}
            value={negSlot.text}
            onChange={(e) => setPromptInput(negSlot.nodeId, "text", e.target.value)}
          />
        </>
      );
    }
    if (dlg.kind === "prompt") {
      const slot = clipSlots.find((c) => c.nodeId === dlg.nodeId);
      if (!slot) return null;
      return (
        <>
          <h3 className="discovery-comfy-q-dlg-title">Prompt (node {slot.nodeId})</h3>
          <textarea
            className="discovery-comfy-q-dlg-textarea mono"
            rows={12}
            spellCheck={false}
            disabled={disabled}
            value={slot.text}
            onChange={(e) => setPromptInput(slot.nodeId, "text", e.target.value)}
          />
        </>
      );
    }
    if (dlg.kind === "cfg" && sampler) {
      const { nodeId, cfg, keys } = sampler;
      return (
        <>
          <h3 className="discovery-comfy-q-dlg-title">CFG</h3>
          <p className="discovery-comfy-q-dlg-hint mono">
            Node {nodeId} · {sampler.classType}
          </p>
          <SliderNumRow
            label="CFG"
            value={cfg}
            min={1}
            max={30}
            step={0.5}
            disabled={disabled}
            onChange={(n) => setPromptInput(nodeId, keys.cfg, n)}
          />
        </>
      );
    }
    if (dlg.kind === "steps" && sampler) {
      const { nodeId, steps, keys } = sampler;
      return (
        <>
          <h3 className="discovery-comfy-q-dlg-title">Steps</h3>
          <p className="discovery-comfy-q-dlg-hint mono">
            Node {nodeId} · {sampler.classType}
          </p>
          <SliderNumRow
            label="Steps"
            value={steps}
            min={1}
            max={150}
            step={1}
            intMode
            disabled={disabled}
            onChange={(n) => setPromptInput(nodeId, keys.steps, n)}
          />
        </>
      );
    }
    if (dlg.kind === "denoise" && sampler && sampler.keys.denoise) {
      const { nodeId, denoise, keys } = sampler;
      return (
        <>
          <h3 className="discovery-comfy-q-dlg-title">Denoise</h3>
          <p className="discovery-comfy-q-dlg-hint mono">
            Node {nodeId} · {sampler.classType}
          </p>
          <SliderNumRow
            label="Denoise"
            value={denoise ?? 1}
            min={0}
            max={1}
            step={0.01}
            disabled={disabled}
            onChange={(n) => setPromptInput(nodeId, keys.denoise!, n)}
          />
        </>
      );
    }
    if (dlg.kind === "speed" && speed) {
      return (
        <>
          <h3 className="discovery-comfy-q-dlg-title">Speed (TeaCache)</h3>
          <p className="discovery-comfy-q-dlg-hint mono">
            Node {speed.nodeId} · {speed.classType} · rel_l1_thresh (lower often = stronger cache)
          </p>
          <SliderNumRow
            label="rel_l1_thresh"
            value={speed.rel_l1}
            min={0}
            max={0.5}
            step={0.005}
            disabled={disabled}
            onChange={(n) => setPromptInput(speed.nodeId, "rel_l1_thresh", n)}
          />
        </>
      );
    }
    if (dlg.kind === "lora") {
      return (
        <>
          <h3 className="discovery-comfy-q-dlg-title">LoRA stack</h3>
          <p className="discovery-comfy-q-dlg-hint">Power LoRA slots and standard LoRA loaders in this graph.</p>
          {powerSlots.length ? (
            <div className="discovery-comfy-q-lora-block">
              <div className="discovery-comfy-q-lora-block-title">Power LoRA Loader</div>
              {powerSlots.map((s) => (
                <div key={`${s.nodeId}:${s.slotKey}`} className="discovery-comfy-q-lora-slot">
                  <label className="discovery-comfy-q-lora-name mono" title={s.name}>
                    <input
                      type="checkbox"
                      checked={s.on}
                      disabled={disabled}
                      onChange={(e) => {
                        const ins = _nodeInputs(promptDraft, s.nodeId);
                        const cur = ins?.[s.slotKey];
                        if (typeof cur !== "object" || cur === null || Array.isArray(cur)) return;
                        setPromptInput(s.nodeId, s.slotKey, { ...(cur as Record<string, unknown>), on: e.target.checked });
                      }}
                    />
                    <span>{s.name || s.slotKey}</span>
                  </label>
                  <SliderNumRow
                    label="Strength"
                    value={s.strength}
                    min={-4}
                    max={4}
                    step={0.05}
                    disabled={disabled}
                    onChange={(n) => {
                      const ins = _nodeInputs(promptDraft, s.nodeId);
                      const cur = ins?.[s.slotKey];
                      if (typeof cur !== "object" || cur === null || Array.isArray(cur)) return;
                      setPromptInput(s.nodeId, s.slotKey, { ...(cur as Record<string, unknown>), strength: n });
                    }}
                  />
                </div>
              ))}
            </div>
          ) : null}
          {standardLoras.length ? (
            <div className="discovery-comfy-q-lora-block">
              <div className="discovery-comfy-q-lora-block-title">LoRA loaders</div>
              {standardLoras.map((r) => (
                <div key={r.nodeId} className="discovery-comfy-q-lora-slot">
                  <div className="discovery-comfy-q-lora-name mono" title={r.name}>
                    {r.classType} · {r.name || r.nodeId}
                  </div>
                  <SliderNumRow
                    label="strength_model"
                    value={r.strengthModel}
                    min={-4}
                    max={4}
                    step={0.05}
                    disabled={disabled}
                    onChange={(n) => setPromptInput(r.nodeId, "strength_model", n)}
                  />
                  {r.strengthClip != null ? (
                    <SliderNumRow
                      label="strength_clip"
                      value={r.strengthClip}
                      min={-4}
                      max={4}
                      step={0.05}
                      disabled={disabled}
                      onChange={(n) => setPromptInput(r.nodeId, "strength_clip", n)}
                    />
                  ) : null}
                </div>
              ))}
            </div>
          ) : null}
        </>
      );
    }
    return null;
  };

  return (
    <div className="discovery-comfy-q-root">
      <div className="discovery-comfy-q-head">Quick edits</div>
      {!hasAnyQuick ? (
        <p className="discovery-comfy-q-empty">No quick-edit targets found (CLIP / KSampler / TeaCache / LoRA).</p>
      ) : (
        <div className="discovery-comfy-q-cards">
          {posSlot ? (
            <div className="discovery-comfy-q-card">
              <div className="discovery-comfy-q-card-title">Positive prompt</div>
              <div className="discovery-comfy-q-card-summary">{_summarizePrompt(posSlot.text)}</div>
              <button type="button" className="discovery-comfy-q-edit" disabled={disabled} onClick={() => openDlg({ kind: "pos", nodeId: posSlot.nodeId })}>
                Edit
              </button>
            </div>
          ) : null}
          {negSlot ? (
            <div className="discovery-comfy-q-card">
              <div className="discovery-comfy-q-card-title">Negative prompt</div>
              <div className="discovery-comfy-q-card-summary">{_summarizePrompt(negSlot.text)}</div>
              <button type="button" className="discovery-comfy-q-edit" disabled={disabled} onClick={() => openDlg({ kind: "neg", nodeId: negSlot.nodeId })}>
                Edit
              </button>
            </div>
          ) : null}
          {extraClip.map((c) => (
            <div key={c.nodeId} className="discovery-comfy-q-card">
              <div className="discovery-comfy-q-card-title">CLIP text · node {c.nodeId}</div>
              <div className="discovery-comfy-q-card-summary">{_summarizePrompt(c.text)}</div>
              <button type="button" className="discovery-comfy-q-edit" disabled={disabled} onClick={() => openDlg({ kind: "prompt", nodeId: c.nodeId })}>
                Edit
              </button>
            </div>
          ))}
          {sampler ? (
            <div className="discovery-comfy-q-card">
              <div className="discovery-comfy-q-card-title">CFG</div>
              <div className="discovery-comfy-q-card-summary mono">
                {sampler.cfg} · node {sampler.nodeId} · {sampler.classType}
              </div>
              <button type="button" className="discovery-comfy-q-edit" disabled={disabled} onClick={() => openDlg({ kind: "cfg" })}>
                Edit
              </button>
            </div>
          ) : null}
          {sampler ? (
            <div className="discovery-comfy-q-card">
              <div className="discovery-comfy-q-card-title">Steps</div>
              <div className="discovery-comfy-q-card-summary mono">{Math.round(sampler.steps)} steps</div>
              <button type="button" className="discovery-comfy-q-edit" disabled={disabled} onClick={() => openDlg({ kind: "steps" })}>
                Edit
              </button>
            </div>
          ) : null}
          {sampler?.keys.denoise ? (
            <div className="discovery-comfy-q-card">
              <div className="discovery-comfy-q-card-title">Denoise</div>
              <div className="discovery-comfy-q-card-summary mono">{(sampler.denoise ?? 1).toFixed(2)}</div>
              <button type="button" className="discovery-comfy-q-edit" disabled={disabled} onClick={() => openDlg({ kind: "denoise" })}>
                Edit
              </button>
            </div>
          ) : null}
          {speed ? (
            <div className="discovery-comfy-q-card">
              <div className="discovery-comfy-q-card-title">Speed (TeaCache)</div>
              <div className="discovery-comfy-q-card-summary mono">
                rel_l1_thresh {speed.rel_l1} · {speed.classType}
              </div>
              <button type="button" className="discovery-comfy-q-edit" disabled={disabled} onClick={() => openDlg({ kind: "speed" })}>
                Edit
              </button>
            </div>
          ) : null}
          {powerSlots.length || standardLoras.length ? (
            <div className="discovery-comfy-q-card">
              <div className="discovery-comfy-q-card-title">LoRA stack</div>
              <div className="discovery-comfy-q-card-summary">{loraSummary}</div>
              <button type="button" className="discovery-comfy-q-edit" disabled={disabled} onClick={() => openDlg({ kind: "lora" })}>
                Edit
              </button>
            </div>
          ) : null}
        </div>
      )}

      <dialog ref={dialogRef} className="discovery-comfy-q-dialog" onCancel={closeDlg}>
        <div className="discovery-comfy-q-dlg-inner">
          {renderDialogBody()}
          <div className="discovery-comfy-q-dlg-actions">
            <button type="button" className="discovery-comfy-q-dlg-close" onClick={closeDlg}>
              Close
            </button>
          </div>
        </div>
      </dialog>
    </div>
  );
}
