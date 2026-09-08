import React from "react";

/** Named browsing context so Workbench + Queue reuse one ComfyUI window. */
export const COMFYUI_WINDOW_NAME = "comfyui";

function declaredComfyUiPort(): string {
  const raw =
    typeof __COMFYUI_HOST_PORT__ !== "undefined" ? String(__COMFYUI_HOST_PORT__ || "").trim() : "";
  return /^\d{2,5}$/.test(raw) ? raw : "8188";
}

export function comfyUiPort(): string {
  return declaredComfyUiPort();
}

export function comfyUiHref(loc?: { protocol: string; hostname: string }): string {
  const protocol = loc?.protocol || (typeof window !== "undefined" ? window.location.protocol : "http:");
  const hostname = loc?.hostname || (typeof window !== "undefined" ? window.location.hostname : "127.0.0.1");
  return `${protocol}//${hostname}:${comfyUiPort()}/`;
}

export function comfyUiHostLabel(loc?: { hostname: string }): string {
  const hostname = loc?.hostname || (typeof window !== "undefined" ? window.location.hostname : "127.0.0.1");
  return `${hostname}:${comfyUiPort()}`;
}

/**
 * Open ComfyUI in the shared `comfyui` window.
 * Omit rel=noopener/noreferrer — those force a new browsing context and defeat named-target reuse.
 */
export function ComfyUiLink({
  children,
  className,
  title,
}: {
  children: React.ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <a
      href={comfyUiHref()}
      target={COMFYUI_WINDOW_NAME}
      className={className}
      title={title || "Open ComfyUI"}
      onClick={(e) => e.stopPropagation()}
    >
      {children}
    </a>
  );
}
