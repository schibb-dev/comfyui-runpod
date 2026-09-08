/// <reference types="vite/client" />

/** Injected by Vite `define` in development; empty string in production builds. */
declare const __DEV_EXPERIMENTS_PROXY_TARGET__: string;

/** Host port ComfyUI is published on (`COMFYUI_HOST_PORT`, default 8188). */
declare const __COMFYUI_HOST_PORT__: string;

interface ImportMetaEnv {
  readonly VITE_DISPLAY_LOCALE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
