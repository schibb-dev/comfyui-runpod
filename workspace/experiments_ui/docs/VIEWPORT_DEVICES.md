# Viewport & device targets

The Experiments UI is a **single React SPA**. Phone is a layout mode of that app, not a second codebase. Reach it from **any iOS browser** (Aloha, Safari, Chrome, Firefox — all WebKit) via the Tailscale Serve URL on port 8790. Do not assume Safari, Add to Home Screen, or `display-mode: standalone`.

## Targets

| Target   | Width (px)     | Current focus | Notes |
|----------|----------------|---------------|--------|
| **Desktop** | ≥ 1024       | Yes           | Mouse/keyboard, top nav, sidebar + main. |
| **Tablet**  | 768–1023     | Later         | Still uses desktop top nav for now. |
| **Phone**   | ≤ 767        | **In progress** | Hamburger destinations, one focused surface, swipe + drill-down. |

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
  - Phone: hamburger (☰) + screen title. Drawer has **Go to** (every destination) and **This screen** (actions the current page registered). No bottom tab strip.
  - Screens register actions with `useRegisterPhoneOverflow()` from `phoneChrome.tsx`.
  - Content is swipe + drill-down: one focused surface, not a dashboard of panels.

- **`web/src/ui/styles.css`**
  - `--bp-phone`, `--bp-tablet`, `--app-phonebar-h`.
  - `html` / `body` / `#root` use `100dvh` so the column matches the visible webview.

- **`index.html`**
  - `width=device-width, initial-scale=1, viewport-fit=cover`.

## Course (phone affordances)

1. **Shell (current):** hamburger destinations + per-screen actions; `viewport-fit=cover`; visualViewport height. Browser-agnostic (Aloha / Safari / …).
2. **Stills (current):** phone main page is a **grid**. Tap drills into **swipe-through stills** (image only). **Swipe right** opens Submit. **Swipe left** opens tags and tasks for that still. Tagging backlog and filters stay in hamburger → This screen. First paint is a skeleton plus a small stills page (24), then more pages fill in; focus only decodes the current still and neighbors.
3. **Shared contract:** one scroll root; 44px targets; PageHeader hidden on phone (title lives in the bar).
4. **Library:** already list → fullscreen viewer. Align overflow actions with the hamburger.
5. **Queue (current):** phone main page is a **compact job list**. Tap opens that job **fullscreen**. **Up / down** stays inside the section you opened (Running, Waiting, or History) and snaps to the next job. **Swipe left** opens details, **swipe right** opens actions. **← Back to list** returns to the compact list. **Autoplay** and **Loop** toggles sit on the list and the swipe bar. Ledger, filters, and Comfy logs stay in hamburger → This screen. Snapshot paints without waiting on history; history fills in after the first 16.
6. **Rate:** one clip + bottom/context actions.
7. **Workbench (current):** phone main page is the **job list**. Tap opens that job **fullscreen**. **← Back to list** returns to the list. **Details** (or ☰ → Job details) opens metadata in a sheet. Working set, search, and filters stay in hamburger → This screen. Desktop keeps the two-column tools/list/detail chrome.
8. **Submit:** stacked composer from existing doors.

Factory map, Workflows, Family A/B, Orchestrator, and Experiments stay in the hamburger Tools section. Capacitor / home-screen icons only after the web shell is usable in Aloha.

## Changing breakpoints

If you change the numbers, update both:

1. `viewport.tsx`: `BP_PHONE_MAX`, `BP_TABLET_MAX`, `BP_DESKTOP_MIN`.
2. `styles.css`: `--bp-phone`, `--bp-tablet`.
