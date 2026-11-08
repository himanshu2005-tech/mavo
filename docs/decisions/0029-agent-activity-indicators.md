# 0029 — Distinguish enabled agents from actual work

Status: accepted

An enabled agent is not continuously generating tokens or executing tools. Give enabled,
ready agents a slow identity-colored orbit, a slightly stronger border and a Running or
Listening line. Only backend runtime_state=working receives the faster, longer arc and
Working label. Existing permitted tool paths flow only during that worker state.

Budget/browser/setup/access/input waits do not animate as active work. Stopped, paused,
finished and draft agents have no live ring, even with a stale working runtime value.
Use one orbit rather than stacking sonar, face pulses, sweeping borders and card glow loops.
Decorative ready animation is limited to 24 agents plus the focused agent; static rings,
labels and borders remain for the rest. Working animation follows actual worker activity.

Reduced motion stops orbiting and retains distinct static arcs and labels. Motion does
not alter schedules, budgets, provider use, action permissions or worker execution.
Verify state transitions using isolated browser fixtures, plus unit tests for disabled,
paused, waiting, working and dense graphs. No test agents are created in real accounts.
