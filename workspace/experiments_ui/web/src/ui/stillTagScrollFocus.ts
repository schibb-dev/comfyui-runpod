import { useEffect, type RefObject } from "react";

type Axis = "x" | "y";

/** Pick the item whose center is closest to the scroll container center. */
export function useScrollCenterFocus({
  rootRef,
  itemRefs,
  itemIds,
  onFocus,
  enabled,
  axis = "y",
}: {
  rootRef: RefObject<HTMLElement | null>;
  itemRefs: RefObject<Map<string, HTMLElement>>;
  itemIds: string[];
  onFocus: (id: string) => void;
  enabled: boolean;
  axis?: Axis;
}) {
  useEffect(() => {
    if (!enabled) return;
    const root = rootRef.current;
    if (!root) return;

    let raf = 0;
    const pick = () => {
      const rootRect = root.getBoundingClientRect();
      const center =
        axis === "x" ? rootRect.left + rootRect.width / 2 : rootRect.top + rootRect.height / 2;

      let bestId = "";
      let bestDist = Number.POSITIVE_INFINITY;
      for (const id of itemIds) {
        const el = itemRefs.current.get(id);
        if (!el) continue;
        const r = el.getBoundingClientRect();
        const elCenter = axis === "x" ? r.left + r.width / 2 : r.top + r.height / 2;
        const dist = Math.abs(elCenter - center);
        if (dist < bestDist) {
          bestDist = dist;
          bestId = id;
        }
      }
      if (bestId) onFocus(bestId);
    };

    const onScroll = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(pick);
    };

    root.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      root.removeEventListener("scroll", onScroll);
      cancelAnimationFrame(raf);
    };
  }, [axis, enabled, itemIds, onFocus, rootRef, itemRefs]);
}

export function scrollFocusItemIntoView(
  root: HTMLElement | null,
  el: HTMLElement | null,
  axis: Axis = "y",
) {
  if (!root || !el) return;
  if (axis === "y") {
    const top = el.offsetTop - root.clientHeight / 2 + el.clientHeight / 2;
    root.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
    return;
  }
  const left = el.offsetLeft - root.clientWidth / 2 + el.clientWidth / 2;
  root.scrollTo({ left: Math.max(0, left), behavior: "smooth" });
}
