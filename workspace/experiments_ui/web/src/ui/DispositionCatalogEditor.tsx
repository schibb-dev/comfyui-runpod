import React, { useState } from "react";
import type { DispositionCatalogMarker } from "./types";

export function DispositionCatalogEditor({
  markers,
  onSave,
  onClose,
  busy,
}: {
  markers: DispositionCatalogMarker[];
  onSave: (markers: DispositionCatalogMarker[]) => void | Promise<void>;
  onClose: () => void;
  busy?: boolean;
}) {
  const [rows, setRows] = useState<DispositionCatalogMarker[]>(() => markers.map((m) => ({ ...m })));

  const updateRow = (id: string, patch: Partial<DispositionCatalogMarker>) => {
    setRows((prev) => prev.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  };

  return (
    <div className="disposition-catalog-modal" role="dialog" aria-modal="true" aria-label="Edit disposition markers">
      <div className="disposition-catalog-modal__backdrop" onClick={onClose} />
      <div className="disposition-catalog-modal__panel">
        <header className="disposition-catalog-modal__head">
          <h3>Edit disposition markers</h3>
          <button type="button" className="drt-btn" onClick={onClose}>
            Close
          </button>
        </header>
        <p className="factory-muted disposition-catalog-modal__note">
          Changes save to the runtime catalog overlay (<code>_status/disposition_catalog.json</code>). Repo seed:{" "}
          <code>disposition_catalog.yaml</code>.
        </p>
        <div className="disposition-catalog-editor__list">
          {rows
            .filter((r) => r.kind === "entry")
            .map((r) => (
              <div key={r.id} className="disposition-catalog-editor__row">
                <label className="disposition-catalog-editor__check">
                  <input
                    type="checkbox"
                    checked={r.enabled !== false}
                    onChange={(e) => updateRow(r.id, { enabled: e.target.checked })}
                  />
                  <span className="mono">{r.id}</span>
                </label>
                <input
                  className="disposition-catalog-editor__input"
                  value={r.label}
                  onChange={(e) => updateRow(r.id, { label: e.target.value })}
                  aria-label={`Label for ${r.id}`}
                />
                <input
                  className="disposition-catalog-editor__input disposition-catalog-editor__hint"
                  value={r.hint || ""}
                  onChange={(e) => updateRow(r.id, { hint: e.target.value })}
                  placeholder="Hint"
                  aria-label={`Hint for ${r.id}`}
                />
              </div>
            ))}
        </div>
        <footer className="disposition-catalog-modal__foot">
          <button type="button" className="drt-btn" disabled={busy} onClick={() => void onSave(rows)}>
            Save catalog
          </button>
        </footer>
      </div>
    </div>
  );
}
