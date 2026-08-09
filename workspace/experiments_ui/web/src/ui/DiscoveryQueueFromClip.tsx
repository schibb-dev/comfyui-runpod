import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  createWorkItems,
  fetchIdentityStillCandidates,
  fetchShapeFactoryWorkProducts,
  mintIdentityStill,
  runDispositionStep,
  type IdentityStillCandidate,
  type IdentityStillMintTarget,
  type ShapeFactoryClip,
} from "./api";
import type {
  DiscoveryLibraryItem,
  ShapeFactoryMapQueueOverrides,
  WorkProductFamilyOption,
} from "./types";
import { marksToVhsWindow } from "./workProductTrim";

/** Prefer real extend families (prompt_profile pools), not identity-still shapes. */
const PREFERRED_EXTEND_FAMILIES = ["FB9_GEX2", "FB9_GEX_FACIAL", "FB9_GEX"] as const;

function isExtendFamilySlug(slug: string): boolean {
  const s = String(slug || "").trim();
  if (!s) return false;
  // Identity-anchor shapes are for still lock, not the default clip→extend target.
  if (/identity/i.test(s)) return false;
  return true;
}

function pickDefaultFamily(
  families: WorkProductFamilyOption[],
  extendDefaults: Record<string, string>,
  hintFamily?: string | null,
  mediaRelpath?: string | null,
): string {
  const slugs = families.map((f) => f.slug).filter(Boolean);
  const has = (slug: string) => slugs.includes(slug);

  const hint = String(hintFamily || "").trim();
  if (hint && extendDefaults[hint] && has(extendDefaults[hint])) return extendDefaults[hint];
  if (hint && has(hint) && isExtendFamilySlug(hint)) return hint;

  // Pipeline successors are the intended next-hop extend families.
  for (const succ of Object.values(extendDefaults || {})) {
    if (succ && has(succ) && isExtendFamilySlug(succ)) return succ;
  }

  // Infer from parent media name when possible (e.g. FB9_GEX2_FACIAL_… → FB9_GEX_FACIAL).
  const base = String(mediaRelpath || "")
    .replace(/\\/g, "/")
    .split("/")
    .pop()
    ?.toUpperCase() || "";
  if (base.includes("GEX2_FACIAL") || base.includes("GEX_FACIAL")) {
    if (has("FB9_GEX_FACIAL")) return "FB9_GEX_FACIAL";
  }
  if (base.includes("GEX2")) {
    if (has("FB9_GEX2")) return "FB9_GEX2";
  }
  if (base.includes("GEX")) {
    if (has("FB9_GEX")) return "FB9_GEX";
  }

  for (const pref of PREFERRED_EXTEND_FAMILIES) {
    if (has(pref)) return pref;
  }
  const first = slugs.find(isExtendFamilySlug);
  return first || slugs[0] || PREFERRED_EXTEND_FAMILIES[0];
}

export function DiscoveryQueueFromClip({
  item,
  mediaRelpath,
  markIn,
  markOut,
  duration,
  fps,
  activeClip,
}: {
  item: DiscoveryLibraryItem;
  mediaRelpath: string | null;
  markIn: number | null;
  markOut: number | null;
  duration: number;
  fps: number;
  activeClip: ShapeFactoryClip | null;
}) {
  const relpath = String(mediaRelpath || item.video_relpath || item.relpath || "").trim();
  const [families, setFamilies] = useState<WorkProductFamilyOption[]>([]);
  const [extendDefaults, setExtendDefaults] = useState<Record<string, string>>({});
  const [family, setFamily] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [identityNeeded, setIdentityNeeded] = useState(false);
  const [identityLoading, setIdentityLoading] = useState(false);
  const [identityCandidates, setIdentityCandidates] = useState<IdentityStillCandidate[]>([]);
  const [identityMintTargets, setIdentityMintTargets] = useState<IdentityStillMintTarget[]>([]);
  const [identitySelectedPath, setIdentitySelectedPath] = useState("");
  const [identitySelectedId, setIdentitySelectedId] = useState("");
  const [identityMintBusy, setIdentityMintBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setFamily("");
    void fetchShapeFactoryWorkProducts({ limit: 1 })
      .then((res) => {
        if (cancelled) return;
        const rows = res.families || [];
        const defaults = res.extend_family_defaults || {};
        setFamilies(rows);
        setExtendDefaults(defaults);
        setFamily(
          pickDefaultFamily(
            rows,
            defaults,
            item.work_items_open?.find((w) => w.factory_family)?.factory_family ||
              item.work_items?.find((w) => w.factory_family)?.factory_family ||
              null,
            relpath || item.video_relpath || item.relpath,
          ),
        );
      })
      .catch(() => {
        /* keep empty — queue will surface errors */
      });
    return () => {
      cancelled = true;
    };
  }, [item.relpath, item.video_relpath, item.work_items, item.work_items_open, relpath]);

  useEffect(() => {
    if (!relpath || !family) {
      setIdentityNeeded(false);
      setIdentityCandidates([]);
      setIdentityMintTargets([]);
      setIdentitySelectedPath("");
      setIdentitySelectedId("");
      return;
    }
    let cancelled = false;
    setIdentityLoading(true);
    void fetchIdentityStillCandidates({
      relpath,
      family_slug: family,
    })
      .then((res) => {
        if (cancelled) return;
        const needed = Boolean(res.needed);
        setIdentityNeeded(needed);
        const cands = Array.isArray(res.candidates) ? res.candidates : [];
        setIdentityCandidates(cands);
        setIdentityMintTargets(Array.isArray(res.mint_targets) ? res.mint_targets : []);
        if (needed) {
          const rec = cands.find((c) => c.id === res.recommended_id) || cands[0];
          setIdentitySelectedPath(rec?.path || "");
          setIdentitySelectedId(rec?.id || "");
        } else {
          setIdentitySelectedPath("");
          setIdentitySelectedId("");
        }
      })
      .catch(() => {
        if (cancelled) return;
        setIdentityNeeded(false);
        setIdentityCandidates([]);
        setIdentityMintTargets([]);
      })
      .finally(() => {
        if (!cancelled) setIdentityLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [family, relpath]);

  const windowOk =
    markIn != null &&
    markOut != null &&
    Number.isFinite(markIn) &&
    Number.isFinite(markOut) &&
    markOut > markIn + 0.05;

  const canQueue =
    Boolean(relpath) &&
    Boolean(family) &&
    windowOk &&
    !busy &&
    !identityLoading &&
    !(identityNeeded && !identitySelectedPath);

  const familyOpts = useMemo(() => {
    const rows = [...families];
    if (family && !rows.some((f) => f.slug === family)) rows.unshift({ slug: family });
    return rows;
  }, [families, family]);

  const buildOverrides = useCallback((): { overrides?: ShapeFactoryMapQueueOverrides; warning: string | null } => {
    if (!windowOk || markIn == null || markOut == null) return { warning: "Set mark in/out or select a clip" };
    const win = marksToVhsWindow(markIn, markOut, duration, fps > 0 ? fps : 18, null);
    const overrides: ShapeFactoryMapQueueOverrides = {
      parameters: {
        skip_first_frames: win.skip_first_frames,
        frame_load_cap: win.frame_load_cap,
      },
    };
    if (activeClip?.clip_id) overrides.source_clip_id = activeClip.clip_id;
    return { overrides, warning: win.warning };
  }, [activeClip?.clip_id, duration, fps, markIn, markOut, windowOk]);

  const queue = async (when: "now" | "later") => {
    if (!canQueue || !relpath) return;
    setBusy(true);
    setMsg(null);
    try {
      const { overrides, warning } = buildOverrides();
      if (!overrides) {
        setMsg(warning || "Need a clip window");
        return;
      }
      const created = await createWorkItems({
        source_relpath: relpath,
        routes: [{ step_id: "advance.extend", factory_family: family }],
        queue_now: when === "now",
      });
      const res = await runDispositionStep({
        relpath,
        step_id: "advance.extend",
        family_slug: family,
        front: when === "now",
        overrides,
        ...(identitySelectedPath ? { identity_anchor: identitySelectedPath } : {}),
      });
      const nested = (res.result as Record<string, unknown> | undefined) || {};
      const nestedResult = (nested.result as Record<string, unknown> | undefined) || {};
      const reason = String(nested.reason || nested.error || nestedResult.reason || "").trim();
      if (nested.ok === false) {
        throw new Error(reason || "queue failed");
      }
      const nextKey = String(nested.job_key || nestedResult.job_key || "").trim();
      const clampMsg = String(
        (nested.trim_clamped as { message?: string } | undefined)?.message ||
          (nestedResult.trim_clamped as { message?: string } | undefined)?.message ||
          warning ||
          "",
      ).trim();
      const fresh = Boolean(nested.fresh_combo || nestedResult.fresh_combo);
      setMsg(
        [
          `Queue ${when}`,
          created.count != null ? `${created.count} route(s)` : null,
          nextKey ? `→${nextKey}` : res.hook || "ok",
          fresh ? "fresh combo" : null,
          clampMsg,
        ]
          .filter(Boolean)
          .join(" · "),
      );
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const mintIdentity = async (target: IdentityStillMintTarget) => {
    if (identityMintBusy || busy) return;
    setIdentityMintBusy(true);
    setMsg(null);
    try {
      const res = await mintIdentityStill({
        video_relpath: target.video_relpath,
        video_path: target.video_path,
        at: target.at || "start",
      });
      const cand = res.candidate;
      if (cand?.path) {
        setIdentityCandidates((prev) => {
          if (prev.some((c) => c.id === cand.id || c.path === cand.path)) return prev;
          return [cand, ...prev];
        });
        setIdentitySelectedPath(cand.path);
        setIdentitySelectedId(cand.id);
        setMsg(`Minted identity still · ${cand.relpath || cand.path}`);
      }
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setIdentityMintBusy(false);
    }
  };

  if (!relpath) return null;

  return (
    <div className="discovery-queue-from-clip" aria-label="Queue from clip">
      <div className="discovery-queue-from-clip__head">
        <span className="work-product-viewer__clips-label">Queue from clip</span>
        {activeClip ? (
          <span className="factory-muted">{activeClip.label || "Clip"} selected</span>
        ) : windowOk ? (
          <span className="factory-muted">scrubber window</span>
        ) : (
          <span className="work-product-viewer__trim-warn">select a clip or set marks</span>
        )}
      </div>
      <div className="discovery-queue-from-clip__row">
        <label className="discovery-queue-from-clip__field">
          <span>Family</span>
          <select
            value={family}
            disabled={busy}
            onChange={(e) => setFamily(e.target.value)}
          >
            {familyOpts.length === 0 ? <option value="">Loading…</option> : null}
            {familyOpts.map((f) => (
              <option key={f.slug} value={f.slug}>
                {f.slug}
              </option>
            ))}
          </select>
        </label>
        <div className="discovery-queue-from-clip__actions">
          <button type="button" className="drt-btn" disabled={!canQueue} onClick={() => void queue("now")}>
            {busy ? "Queuing…" : "Queue now"}
          </button>
          <button type="button" className="drt-btn" disabled={!canQueue} onClick={() => void queue("later")}>
            Queue later
          </button>
        </div>
      </div>
      {identityNeeded ? (
        <div className="discovery-queue-from-clip__identity" aria-label="Identity still">
          <div className="work-product-identity-still__head">
            <span className="work-product-viewer__clips-label">Identity still</span>
            {identityLoading ? <span className="factory-muted">loading…</span> : null}
          </div>
          {identityCandidates.length ? (
            <div className="work-product-identity-still__strip" role="listbox">
              {identityCandidates.slice(0, 8).map((c) => {
                const selected = identitySelectedId === c.id || identitySelectedPath === c.path;
                const thumb = c.thumb_url || c.url;
                return (
                  <button
                    key={c.id || c.path}
                    type="button"
                    role="option"
                    aria-selected={selected}
                    className={`work-product-identity-still__thumb${selected ? " is-selected" : ""}`}
                    disabled={busy}
                    title={c.label || c.evidence || "still"}
                    onClick={() => {
                      setIdentitySelectedPath(c.path);
                      setIdentitySelectedId(c.id);
                    }}
                  >
                    {thumb ? <img src={thumb} alt="" loading="lazy" /> : <span>{(c.evidence || "?").slice(0, 3)}</span>}
                    <span className="work-product-identity-still__ev">{c.evidence || ""}</span>
                  </button>
                );
              })}
            </div>
          ) : (
            <p className="factory-muted" style={{ margin: 0, fontSize: "0.78rem" }}>
              No identity still yet — mint a frame or pick another family.
            </p>
          )}
          {identityMintTargets.length ? (
            <div className="work-product-identity-still__mints">
              {identityMintTargets.slice(0, 3).map((t) => (
                <button
                  key={`${t.video_relpath || t.video_path}-${t.lineage_depth}`}
                  type="button"
                  className="drt-btn work-product-identity-still__mint"
                  disabled={busy || identityMintBusy}
                  onClick={() => void mintIdentity(t)}
                >
                  {identityMintBusy ? "Minting…" : t.label || "First frame"}
                </button>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
      {msg ? <p className="work-product-quick-queue__msg" title={msg}>{msg}</p> : null}
    </div>
  );
}
