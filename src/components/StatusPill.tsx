import { useAppStore } from "../store/app";

export function StatusPill() {
  const status = useAppStore((state) => state.status);
  const error = useAppStore((state) => state.statusError);
  const connected = Boolean(status && !error);
  return (
    <div role="status" className="flex items-center gap-2 whitespace-nowrap text-[12px] text-zinc-500">
      <span className={`h-1.5 w-1.5 rounded-full ${connected ? "bg-[var(--good)]" : "bg-[var(--warn)]"}`} />
      {connected ? "Connected" : error ? "Unavailable" : "Connecting…"}
    </div>
  );
}
