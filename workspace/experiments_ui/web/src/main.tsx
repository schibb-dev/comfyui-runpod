import React, { Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AppShell } from "./ui/AppShell";
import { resolveRouteId, type AppRouteId } from "./ui/routes";
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
  submit: lazy(() => import("./ui/SubmitComposerApp").then((m) => ({ default: m.SubmitComposerApp }))),
  "vision-slices": lazy(() => import("./ui/VisionSliceReviewApp").then((m) => ({ default: m.VisionSliceReviewApp }))),
  library: lazy(() => import("./ui/DiscoveryLibraryApp").then((m) => ({ default: m.DiscoveryLibraryApp }))),
  clips: lazy(() => import("./ui/ClipsLibraryApp").then((m) => ({ default: m.ClipsLibraryApp }))),
  experiments: lazy(() => import("./ui/App").then((m) => ({ default: m.App }))),
};

const VisionTagJudgeApp = lazy(() =>
  import("./ui/VisionTagJudgeApp").then((m) => ({ default: m.VisionTagJudgeApp })),
);

const pathname = window.location.pathname || "/";
const active = resolveRouteId(pathname);
const RootView = pathname.startsWith("/vision/tag-judge") ? VisionTagJudgeApp : SCREENS[active];
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
      <AppShell active={active}>
        <ScreenErrorBoundary>
          <Suspense
            fallback={
              <div className="panel" style={{ margin: 16, padding: 16, color: "var(--muted, #aab2c5)" }}>
                Loading…
              </div>
            }
          >
            <RootView />
          </Suspense>
        </ScreenErrorBoundary>
      </AppShell>
    </QueryClientProvider>
  </React.StrictMode>,
);
