/// <reference types="vite/client" />

/** Injected by Vite `define` in development; empty string in production builds. */
declare const __DEV_EXPERIMENTS_PROXY_TARGET__: string;

interface ImportMetaEnv {
  readonly VITE_DISPLAY_LOCALE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
