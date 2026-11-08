import { Component, type ReactNode } from "react";
import { AlertCircle } from "lucide-react";

export class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: Error) { console.error("Mavo view failed", error); }
  render() {
    if (this.state.failed) return <div className="page-content"><div className="surface p-8"><AlertCircle className="text-mint" /><h1 className="mt-4 text-xl text-zinc-50">This view could not load</h1><p className="mt-2 text-sm text-zinc-400">Your saved data is still on this computer. Reload Mavo to try again.</p><button className="btn-primary mt-5" onClick={() => window.location.reload()}>Reload Mavo</button></div></div>;
    return this.props.children;
  }
}
