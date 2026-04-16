import { useCallback, useEffect, useReducer } from "react";

/** API-format Comfy prompt map (node id → node body). */
export type PromptDraftMap = Record<string, unknown>;

const MAX_HISTORY = 80;

function cloneJson<T>(x: T): T {
  return JSON.parse(JSON.stringify(x)) as T;
}

function applySetInput(prev: PromptDraftMap, nodeId: string, inputKey: string, value: unknown): PromptDraftMap {
  const node = prev[nodeId];
  if (typeof node !== "object" || node === null) return prev;
  const nrec = node as Record<string, unknown>;
  const inputs = nrec.inputs;
  if (typeof inputs !== "object" || inputs === null) return prev;
  const inRec = { ...(inputs as Record<string, unknown>), [inputKey]: value };
  return { ...prev, [nodeId]: { ...nrec, inputs: inRec } };
}

function draftEqual(a: PromptDraftMap | null, b: PromptDraftMap | null): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  return JSON.stringify(a) === JSON.stringify(b);
}

function trimPast(past: PromptDraftMap[]): PromptDraftMap[] {
  if (past.length <= MAX_HISTORY) return past;
  return past.slice(past.length - MAX_HISTORY);
}

type HistoryState = {
  past: PromptDraftMap[];
  present: PromptDraftMap | null;
  future: PromptDraftMap[];
  /** When set, further `coalesce` edits for this nodeId:inputKey skip pushing to `past`. */
  coalesceKey: string | null;
};

type HistoryAction =
  | { type: "reset"; present: PromptDraftMap | null }
  | {
      type: "setInput";
      nodeId: string;
      inputKey: string;
      value: unknown;
      coalesce?: boolean;
      /** When true, snapshot `present` onto `past` (undo stack). Quick Edits only for now. */
      recordHistory?: boolean;
    }
  | { type: "undo" }
  | { type: "redo" }
  | { type: "burstEnd" };

const initialHistoryState: HistoryState = {
  past: [],
  present: null,
  future: [],
  coalesceKey: null,
};

function historyReducer(state: HistoryState, action: HistoryAction): HistoryState {
  switch (action.type) {
    case "reset":
      return {
        past: [],
        present: action.present ? cloneJson(action.present) : null,
        future: [],
        coalesceKey: null,
      };
    case "burstEnd":
      return state.coalesceKey === null ? state : { ...state, coalesceKey: null };
    case "setInput": {
      const { present } = state;
      if (!present) return state;
      const next = applySetInput(present, action.nodeId, action.inputKey, action.value);
      if (draftEqual(present, next)) return state;
      if (action.recordHistory !== true) {
        return { ...state, present: next, future: [], coalesceKey: null };
      }
      const burstKey = `${action.nodeId}:${action.inputKey}`;
      if (action.coalesce) {
        if (state.coalesceKey === burstKey) {
          return { ...state, present: next, future: [] };
        }
        return {
          past: trimPast([...state.past, cloneJson(present)]),
          present: next,
          future: [],
          coalesceKey: burstKey,
        };
      }
      return {
        past: trimPast([...state.past, cloneJson(present)]),
        present: next,
        future: [],
        coalesceKey: null,
      };
    }
    case "undo": {
      if (!state.present || state.past.length === 0) return state;
      const previous = state.past[state.past.length - 1]!;
      const past = state.past.slice(0, -1);
      const future = [cloneJson(state.present), ...state.future];
      return { past, present: previous, future, coalesceKey: null };
    }
    case "redo": {
      if (!state.present || state.future.length === 0) return state;
      const next = state.future[0]!;
      const future = state.future.slice(1);
      const past = trimPast([...state.past, cloneJson(state.present)]);
      return { past, present: next, future, coalesceKey: null };
    }
    default:
      return state;
  }
}

export type SetPromptInputMeta = {
  /**
   * When true, this edit is recorded on the undo stack.
   * **Quick Edits only** until we extend history to all node-field edits.
   */
  recordHistory?: boolean;
  /** Range-slider drag: merge into one undo step until `endSliderBurst`. */
  coalesce?: boolean;
};

/**
 * Immutable prompt draft with undo/redo stacks (deep-clone snapshots).
 * Only edits dispatched with `{ recordHistory: true }` are undoable today (Quick Edits).
 * Suitable for a future audit log by recording the same snapshots or dispatch metadata.
 */
export function usePromptDraftHistory() {
  const [state, dispatch] = useReducer(historyReducer, initialHistoryState);

  const resetPromptDraft = useCallback((next: PromptDraftMap | null) => {
    dispatch({ type: "reset", present: next });
  }, []);

  const setPromptInput = useCallback((nodeId: string, inputKey: string, value: unknown, meta?: SetPromptInputMeta) => {
    dispatch({
      type: "setInput",
      nodeId,
      inputKey,
      value,
      coalesce: meta?.coalesce,
      recordHistory: meta?.recordHistory,
    });
  }, []);

  const undo = useCallback(() => dispatch({ type: "undo" }), []);
  const redo = useCallback(() => dispatch({ type: "redo" }), []);
  const endSliderBurst = useCallback(() => dispatch({ type: "burstEnd" }), []);

  const canUndo = Boolean(state.present && state.past.length > 0);
  const canRedo = Boolean(state.present && state.future.length > 0);

  return {
    promptDraft: state.present,
    setPromptInput,
    resetPromptDraft,
    undo,
    redo,
    endSliderBurst,
    canUndo,
    canRedo,
  };
}

/**
 * Undo/redo for **recorded** prompt edits (Quick Edits today) when focus is not in a text-like control.
 * Native field undo still applies inside those controls.
 */
export function useComfyPromptUndoKeyboard(opts: {
  active: boolean;
  canUndo: boolean;
  canRedo: boolean;
  undo: () => void;
  redo: () => void;
}) {
  const { active, canUndo, canRedo, undo, redo } = opts;
  useEffect(() => {
    if (!active) return;
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      const t = e.target as HTMLElement | null;
      if (t) {
        const tag = t.tagName;
        if (tag === "TEXTAREA") return;
        if (tag === "SELECT") return;
        if (tag === "INPUT") {
          const typ = (t as HTMLInputElement).type;
          if (
            typ === "text" ||
            typ === "search" ||
            typ === "url" ||
            typ === "password" ||
            typ === "email" ||
            typ === "tel" ||
            typ === "week" ||
            typ === "month"
          ) {
            return;
          }
        }
      }
      const key = e.key.toLowerCase();
      if (key === "z") {
        if (e.shiftKey) {
          if (canRedo) {
            e.preventDefault();
            redo();
          }
        } else if (canUndo) {
          e.preventDefault();
          undo();
        }
      } else if (key === "y" && !e.shiftKey && canRedo) {
        e.preventDefault();
        redo();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [active, canUndo, canRedo, undo, redo]);
}
