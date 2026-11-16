import suggestions from '../../docs/examples/connection-requirements.json';

/** Catalog display hints only. Never used by permission checks, tools, or the scheduler. */
export function exampleConnectionKeys(crew: {name?:string;responsibilities?:string}): string[] {
  if (!crew.name?.startsWith('Example · ') || !crew.responsibilities?.startsWith('PRACTICAL EXAMPLE —')) return [];
  const name=crew.name.slice('Example · '.length);
  return [...Object.entries(suggestions.resources),...Object.entries(suggestions.actions)]
    .filter(([,names])=>names.includes(name)).map(([key])=>key);
}
