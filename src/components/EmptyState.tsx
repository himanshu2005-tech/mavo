import type { LucideIcon } from "lucide-react";

export function EmptyState({ icon: Icon, title, body, action }: { icon: LucideIcon; title: string; body: string; action?: { label: string; onClick: () => void } }) {
  return (
    <div className="grid min-h-[340px] place-items-center p-8 text-center">
      <div className="max-w-sm">
        <span className="empty-orb" aria-hidden="true"><Icon size={26} strokeWidth={1.5} /></span>
        <h2 className="text-[17px] font-semibold tracking-[-0.01em] text-zinc-100">{title}</h2>
        <p className="mt-2 text-[13px] leading-6 text-zinc-500">{body}</p>
        {action && <button onClick={action.onClick} className="btn-primary mt-5">{action.label}</button>}
      </div>
    </div>
  );
}
