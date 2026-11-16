import { useAppStore } from "../store/app";

/** Quiet when all is well: the bar says nothing. Only a problem, or the first connect, is shown,
 *  as plain words. */
export function StatusPill() {
  const status = useAppStore((state) => state.status);
  const error = useAppStore((state) => state.statusError);
  if (status && !error) return null;
  return (
    <div role="status" title="Local workspace status. Mavo Core has a separate service connection."
      className={`whitespace-nowrap text-[12px] ${error ? "text-[var(--warn)]" : "text-zinc-500"}`}>
      {error ? "Workspace unavailable" : "Connecting…"}
    </div>
  );
}
