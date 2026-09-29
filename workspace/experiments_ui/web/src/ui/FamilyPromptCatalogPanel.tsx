import React, { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchShapeFactoryPromptProfile,
  fetchShapeFactoryPromptVariants,
  mutateShapeFactoryPromptVariant,
} from "./api";
import { queryKeys } from "./queryKeys";
import { loadFamiliesBootstrap } from "./shapeFactorySessionCache";
import { promptProfileOptionLabel, promptVariantName } from "./submitFamily";
import type { WorkProductFamilyPromptProfile } from "./types";

async function refreshCatalogCaches(queryClient: ReturnType<typeof useQueryClient>, family: string) {
  try {
    await loadFamiliesBootstrap({ force: true });
  } catch {
    /* pickers still update after invalidate */
  }
  await queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.promptVariants(family) });
  await queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.promptVariantsRoot });
}

/**
 * Direct prompt-catalog editor for a Factory family — no job required.
 * List / edit text / rename / default / available / create.
 */
export function FamilyPromptCatalogPanel({ familySlug }: { familySlug: string }) {
  const family = String(familySlug || "").trim();
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const [positive, setPositive] = useState("");
  const [negative, setNegative] = useState("");
  const [dirty, setDirty] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [createAsDefault, setCreateAsDefault] = useState(false);

  const listQuery = useQuery({
    queryKey: queryKeys.shapeFactory.promptVariants(family),
    queryFn: () => fetchShapeFactoryPromptVariants(family, { includeUnavailable: true }),
    enabled: Boolean(family),
    staleTime: 15_000,
  });

  const variants = (listQuery.data?.variants || []) as WorkProductFamilyPromptProfile[];
  const selected = useMemo(() => {
    if (creating) return null;
    if (selectedId) {
      const hit = variants.find((v) => String(v.variant_id || "") === selectedId);
      if (hit) return hit;
    }
    return variants.find((v) => v.is_default) || variants[0] || null;
  }, [creating, selectedId, variants]);

  const selectedPath = String(selected?.path || "").trim();
  const profileQuery = useQuery({
    queryKey: queryKeys.shapeFactory.promptProfile(selectedPath),
    queryFn: () => fetchShapeFactoryPromptProfile(selectedPath),
    enabled: Boolean(selectedPath) && !creating,
    staleTime: 10_000,
  });

  useEffect(() => {
    if (creating) return;
    if (!selected) {
      setNameDraft("");
      setPositive("");
      setNegative("");
      setDirty(false);
      return;
    }
    setNameDraft(String(selected.name || selected.label || "").trim());
    const pos = String(profileQuery.data?.positive || "");
    const neg = String(profileQuery.data?.negative || "");
    if (profileQuery.isSuccess || profileQuery.isError) {
      setPositive(pos);
      setNegative(neg);
      setDirty(false);
    }
  }, [
    creating,
    selected?.variant_id,
    selected?.name,
    selected?.label,
    profileQuery.data?.positive,
    profileQuery.data?.negative,
    profileQuery.isSuccess,
    profileQuery.isError,
  ]);

  const saveMut = useMutation({
    mutationFn: async () => {
      if (creating) {
        const name = nameDraft.trim();
        if (!name) throw new Error("Name is required for a new variant");
        return mutateShapeFactoryPromptVariant({
          action: "create",
          family,
          name,
          positive,
          negative,
          set_as_default: createAsDefault,
        });
      }
      const vid = String(selected?.variant_id || "").trim();
      if (!vid) throw new Error("Selected variant has no id — run prompt-catalog backfill");
      return mutateShapeFactoryPromptVariant({
        action: "update_text",
        family,
        variant_id: vid,
        positive,
        negative,
      });
    },
    onSuccess: async (res) => {
      setMsg(creating ? `Created ${nameDraft.trim()}` : "Saved catalog text");
      setDirty(false);
      const newId = String(res.variant_id || "").trim();
      setCreating(false);
      if (newId) setSelectedId(newId);
      await refreshCatalogCaches(queryClient, family);
      if (selectedPath) {
        await queryClient.invalidateQueries({ queryKey: queryKeys.shapeFactory.promptProfile(selectedPath) });
      }
    },
    onError: (e) => setMsg(e instanceof Error ? e.message : String(e)),
  });

  const metaMut = useMutation({
    mutationFn: (body: Parameters<typeof mutateShapeFactoryPromptVariant>[0]) =>
      mutateShapeFactoryPromptVariant(body),
    onSuccess: async (_res, vars) => {
      if (vars.action === "rename") setMsg(`Renamed to ${vars.name}`);
      else if (vars.action === "set_default") setMsg("Set as family default");
      else if (vars.action === "set_available") setMsg(vars.available ? "Shown in pickers" : "Hidden from pickers");
      await refreshCatalogCaches(queryClient, family);
    },
    onError: (e) => setMsg(e instanceof Error ? e.message : String(e)),
  });

  const busy = saveMut.isPending || metaMut.isPending;
  const vid = String(selected?.variant_id || "").trim();

  const startCreate = () => {
    setCreating(true);
    setSelectedId(null);
    setNameDraft("");
    setPositive("");
    setNegative("");
    setCreateAsDefault(false);
    setDirty(true);
    setMsg(null);
  };

  const cancelCreate = () => {
    setCreating(false);
    setDirty(false);
    setMsg(null);
  };

  if (!family) return null;

  return (
    <section className="sfmap-prompt-catalog" id="sfmap-family-prompts" aria-label="Prompt catalog">
      <div className="sfmap-prompt-catalog__head">
        <h3 className="sfmap-prompt-catalog__title">
          Prompts
          <span className="factory-muted">
            {" "}
            · {variants.length} variant{variants.length === 1 ? "" : "s"}
          </span>
        </h3>
        <p className="factory-muted sfmap-prompt-catalog__hint">
          Edit the family catalog directly. Workbench Promote still copies from a job when you want that path.
        </p>
      </div>

      <div className="sfmap-prompt-catalog__layout">
        <div className="sfmap-prompt-catalog__list" role="listbox" aria-label="Prompt variants">
          {listQuery.isLoading ? <div className="factory-muted">Loading variants…</div> : null}
          {listQuery.error ? (
            <div className="factory-error">
              {listQuery.error instanceof Error ? listQuery.error.message : String(listQuery.error)}
            </div>
          ) : null}
          {variants.map((v) => {
            const id = String(v.variant_id || v.path || "");
            const active = !creating && selected?.variant_id === v.variant_id;
            return (
              <button
                key={id}
                type="button"
                role="option"
                aria-selected={active}
                className={`sfmap-prompt-catalog__row${active ? " sfmap-prompt-catalog__row--active" : ""}${
                  v.available === false ? " sfmap-prompt-catalog__row--hidden" : ""
                }`}
                onClick={() => {
                  setCreating(false);
                  setSelectedId(String(v.variant_id || ""));
                  setMsg(null);
                }}
              >
                <span className="sfmap-prompt-catalog__row-name">
                  {promptVariantName(v) || v.slug || v.basename || "variant"}
                </span>
                {v.is_default ? <span className="sfmap-prompt-catalog__badge">Default</span> : null}
                {v.available === false ? <span className="sfmap-prompt-catalog__badge">Hidden</span> : null}
              </button>
            );
          })}
          {!listQuery.isLoading && !variants.length ? (
            <div className="factory-empty">No catalog variants yet</div>
          ) : null}
          <button type="button" className="drt-btn sfmap-prompt-catalog__new" disabled={busy} onClick={startCreate}>
            New variant
          </button>
        </div>

        <div className="sfmap-prompt-catalog__editor">
          {creating ? (
            <div className="sfmap-prompt-catalog__editor-title">New variant</div>
          ) : selected ? (
            <div className="sfmap-prompt-catalog__editor-title">
              {promptProfileOptionLabel(selected)}
              {selected.basename ? (
                <span className="factory-muted mono"> · {selected.basename}</span>
              ) : null}
            </div>
          ) : (
            <div className="factory-muted">Select a variant</div>
          )}

          {(creating || selected) && (
            <>
              <label className="sfmap-prompt-catalog__field">
                <span>Name</span>
                <input
                  value={nameDraft}
                  disabled={busy || (!creating && !vid)}
                  onChange={(e) => {
                    setNameDraft(e.target.value);
                    if (creating) setDirty(true);
                  }}
                  placeholder="e.g. Base"
                />
              </label>

              {!creating && vid ? (
                <div className="sfmap-prompt-catalog__meta-actions">
                  <button
                    type="button"
                    className="drt-btn"
                    disabled={busy || !nameDraft.trim() || nameDraft.trim() === String(selected?.name || "").trim()}
                    onClick={() =>
                      metaMut.mutate({
                        action: "rename",
                        family,
                        variant_id: vid,
                        name: nameDraft.trim(),
                      })
                    }
                  >
                    Rename
                  </button>
                  <button
                    type="button"
                    className="drt-btn"
                    disabled={busy || Boolean(selected?.is_default)}
                    title={selected?.is_default ? "Already the family default" : "Designate as family default"}
                    onClick={() => metaMut.mutate({ action: "set_default", family, variant_id: vid })}
                  >
                    Set default
                  </button>
                  <button
                    type="button"
                    className="drt-btn"
                    disabled={busy || (selected?.is_default && selected?.available !== false)}
                    title={
                      selected?.is_default
                        ? "Pick another default before hiding this one"
                        : selected?.available === false
                          ? "Show in pickers again"
                          : "Hide from pickers"
                    }
                    onClick={() =>
                      metaMut.mutate({
                        action: "set_available",
                        family,
                        variant_id: vid,
                        available: selected?.available === false,
                      })
                    }
                  >
                    {selected?.available === false ? "Show" : "Hide"}
                  </button>
                </div>
              ) : null}

              {creating ? (
                <label className="sfmap-prompt-catalog__check">
                  <input
                    type="checkbox"
                    checked={createAsDefault}
                    disabled={busy}
                    onChange={(e) => setCreateAsDefault(e.target.checked)}
                  />{" "}
                  Set as family default
                </label>
              ) : null}

              {profileQuery.isLoading && !creating ? (
                <div className="factory-muted">Loading prompt text…</div>
              ) : null}

              <label className="sfmap-prompt-catalog__field">
                <span>Positive</span>
                <textarea
                  className="sfmap-prompt-catalog__textarea"
                  rows={8}
                  value={positive}
                  disabled={busy}
                  onChange={(e) => {
                    setPositive(e.target.value);
                    setDirty(true);
                  }}
                />
              </label>
              <label className="sfmap-prompt-catalog__field">
                <span>Negative</span>
                <textarea
                  className="sfmap-prompt-catalog__textarea"
                  rows={5}
                  value={negative}
                  disabled={busy}
                  onChange={(e) => {
                    setNegative(e.target.value);
                    setDirty(true);
                  }}
                />
              </label>

              <div className="sfmap-prompt-catalog__save-row">
                <button
                  type="button"
                  className="drt-btn"
                  disabled={busy || (!creating && !dirty) || (creating && !nameDraft.trim())}
                  onClick={() => {
                    setMsg(null);
                    saveMut.mutate();
                  }}
                >
                  {creating ? "Create variant" : "Save text"}
                </button>
                {creating ? (
                  <button type="button" className="drt-btn" disabled={busy} onClick={cancelCreate}>
                    Cancel
                  </button>
                ) : null}
              </div>
            </>
          )}

          {msg ? <p className="sfmap-prompt-catalog__msg factory-muted">{msg}</p> : null}
        </div>
      </div>
    </section>
  );
}
