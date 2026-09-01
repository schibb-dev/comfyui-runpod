import React, { Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AppShell } from "./ui/AppShell";
import { canHandleClientPath, resolveRouteId, type AppRouteId } from "./ui/routes";
import "./ui/styles.css";

/** Lazy screens so a broken module (bad HMR transform) cannot blank every route. */
const SCREENS: Record<AppRouteId, React.LazyExoticComponent<React.ComponentType>> = {
  home: lazy(() => import("./ui/HomeDashboard").then((m) => ({ default: m.HomeDashboard }))),
  queue: lazy(() => import("./ui/ComfyQueueMonitorApp").then((m) => ({ default: m.ComfyQueueMonitorApp }))),
  orchestrator: lazy(() => import("./ui/OrchestratorApp").then((m) => ({ default: m.OrchestratorApp }))),
  workflows: lazy(() => import("./ui/WorkflowExplorerApp").then((m) => ({ default: m.WorkflowExplorerApp }))),
  factory: lazy(() => import("./ui/DiscoveryFactoryMapApp").then((m) => ({ default: m.DiscoveryFactoryMapApp }))),
  rate: lazy(() => import("./ui/DiscoveryRatingQueueApp").then((m) => ({ default: m.DiscoveryRatingQueueApp }))),
  workbench: lazy(() => import("./ui/WorkProductsApp").then((m) => ({ default: m.WorkProductsApp }))),
  "family-ab": lazy(() => import("./ui/FamilyABApp").then((m) => ({ default: m.FamilyABApp }))),
  submit: lazy(() => import("./ui/SubmitComposerApp").then((m) => ({ default: m.SubmitComposerApp }))),
  "vision-slices": lazy(() => import("./ui/VisionSliceReviewApp").then((m) => ({ default: m.VisionSliceReviewApp }))),
  library: lazy(() => import("./ui/DiscoveryLibraryApp").then((m) => ({ default: m.DiscoveryLibraryApp }))),
  stills: lazy(() => import("./ui/StillGalleryApp").then((m) => ({ default: m.StillGalleryApp }))),
  clips: lazy(() => import("./ui/ClipsLibraryApp").then((m) => ({ default: m.ClipsLibraryApp }))),
  experiments: lazy(() => import("./ui/App").then((m) => ({ default: m.App }))),
};

const VisionTagJudgeApp = lazy(() =>
  import("./ui/VisionTagJudgeApp").then((m) => ({ default: m.VisionTagJudgeApp })),
);

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 10 * 60_000,
      retry: 1,
      refetchOnWindowFocus: false,
      refetchOnReconnect: "always",
    },
  },
});

function locationHref(): string {
  return `${window.location.pathname || "/"}${window.location.search || ""}${window.location.hash || ""}`;
}

class ScreenErrorBoundary extends React.Component<{ children: React.ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <div className="panel" style={{ margin: 16, padding: 16, color: "var(--bad, #ff5a7a)" }}>
          <h1 style={{ margin: "0 0 8px", fontSize: 18 }}>Screen failed to render</h1>
          <pre style={{ whiteSpace: "pre-wrap", fontSize: 13, color: "var(--text, #e7eaf3)" }}>
            {this.state.error.message}
          </pre>
          <p style={{ marginTop: 12, color: "var(--muted, #aab2c5)", fontSize: 13 }}>
            Try a hard refresh. If this persists after editing, restart the Vite dev server.
          </p>
        </div>
      );
    }
    return this.props.children;
  }
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterRoot />
    </QueryClientProvider>
  </React.StrictMode>,
);

function RouterRoot() {
  const [href, setHref] = React.useState<string>(() => locationHref());
  React.useEffect(() => {
    const onPop = () => setHref(locationHref());
    const onDocumentClick = (e: MouseEvent) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.altKey || e.ctrlKey || e.shiftKey) return;
      const target = e.target;
      if (!(target instanceof Element)) return;
      const anchor = target.closest("a[href]");
      if (!(anchor instanceof HTMLAnchorElement)) return;
      if (anchor.target && anchor.target !== "_self") return;
      if (anchor.hasAttribute("download")) return;
      if (anchor.dataset.spa === "false") return;
      const rel = (anchor.getAttribute("rel") || "").toLowerCase();
      if (rel.includes("external")) return;
      let url: URL;
      try {
        url = new URL(anchor.href, window.location.origin);
      } catch {
        return;
      }
      if (url.origin !== window.location.origin) return;
      if (!canHandleClientPath(url.pathname)) return;
      const currentPath = window.location.pathname || "/";
      const currentSearch = window.location.search || "";
      if (url.pathname === currentPath && url.search === currentSearch && url.hash && url.hash !== window.location.hash) {
        // Let native anchor jumps through when only hash changed.
        return;
      }
      const next = `${url.pathname}${url.search}${url.hash}`;
      const current = locationHref();
      if (next === current) return;
      e.preventDefault();
      window.history.pushState({}, "", next);
      window.dispatchEvent(new PopStateEvent("popstate"));
    };
    window.addEventListener("popstate", onPop);
    document.addEventListener("click", onDocumentClick);
    return () => {
      window.removeEventListener("popstate", onPop);
      document.removeEventListener("click", onDocumentClick);
    };
  }, []);
  const pathname = window.location.pathname || "/";
  const active = resolveRouteId(pathname);
  const RootView = pathname.startsWith("/vision/tag-judge") ? VisionTagJudgeApp : SCREENS[active];
  return (
    <AppShell active={active}>
      <ScreenErrorBoundary>
        <Suspense
          fallback={
            <div className="panel" style={{ margin: 16, padding: 16, color: "var(--muted, #aab2c5)" }}>
              Loading…
            </div>
          }
        >
          <RootView key={href} />
        </Suspense>
      </ScreenErrorBoundary>
    </AppShell>
  );
}
