import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./ui/App";
import { AppShell } from "./ui/AppShell";
import { AssetWorkbench } from "./ui/AssetWorkbench";
import { ComfyQueueMonitorApp } from "./ui/ComfyQueueMonitorApp";
import { DiscoveryLibraryApp } from "./ui/DiscoveryLibraryApp";
import { DiscoveryFactoryMapApp } from "./ui/DiscoveryFactoryMapApp";
import { DiscoveryRatingQueueApp } from "./ui/DiscoveryRatingQueueApp";
import { HomeDashboard } from "./ui/HomeDashboard";
import { OrchestratorApp } from "./ui/OrchestratorApp";
import { WorkflowExplorerApp } from "./ui/WorkflowExplorerApp";
import { isWorkbenchLens, resolveRouteId, type AppRouteId } from "./ui/routes";
import "./ui/styles.css";

const SCREENS: Record<AppRouteId, React.ComponentType> = {
  home: HomeDashboard,
  queue: ComfyQueueMonitorApp,
  orchestrator: OrchestratorApp,
  workflows: WorkflowExplorerApp,
  factory: DiscoveryFactoryMapApp,
  rate: DiscoveryRatingQueueApp,
  library: DiscoveryLibraryApp,
  experiments: App,
};

const active = resolveRouteId(window.location.pathname || "/");
const RootView = SCREENS[active];

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

// Library / Factory / Rate render inside the shared Workbench frame (lens bar);
// every other route renders bare inside the app shell.
const screen = isWorkbenchLens(active) ? (
  <AssetWorkbench active={active}>
    <RootView />
  </AssetWorkbench>
) : (
  <RootView />
);

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AppShell active={active}>
      <ScreenErrorBoundary>{screen}</ScreenErrorBoundary>
    </AppShell>
  </React.StrictMode>,
);

