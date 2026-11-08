import { expect,it } from "vitest";
import { allocateIdentityColors,contrast,identityStyle } from "./identityColors";

it("does not repeat actual colors across 500 Crews and their connections in either theme", () => {
  const keys=[...Array.from({length:500},(_,i)=>`crew:${i}`),...Array.from({length:15},(_,i)=>`route:${i}`),
    ...Array.from({length:30},(_,i)=>`resource:${i}`),...Array.from({length:20},(_,i)=>`action:${i}`)];
  const colors=Object.values(allocateIdentityColors(keys));
  expect(colors).toHaveLength(keys.length);
  expect(new Set(colors.map(c=>c.dark)).size).toBe(keys.length);
  expect(new Set(colors.map(c=>c.light)).size).toBe(keys.length);
  for (const c of colors) {
    expect(contrast(c.dark,"#181818")).toBeGreaterThanOrEqual(3);
    expect(contrast(c.light,"#f5f5f5")).toBeGreaterThanOrEqual(3);
  }
});

it("keeps assignments on reordering, additions, removals and saved-preference reload", () => {
  const keys=['crew:a','crew:b','route:a','resource:a'];
  const first=allocateIdentityColors(keys);
  expect(allocateIdentityColors([...keys].reverse())).toEqual(first);
  const next=allocateIdentityColors(['crew:new',...keys].reverse(),JSON.parse(JSON.stringify(first)));
  for (const key of keys) expect(next[key]).toEqual(first[key]);
  expect(allocateIdentityColors(['crew:a'],next)).toEqual(next);
});

it("repairs duplicate and invalid saved colors rather than silently repeating them", () => {
  const first=allocateIdentityColors(['crew:a']);
  const next=allocateIdentityColors(['crew:a','crew:b','crew:c'],{
    ...first,'crew:b':first['crew:a'],'crew:c':{hue:0,dark:'#ffffff',light:'#ffffff'},
  });
  expect(next['crew:a']).toEqual(first['crew:a']);
  expect(new Set(Object.values(next).map(c=>c.dark)).size).toBe(3);
  expect(new Set(Object.values(next).map(c=>c.light)).size).toBe(3);
  expect(identityStyle(next['crew:a'])['--identity' as keyof ReturnType<typeof identityStyle>]).toContain('light-dark(');
});
