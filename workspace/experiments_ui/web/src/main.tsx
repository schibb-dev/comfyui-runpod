import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./ui/App";
import { AppShell } from "./ui/AppShell";
import { ComfyQueueMonitorApp } from "./ui/ComfyQueueMonitorApp";
import { DiscoveryLibraryApp } from "./ui/DiscoveryLibraryApp";
import { DiscoveryFactoryMapApp } from "./ui/DiscoveryFactoryMapApp";
import { DiscoveryRatingQueueApp } from "./ui/DiscoveryRatingQueueApp";
import { HomeDashboard } from "./ui/HomeDashboard";
import { OrchestratorApp } from "./ui/OrchestratorApp";
import { WorkflowExplorerApp } from "./ui/WorkflowExplorerApp";
import { WorkProductsApp } from "./ui/WorkProductsApp";
import { VisionSliceReviewApp } from "./ui/VisionSliceReviewApp";
import { VisionTagJudgeApp } from "./ui/VisionTagJudgeApp";
import { resolveRouteId, type AppRouteId } from "./ui/routes";
import "./ui/styles.css";

const SCREENS: Record<AppRouteId, React.ComponentType> = {
  home: HomeDashboard,
  queue: ComfyQueueMonitorApp,
  orchestrator: OrchestratorApp,
  workflows: WorkflowExplorerApp,
  factory: DiscoveryFactoryMapApp,
  rate: DiscoveryRatingQueueApp,
  workbench: WorkProductsApp,
  "vision-slices": VisionSliceReviewApp,
  library: DiscoveryLibraryApp,
  experiments: App,
};

const pathname = window.location.pathname || "/";
const active = resolveRouteId(pathname);
const RootView = pathname.startsWith("/vision/tag-judge") ? VisionTagJudgeApp : SCREENS[active];

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
        </div>
      );
    }
    return this.props.children;
  }
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AppShell active={active}>
      <ScreenErrorBoundary>
        <RootView />
      </ScreenErrorBoundary>
    </AppShell>
  </React.StrictMode>,
);
