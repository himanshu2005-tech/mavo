import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { CoreStatus } from "../types";

export function CoreServiceCard() {
  const [status, setStatus] = useState<CoreStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { void api.coreStatus().then(setStatus).catch((e: Error) => setError(e.message)); }, []);
  return <section className="surface p-6" aria-label="Mavo Core service"><div className="flex items-center justify-between"><h2 className="text-sm font-semibold">Mavo assistant</h2><span className="badge">{status?.connected ? "Available" : status ? "Unavailable" : "Connecting..."}</span></div>{(error || status?.error) && <p role="status" className="notice mt-3">{error || status?.error}</p>}</section>;
}
