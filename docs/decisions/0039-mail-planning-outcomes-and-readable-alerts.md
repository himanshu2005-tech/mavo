# 0039 - Parent-owned mail alerts and verified planning outcomes

Status: accepted

## Observed gaps

A read-only member could classify important mail yet have no independent notification
capability, suppressing delivery despite its parent's saved desktop preference. A separate
Crew did queue an alert, but the nine-second one-line pill collapsed back to an unread dot.
The app being open was not a suppression rule. A later pickup-message copy omitted the
date/time present in the original; a planner could silently skip it or claim completion
without a meaningful scheduling outcome.

## Decision

Extend the existing typed mail checkpoints, TaskRunner and ReminderEvent outbox. No new
orchestration engine, account connection, schema migration or retroactive grant is introduced.

- The parent may deliver current-assignment important-mail decisions under its own desktop
  grant. A member still needs its restricted Gmail read/body access and the current account.
  Notified message IDs commit with outbox insertion. Effectful planners instead deliver
  verified changes or questions; no worker receives additional notification permissions.
- Actionable mail planning has a typed resolution: missing details with a correlated user
  question; planned with current action receipts; or already tracked with observed resource
  IDs. Failed/blocked work cannot mark the supplied mail seen. A missing-details checkpoint
  discards simultaneous action proposals rather than executing invented dates.
- Keep short source excerpts in authorized read-only reports, distinct from model summaries.
  Headers remain app-owned. Excerpts are untrusted data, not tool or policy instructions.
- New arrivals open the existing scrollable native card without taking keyboard focus or
  automatically folding to a dot. Queue rotation, fullscreen reading, mute, account gates,
  Missed and revocation behavior remain. The main window's visibility is irrelevant.
- Add a small Settings display-test control. It reopens a pending card or explicitly labelled
  local preview, never a fake successful Crew result. No model or Google operation is involved.

## Verification and limits

`test_mail_planning_outcomes.py` exercises durable questions, no writes for missing details,
receipt/duplicate claims and parent delivery without expanding member access. Existing
mail, setup-preview, permissions and outbox tests remain. Frontend tests cover the display
diagnostic; the headless desktop harness covers readable reports, scrolling, fullscreen and
queue behavior with mocked native IPC. Separately, the actual Windows app's Settings test
was invoked through UI Automation and its visible on-screen card inspected, with the main
app open. This live display check does not verify real Calendar/Tasks writes.

Existing consumed messages are not replayed or reclassified automatically. No real calendar
appointments, tasks, mail sends or contact changes are performed merely to validate this fix.
Model classification can still be wrong. The current planner's configured interval and
responsibility remain authoritative; this does not turn a mail-commitment watcher into a
different full-day optimization Crew or infer missing personal preferences.
