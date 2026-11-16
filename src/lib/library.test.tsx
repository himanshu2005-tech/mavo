import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it } from 'vitest';
import { CATEGORIES, LIBRARY, every, findAgents } from './library';
import { LibraryPage } from '../pages/LibraryPage';

it('offers every catalog agent with a plain kind, timing and a ready chat request', () => {
  expect(LIBRARY.length).toBe(100);
  for (const agent of LIBRARY) {
    expect(CATEGORIES[agent.category], agent.name).toBeTruthy();
    expect(agent.prompt.startsWith(`Make an agent called "${agent.name}".`)).toBe(true);
    expect(agent.when).toBeTruthy();
  }
  const meals = LIBRARY.find(a => a.name === 'Meal Reminders')!;
  expect(meals.when).toBe('Daily at 09:00, 13:00, 19:45');
  expect(meals.prompt).toContain('13:00 "Time for lunch."');
});

it('says intervals the way people do', () => {
  expect(every(30)).toBe('Every 30 min');
  expect(every(60)).toBe('Every hour');
  expect(every(240)).toBe('Every 4 hours');
  expect(every(1440)).toBe('Every day');
  expect(every(10080)).toBe('Every week');
});

it('finds agents by words, by what they use, and by kind', () => {
  expect(findAgents('', null)).toHaveLength(100);
  expect(findAgents('meal', null).map(a => a.name)).toContain('Meal Reminders');
  expect(findAgents('', 'coding').every(a => a.category === 'coding')).toBe(true);
  expect(findAgents('gmail', null).length).toBeGreaterThan(0);
  expect(findAgents('zzzz nothing', null)).toEqual([]);
});

it('lists the agents with one Use button each', () => {
  const html = renderToStaticMarkup(<LibraryPage />);
  expect(html).toContain('Meal Reminders');
  expect(html.match(/aria-label="Use /g)).toHaveLength(100);
});
