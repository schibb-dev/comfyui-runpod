# Viewport & device targets

The Experiments UI is a **single React SPA**. Phone is a layout mode of that app, not a second codebase. Reach it from **any iOS browser** (Aloha, Safari, Chrome, Firefox — all WebKit) via the Tailscale Serve URL on port 8790. Do not assume Safari, Add to Home Screen, or `display-mode: standalone`.

## Targets

| Target   | Width (px)     | Current focus | Notes |
|----------|----------------|---------------|--------|
| **Desktop** | ≥ 1024       | Yes           | Mouse/keyboard, top nav, sidebar + main. |
| **Tablet**  | 768–1023     | Later         | Still uses desktop top nav for now. |
| **Phone**   | ≤ 767        | **In progress** | Bottom tabs, one surface at a time, sheets. |

## Browsers (iPhone)

Every iOS browser uses WKWebView. CSS, touch, and `env(safe-area-inset-*)` work the same. What **does** change is **chrome around the webview**:

- Aloha (and Safari, Chrome, …) draw their own tab/URL bars. The page usually gets a **shrunk webview**, not an overlay. Put phone chrome **in the document flow** (flex column + `100dvh`), not `position: fixed` to the screen.
- `window.visualViewport` tracks show/hide of that chrome and the keyboard. `viewport.tsx` listens to it for **height**. **Width / breakpoints stay on `window.innerWidth`** so pinch-zoom cannot flip desktop ↔ phone.
- `viewport-fit=cover` lets `safe-area-inset-*` apply. Home-indicator inset is often `0` when the browser already keeps the webview above it (common in Aloha).
- Do not gate features on `apple-mobile-web-app-capable` or standalone PWA. Optional later; not required for Aloha.

## Provisions in the codebase

- **`web/src/ui/viewport.tsx`**
  - Breakpoints: `BP_PHONE_MAX` (767), `BP_TABLET_MAX` (1023), `BP_DESKTOP_MIN` (1024).
  - `getDeviceType(width)` → `"desktop" | "tablet" | "phone"`.
  - **`DeviceProvider`** wraps the app in `main.tsx`. Use **`useDeviceContext()`** in screens (do not nest another provider).
  - **`useDevice()`** / **`useIsPhone()`** / **`useIsTabletOrSmaller()`** — prefer context after mount so resize is shared.

- **`web/src/ui/AppShell.tsx`**
  - Desktop: top pipeline + tools nav.
  - Phone: bottom tabs **Home · Library · Stills · Rate · Workbench** plus **More** (Clips, Factory, Queue, Follow-up, tools). Submit stays a door, not a tab.
  - Route lists: `phoneTabRoutes()` / `phoneMoreRoutes()` in `routes.ts`.

- **`web/src/ui/styles.css`**
  - `--bp-phone`, `--bp-tablet`, `--app-tabbar-h`.
  - `html` / `body` / `#root` use `100dvh` so the column matches the visible webview.

- **`index.html`**
  - `width=device-width, initial-scale=1, viewport-fit=cover`.

## Course (phone affordances)

1. **Shell (done / current):** DeviceProvider, `viewport-fit=cover`, visualViewport height, bottom tabs + More. Browser-agnostic.
2. **Shared contract:** one scroll root per screen; 44px targets; filters in sheets; don’t duplicate the tab label in a fat page header.
3. **Library:** already list → fullscreen viewer. Next: swipe between items; keep it the pattern other screens copy.
4. **Rate:** one clip + bottom actions (appetite / disposition).
5. **Stills:** thumb grid → inspect; filters as a sheet.
6. **Workbench:** job list only; tap → fullscreen clip; metadata/tools as sheets (desktop split stays desktop).
7. **Submit:** stacked composer from existing doors.

Defer Factory map, Workflows, Family A/B, Orchestrator, Experiments to More. Capacitor / home-screen icons only after the web shell is usable in Aloha.

## Changing breakpoints

If you change the numbers, update both:

1. `viewport.tsx`: `BP_PHONE_MAX`, `BP_TABLET_MAX`, `BP_DESKTOP_MIN`.
2. `styles.css`: `--bp-phone`, `--bp-tablet`.
