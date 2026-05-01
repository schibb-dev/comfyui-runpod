# Viewport & device targets

The Experiments UI is being developed with **three device targets** in mind. We focus on one at a time; the codebase is set up so navigation and layout can branch by device later.

## Targets

| Target   | Width (px)     | Current focus | Notes |
|----------|----------------|---------------|--------|
| **Desktop** | ≥ 1024       | **Yes**       | Mouse/keyboard, sidebar + main content. Primary development target. |
| **Tablet**  | 768–1023     | Later         | Touch-capable; may share desktop layout or diverge. |
| **Phone**   | ≤ 767        | Later         | **Swipe-heavy**; quite different UI (full-screen flows, bottom nav, gestures). |

## Provisions in the codebase

- **`web/src/ui/viewport.tsx`**
  - Breakpoint constants: `BP_PHONE_MAX` (767), `BP_TABLET_MAX` (1023), `BP_DESKTOP_MIN` (1024).
  - `getDeviceType(width)` → `"desktop" | "tablet" | "phone"`.
  - **`useDevice()`** – hook that returns `{ device, width, height }` and updates on resize. Use this to branch layout or behavior (e.g. render different nav for phone).
  - **`useIsPhone()`** / **`useIsTabletOrSmaller()`** – shorthand hooks.

- **`web/src/ui/styles.css`**
  - CSS custom properties: `--bp-phone: 767px`, `--bp-tablet: 1024px`. Use in media queries for device-specific styles, e.g.:
    ```css
    @media (max-width: var(--bp-phone)) {
      /* phone-only layout */
    }
    @media (min-width: var(--bp-tablet)) {
      /* desktop (and up) */
    }
    ```

- **`index.html`**
  - Viewport meta already set: `width=device-width, initial-scale=1.0`.

## Current state

- **Desktop**: All current work (navigation, Pair/Slide viewers, sidebar, etc.) is desktop-first.
- **Tablet / phone**: No dedicated UI yet. When we add them:
  - Use `useDevice().device` (or the shorthand hooks) to choose layout/navigation components.
  - Phone UI will rely on **swiping** and full-screen flows rather than duplicating the desktop sidebar + panels.

## Changing breakpoints

If you change the numbers, update both:

1. `viewport.tsx`: `BP_PHONE_MAX`, `BP_TABLET_MAX`, `BP_DESKTOP_MIN`.
2. `styles.css`: `--bp-phone`, `--bp-tablet`.
