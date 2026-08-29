import React, { useEffect } from "react";
import { createPortal } from "react-dom";
import { type SubmitDeepLink } from "./discoveryDeepLink";
import { SubmitComposerApp } from "./SubmitComposerApp";

/**
 * Full Submit compose UI in a modal so Workbench (and other hosts) keep scroll/selection.
 */
export function SubmitComposerModal({
  intent,
  onClose,
  onSubmitted,
}: {
  intent: SubmitDeepLink | null;
  onClose: () => void;
  onSubmitted?: (info: { jobKeys: string[] }) => void;
}) {
  useEffect(() => {
    if (!intent) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopImmediatePropagation();
        onClose();
      }
    };
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKey, { capture: true });
    return () => {
      document.body.style.overflow = prevOverflow;
      window.removeEventListener("keydown", onKey, { capture: true });
    };
  }, [intent, onClose]);

  if (!intent) return null;

  return createPortal(
    <div
      className="modal-overlay submit-composer-modal-overlay"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Submit"
    >
      <div className="modal submit-composer-modal" onClick={(e) => e.stopPropagation()}>
        <SubmitComposerApp
          intent={intent}
          presentation="modal"
          onClose={onClose}
          onSubmitted={onSubmitted}
        />
      </div>
    </div>,
    document.body,
  );
}
