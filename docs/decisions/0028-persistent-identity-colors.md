# 0028 — Persistent identity accents on a neutral graph

Status: accepted

Use individual color identities, not a repeating template palette or one shared color per
connection category. Crew, route, resource and action keys share a collision-checked registry.
New assignments maximize distance among sampled OKLab colors; actual dark AND light RGB
values must both be unused. Badges meet 3:1 contrast against their theme's neutral surface.
No dependency, inference request or external service is needed.

Colors are small accents on otherwise neutral cards and rows. At rest links are quiet; focus
reveals identity-colored paths. Crew assignment links use Crew colors, model links use their
route colors, and resource/action links use the corresponding entry's color. Trace dots and
detail badges use the same identity. Status remains separate (labels, status dots, dashed
stopped borders/links); color never authorizes an action or claims it executed.

Persist only identity keys and color tokens in localStorage under mavo-identity-colors-v1.
No API keys, authorization tokens, messages or resource contents are included. Preserve assignments when nodes
are reordered, paused, removed or added; do not recycle saved identities during normal use.
Validate stored preferences and repair duplicate or invalid colors. Storage failure warns
once and retains a session palette. Other desktop windows follow storage updates. Clearing
application appearance data may reset the palette, without changing agent records or grants.

Exact non-repetition is not a promise that 500 shades are all perceptually distinguishable,
or that all users perceive color alike. Keep names, initials, semantic icons, grouped headings
and states. Avoid rainbow fills, new bloom effects and dense animations.

Verify 500 Crews plus routes/resources/actions for RGB uniqueness and contrast in both themes;
test save/reload, order independence, additions, duplicate repair, rendered browser colors,
focus consistency and endpoint identity. Browser mutations remain isolated fixtures.
