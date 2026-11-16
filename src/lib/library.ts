import catalog from '../../docs/examples/practical-crew-catalog.json';
import hints from '../../docs/examples/connection-requirements.json';

/** A ready-made agent in the Library. Choosing one only fills the chat: the normal setup (what
 *  it may read and change, a test, then Turn it on) still decides what it can do. */
export interface LibraryAgent {
  name: string;
  category: string;
  goal: string;
  when: string;
  uses: string[];
  prompt: string;
}

type Entry = { name: string; template: string; goal: string; needs: string; steps: string; output: string; interval?: number; daily?: string[][] };

export const CATEGORIES: Record<string, string> = {
  schedule: 'Plan & remind', communication: 'Mail', research: 'Research', learning: 'Learning',
  coding: 'Code', project: 'Projects', files: 'Files', computer: 'This PC', operator: 'Websites',
  travel: 'Travel', home: 'Home', 'personal-admin': 'Admin', wellbeing: 'Wellbeing', fitness: 'Fitness', health: 'Health',
};

const USES: Record<string, string> = {
  'google.gmail': 'Gmail', 'google.calendar': 'Calendar', 'google.tasks': 'Tasks', 'google.contacts': 'Contacts',
  device: 'This PC', github: 'GitHub', feeds: 'News', web: 'Web', files: 'Files', weather: 'Weather', notify: 'Alerts',
};

export function every(minutes: number): string {
  if (minutes % 10080 === 0) return minutes === 10080 ? 'Every week' : `Every ${minutes / 10080} weeks`;
  if (minutes % 1440 === 0) return minutes === 1440 ? 'Every day' : `Every ${minutes / 1440} days`;
  if (minutes % 60 === 0) return minutes === 60 ? 'Every hour' : `Every ${minutes / 60} hours`;
  return `Every ${minutes} min`;
}

function when(entry: Entry): string {
  if (entry.daily?.length) return 'Daily at ' + entry.daily.map(([time]) => time).join(', ');
  if (entry.interval) return every(entry.interval);
  return 'When you ask';
}

function prompt(entry: Entry, timing: string): string {
  const lines = [`Make an agent called "${entry.name}". ${entry.goal}`];
  if (entry.daily?.length) lines.push('Remind me daily: ' + entry.daily.map(([time, text]) => `${time} "${text}"`).join(', ') + '.');
  else if (entry.interval) lines.push(`Run it ${timing.toLowerCase()}.`);
  lines.push(`What it does: ${entry.steps}`, `What I get: ${entry.output}`);
  return lines.join('\n');
}

function uses(name: string): string[] {
  const all = { ...hints.resources, ...hints.actions } as Record<string, string[]>;
  return Object.entries(all).filter(([, names]) => names.includes(name)).map(([key]) => USES[key] ?? key);
}

export const LIBRARY: LibraryAgent[] = (catalog.agents as Entry[]).map(entry => {
  const timing = when(entry);
  return { name: entry.name, category: entry.template, goal: entry.goal, when: timing, uses: uses(entry.name), prompt: prompt(entry, timing) };
});

export function findAgents(query: string, category: string | null): LibraryAgent[] {
  const q = query.trim().toLowerCase();
  return LIBRARY.filter(a => (!category || a.category === category)
    && (!q || `${a.name} ${a.goal} ${a.uses.join(' ')} ${CATEGORIES[a.category] ?? ''}`.toLowerCase().includes(q)));
}
