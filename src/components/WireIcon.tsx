import { AppWindow, Archive, Bell, CalendarDays, CalendarPlus, Cloud, Code2, FolderInput, FolderOpen, Folders, Globe, HardDrive, ListChecks, Lock, Mail, Monitor, Moon, MousePointerClick, Music, NotebookPen, NotebookText, PenLine, Power, Rss, Send, SquareTerminal, Trash2, Users, Volume2, Volume1, Youtube, type LucideIcon } from "lucide-react";

const ICONS: Record<string, LucideIcon> = {
  mail: Mail, calendar: CalendarDays, calendarPlus: CalendarPlus, tasks: ListChecks, drive: HardDrive, contacts: Users,
  rss: Rss, code: Code2, weather: Cloud, monitor: Monitor, globe: Globe, bell: Bell, send: Send, draft: PenLine,
  moon: Moon, power: Power, app: AppWindow, terminal: SquareTerminal, folder: FolderOpen, click: MousePointerClick, trash: Trash2,
  lock: Lock, volume: Volume1, music: Music, speak: Volume2, notes: NotebookText, notesWrite: NotebookPen,
  files: Folders, organise: FolderInput, backup: Archive, youtube: Youtube,
};

/** The icon for a resource or action on the graph (the names crewWiring.ts gives them). */
export function WireIcon({ name, size = 14 }: { name: string; size?: number }) {
  const I = ICONS[name] ?? Globe;
  return <I size={size} aria-hidden="true" />;
}
