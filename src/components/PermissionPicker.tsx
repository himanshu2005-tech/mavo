import type { GoogleCapability } from "../types";

export const permissionLabels: Record<GoogleCapability, string> = {
  "gmail:read": "Read inbox headers", "gmail:body": "Read email contents",
  "gmail:send": "Send emails", "gmail:modify": "Create drafts, archive, mark read, and move mail to trash",
  "calendar:read": "Read events", "calendar:write": "Create, edit, and delete events",
  "tasks:read": "Read tasks", "tasks:write": "Create, edit, complete, and delete tasks",
  "drive:read": "Read file names, types and links (not contents)",
  "contacts:read": "Read contact names, emails and phone numbers",
  "youtube:read": "See your subscriptions' new videos and the videos you liked",
};
export function PermissionPicker({ value, onChange, suggested = [], disabled = false, allowed, showPolicyHint = true }: {
  value: GoogleCapability[]; onChange: (value: GoogleCapability[]) => void;
  suggested?: GoogleCapability[]; disabled?: boolean; allowed?: GoogleCapability[];
  showPolicyHint?: boolean;
}) {
  return <div className="space-y-3">{(["gmail", "calendar", "tasks", "drive", "contacts", "youtube"] as const).filter(service => !allowed || allowed.some(id => id.startsWith(service+":"))).map(service => <fieldset key={service} disabled={disabled} className="rounded-xl border border-line p-4"><legend className="px-1 text-xs font-semibold text-zinc-200">{{ gmail: "Gmail", calendar: "Google Calendar", tasks: "Google Tasks", drive:"Google Drive",contacts:"Google Contacts (People API)", youtube:"YouTube" }[service]}</legend><div className="space-y-3">{(Object.keys(permissionLabels) as GoogleCapability[]).filter(id => id.startsWith(service + ":") && (!allowed || allowed.includes(id))).map(id => <label className="flex items-start gap-3 text-xs leading-5 text-zinc-400" key={id}><input type="checkbox" className="mt-1" checked={value.includes(id)} onChange={e => onChange(e.target.checked ? [...value, id] : value.filter(v => v !== id))} /><span>{permissionLabels[id]}{suggested.includes(id) && <span className="ml-2 text-[10px] text-mint">Suggested</span>}{showPolicyHint && /:(send|modify|write)$/.test(id) && <span className="block text-[10px] text-zinc-500">Execution follows this Crew’s saved action policy.</span>}</span></label>)}</div></fieldset>)}</div>;
}
