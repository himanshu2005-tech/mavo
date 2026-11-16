/** What the person typed, as they typed it. A question asked from an agent's card goes to Mavo
 *  with the agent's latest report before it and an instruction after it; both are for Mavo, and
 *  the chat showed them as if the person had written them. */
export function typedQuestion(text: string): string {
  let shown = text.replace(/\n\n\(Asked from my Crew's card\.[\s\S]*$/, "");
  if (shown.startsWith('About my Crew "') && shown.includes(">>>\n\n")) shown = shown.slice(shown.lastIndexOf(">>>\n\n") + 5);
  return shown.trim() || text;
}

/** An older run's technical failure, in the words Mavo uses now. */
const PLAIN: [RegExp, string][] = [
  [/^No source evidence matched an authorized source read in this assignment\.?$/, "It could not find the answer on the pages it read."],
  [/^Connected sources are unavailable or stale; no current report could be verified\.?$/, "It could not read its sources this time."],
];
export function plainError(text: string): string {
  const found = PLAIN.find(([pattern]) => pattern.test(text.trim()));
  return found ? found[1] : text;
}
