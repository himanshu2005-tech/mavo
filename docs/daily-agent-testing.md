# Daily-agent behavior checks

These checks create disposable Crew in separate SQLite databases on the project's
D: drive. They do **not** add examples to your real workspace, sign in to accounts,
send mail, change tasks/events, run terminal commands, or control your browser.
No credentials or raw model responses are written to the reports.

## Zero-cost regression checks

From the repository root in PowerShell:

```powershell
backend\.venv\Scripts\python.exe scripts\verify-automation-examples.py
backend\.venv\Scripts\python.exe scripts\check-daily-agents.py --simulate
```

The first command reads actual device counters and checks:

- C-drive free-space reporting, dismissal and delivery on the next check.
- Quiet computer-health monitoring.
- Daily reminders and duplicate suppression.
- Missing device permission blocking execution without model calls.

The second uses deterministic HTTP model responses, synthetic headlines and
synthetic Google snapshots, but the real Crew planner, permissions, first-run
verifier, worker runtime, task runner, database and notification queue. It checks:

- A news draft stays private until its read-only first run passes; its budget
  becomes measured calls plus three spare calls.
- A new headline creates one Crew-level desktop update; an unchanged review stays
  quiet and does not mutate the shared source cache.
- An agenda worker receives both its authorized calendar and tasks.
- Inbox triage receives authorized email bodies, reports an academic deadline,
  excludes a promotion and performs no action from an embedded malicious instruction.

Simulation checks application wiring, not the intelligence or reliability of a real
model. The notification checks verify durable queue entries, **not** native popup
appearance. Creation uses prepared, schema-validated proposals to avoid extra
planner calls; this is not a natural-language Core planning evaluation.

## Optional billed AIRouter check

Explicit opt-in is required:

```powershell
backend\.venv\Scripts\python.exe scripts\check-daily-agents.py --live-airouter --max-calls 6
```

The script reads the first saved AIRouter route and decrypts its credential through
Windows DPAPI as your Windows user. The original database is opened read-only;
the key is copied into an in-memory credential store in the disposable workspace.
The saved route, Crew and connected accounts are never changed.

Only inference POSTs to `https://api.airouter.in/v1/chat/completions` are allowed by
the test transport. Every HTTP attempt—including malformed-response repairs and
failed responses—counts against one shared ceiling. Network-level retries are
disabled. Five requests are normally needed for the four checks, with at most one
extra request under the default six-request ceiling. A worker can consume the
remaining allowance without completing, in which case later checks fail rather
than silently spending more. Reported token counts are usage, not a dollar estimate.

Results:

- `.build/daily-agents-simulated.json`: zero-cost replay, clearly labelled.
- `.build/daily-agents-verification.json`: live AIRouter evaluation.

Reports contain pass/fail outcomes, bounded failure reasons, request counts and
safe checkpoint diagnostics (states/counts, not raw answers). A failed behavioral
assertion exits nonzero, even if the first-run protocol itself returned success.
The two modes never overwrite each other's report.

## Initial live evaluation — 9 November 2026

The saved `alibaba/qwen3.7-flash` route used **six requests**, with no real account
reads or external writes. It did not satisfy the fixtures:

- News produced a quiet first-run result instead of the supplied headline.
- Its subsequent review did not create the expected desktop update.
- Agenda exhausted its three-call worker allowance before a final answer.
- Inbox returned without the expected academic-deadline report.

The same application paths passed deterministic HTTP fixtures. This distinguishes
successful wiring from unreliable live-model behavior; it is **not** evidence
that live news, calendar or inbox monitoring works reliably yet.

Follow-up corrections clarify that assigned workers finish their current task
instead of waiting for the next schedule, and put the notification rule after the
JSON schema for scheduled Crew adapters as well as legacy workers. Regression
tests cover both prompt boundaries. No additional billed call was made after the
six-request ceiling, so the revised prompts still need a bounded live recheck.

## Automated fault coverage

`backend/tests/test_daily_agent_smoke.py` verifies the zero-network harness,
explicit opt-in, cleanup and shared request cap. Existing automation, first-run,
worker and source-evidence tests cover permission revocation during work,
stop/delete/pause fencing, restart recovery, unchanged baselines, bounded malformed
responses/timeouts, partial source failure, and prevention of fake successful work.
These fault tests use controlled fixtures; they do not disable your networking or
alter your actual drives, credentials or accounts.
