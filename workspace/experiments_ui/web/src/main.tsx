import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./ui/App";
import { ComfyQueueMonitorApp } from "./ui/ComfyQueueMonitorApp";
import { OrchestratorApp } from "./ui/OrchestratorApp";
import "./ui/styles.css";

const path = window.location.pathname || "/";
const RootView = path.startsWith("/comfy-queue") ? ComfyQueueMonitorApp : path.startsWith("/orchestrator") ? OrchestratorApp : App;

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <RootView />
  </React.StrictMode>
);

