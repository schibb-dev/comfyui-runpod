/**
 * Viewport / device breakpoints for Experiments UI.
 *
 * Three targets:
 * - desktop: primary focus; mouse/keyboard, sidebar + main content.
 * - tablet: shared layout with optional touch; may diverge later.
 * - phone: different UX; swipe-heavy navigation, full-screen flows.
 *
 * Use useDevice() or useDeviceContext() in components to branch layout or behavior.
 * Use CSS vars (--bp-tablet, --bp-phone) for media queries.
 */

import React, { createContext, useContext, useEffect, useState } from "react";

/** Min width (px) above which we consider the viewport "desktop". */
export const BP_DESKTOP_MIN = 1024;
/** Max width (px) below which we consider the viewport "tablet" (and above phone). */
export const BP_TABLET_MAX = 1023;
/** Max width (px) below which we consider the viewport "phone". */
export const BP_PHONE_MAX = 767;

export type DeviceType = "desktop" | "tablet" | "phone";

export function getDeviceType(width: number): DeviceType {
  if (width <= BP_PHONE_MAX) return "phone";
  if (width <= BP_TABLET_MAX) return "tablet";
  return "desktop";
}

export type ViewportState = {
  device: DeviceType;
  width: number;
  height: number;
};

function getViewportState(): ViewportState {
  if (typeof window === "undefined") {
    return { device: "desktop", width: 1024, height: 768 };
  }
  const w = window.innerWidth;
  const h = window.innerHeight;
  return {
    device: getDeviceType(w),
    width: w,
    height: h,
  };
}

/**
 * Hook that returns current device class and viewport size.
 * Updates on resize. Use to branch layout/behavior by device (desktop vs tablet vs phone).
 */
export function useDevice(): ViewportState {
  const [state, setState] = useState<ViewportState>(getViewportState);

  useEffect(() => {
    const onResize = () => setState(getViewportState());
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  return state;
}

/** Shorthand: is the current viewport phone? (for future swipe-first UI). */
export function useIsPhone(): boolean {
  return useDevice().device === "phone";
}

/** Shorthand: is the current viewport tablet or smaller? */
export function useIsTabletOrSmaller(): boolean {
  const device = useDevice().device;
  return device === "tablet" || device === "phone";
}

/** Context so any component (e.g. navigation) can read device without prop drilling. */
const DeviceContext = createContext<ViewportState>({
  device: "desktop",
  width: 1024,
  height: 768,
});

export function DeviceProvider({ children }: { children: React.ReactNode }) {
  const state = useDevice();
  return <DeviceContext.Provider value={state}>{children}</DeviceContext.Provider>;
}

export function useDeviceContext(): ViewportState {
  return useContext(DeviceContext);
}
