import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { ReminderOverlay } from "./pages/ReminderOverlay";
import { StatusOverlay } from "./pages/StatusOverlay";
import { startTheme } from "./lib/theme";
import { ErrorBoundary } from "./components/ErrorBoundary";
import "./styles.css";

startTheme();

const overlay = new URLSearchParams(window.location.search).get("overlay");
if(overlay) document.documentElement.classList.add("overlay-root");
const Root = overlay === "reminder" ? ReminderOverlay : overlay === "status" ? StatusOverlay : App;
ReactDOM.createRoot(document.getElementById("root")!).render(<React.StrictMode><ErrorBoundary><Root /></ErrorBoundary></React.StrictMode>);
